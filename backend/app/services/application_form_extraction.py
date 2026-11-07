"""DOM extraction for Phase 2E Stage B.

Extraction is intentionally separate from classification. It reads the current
serialized HTML through a deliberately narrow page interface and returns plain
``RawFormField`` records. It never executes employer JavaScript, never clicks
anything and never reads password, OTP/MFA, CAPTCHA or hidden security values.

Labels are resolved in this order: ``aria-labelledby``, ``aria-label``, an
associated ``<label for>``, a wrapping label, the fieldset legend, placeholder,
nearby descriptive text, and finally a humanised field name. Unstable CSS
selectors and screen position are never used to identify a field.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Protocol

from bs4 import BeautifulSoup
from bs4.element import Tag

SECRET_NAME = re.compile(
    r"(?:csrf|authenticity|xsrf|nonce|__requestverificationtoken|access[_-]?token|"
    r"secret|password|passwd|passcode|one[_-]?time|otp|mfa|2fa|captcha|"
    r"verification[_-]?code|security[_-]?code|auth[_-]?code)",
    re.IGNORECASE,
)
SECRET_VALUE_NAME = re.compile(
    r"(?:password|passwd|passcode|one[_-]?time|otp|mfa|2fa|captcha|"
    r"verification[_-]?code|security[_-]?code|auth[_-]?code|token|secret)",
    re.IGNORECASE,
)
TOKEN_HIDDEN = re.compile(
    r"(?:csrf|authenticity|xsrf|nonce|__requestverificationtoken|token|secret|"
    r"captcha|g-recaptcha-response|h-captcha-response|cf-turnstile-response)",
    re.IGNORECASE,
)
CAPTCHA_MARKER = re.compile(
    r"(?:captcha|recaptcha|hcaptcha|turnstile|robot[_-]?check|human[_-]?verification)",
    re.IGNORECASE,
)
LABEL_CLASS = re.compile(r"(?:label|question|field[-_]?name|form[-_]?label)", re.IGNORECASE)
HELP_CLASS = re.compile(r"(?:help|hint|description|form[-_]?text|instruction)", re.IGNORECASE)

SKIPPED_INPUT_TYPES = frozenset(
    {"submit", "button", "reset", "image", "range", "color", "week", "time"}
)


class FormPageContent(Protocol):
    """Controlled page interface: read-only serialized HTML, nothing else."""

    async def content(self) -> str:
        ...


@dataclass(frozen=True)
class StaticPageContent:
    """Test/adapter-friendly content provider for synthetic HTML fixtures."""

    html: str

    async def content(self) -> str:
        return self.html


@dataclass(frozen=True)
class RawFieldOption:
    label: str
    value: str | None = None


@dataclass
class RawFormField:
    form_index: int
    field_type: str
    label: str
    name: str | None = None
    html_id: str | None = None
    placeholder: str | None = None
    help_text: str | None = None
    required: bool = False
    options: list[RawFieldOption] = field(default_factory=list)
    current_value: str | None = None
    visible: bool = True
    multiple: bool = False
    order: int = 0


@dataclass(frozen=True)
class RawFormSnapshot:
    fields: tuple[RawFormField, ...]
    form_count: int


def _clean(value: str | None, *, limit: int = 500) -> str | None:
    if value is None:
        return None
    cleaned = re.sub(r"\s+", " ", value).strip()
    if not cleaned:
        return None
    return cleaned[:limit]


def _text(element: Tag | None, *, limit: int = 500) -> str | None:
    if element is None:
        return None
    return _clean(element.get_text(" ", strip=True), limit=limit)


def _humanize(value: str | None) -> str | None:
    cleaned = _clean(value, limit=200)
    if not cleaned:
        return None
    cleaned = re.sub(r"[_\-.:\[\]]+", " ", cleaned)
    cleaned = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", cleaned)
    return _clean(cleaned, limit=200)


def _attr(element: Tag, name: str) -> str | None:
    value = element.get(name)
    if isinstance(value, list):
        value = " ".join(str(item) for item in value)
    return _clean(value, limit=500) if value is not None else None


def _ids(element: Tag, name: str) -> list[str]:
    value = _attr(element, name)
    return [item for item in (value or "").split() if item][:10]


def _class_text(element: Tag) -> str:
    classes = element.get("class") or []
    return " ".join(str(item) for item in classes)


def _hidden(element: Tag) -> bool:
    for parent in [element, *element.parents]:
        if not isinstance(parent, Tag):
            continue
        if parent.has_attr("hidden"):
            return True
        if (parent.get("aria-hidden") or "").strip().casefold() == "true":
            return True
        style = (parent.get("style") or "").replace(" ", "").casefold()
        if "display:none" in style or "visibility:hidden" in style:
            return True
        if parent.name in {"form", "body", "html"}:
            break
    return False


def _required(element: Tag) -> bool:
    if element.has_attr("required"):
        return True
    if (element.get("aria-required") or "").strip().casefold() == "true":
        return True
    tokens = [token.casefold() for token in (element.get("class") or [])]
    return any(
        token in {"required", "is-required", "form-required"}
        or (token.endswith("-required") and not token.startswith("not-"))
        for token in tokens
    )


def _find_label(soup: BeautifulSoup, control: Tag) -> str | None:
    labelled_by = _ids(control, "aria-labelledby")
    if labelled_by:
        parts = [_text(soup.find(id=item)) for item in labelled_by]
        joined = _clean(" ".join(part for part in parts if part))
        if joined:
            return joined

    aria_label = _attr(control, "aria-label")
    if aria_label:
        return aria_label

    control_id = _attr(control, "id")
    if control_id:
        label = soup.find("label", attrs={"for": control_id})
        text = _text(label)
        if text:
            return text

    ancestor = control.find_parent("label")
    text = _text(ancestor)
    if text:
        return text

    fieldset = control.find_parent("fieldset")
    if fieldset is not None:
        legend = fieldset.find("legend", recursive=False)
        text = _text(legend)
        if text:
            return text

    placeholder = _attr(control, "placeholder")
    if placeholder:
        return placeholder

    nearby = _nearby_text(control)
    if nearby:
        return nearby

    return _humanize(control_id or _attr(control, "name"))


def _nearby_text(control: Tag) -> str | None:
    """Read a short preceding label-like text, never a whole container."""

    def candidate(node: object) -> str | None:
        if not isinstance(node, Tag):
            return None
        if node.name in {"input", "select", "textarea", "button", "form"}:
            return None
        if node.find(["input", "select", "textarea"]):
            return None
        if len(node.get_text(strip=True)) > 250:
            return None
        classes = _class_text(node)
        if LABEL_CLASS.search(classes) or node.name in {"label", "legend", "strong"}:
            return _text(node, limit=250)
        return None

    siblings = list(control.previous_siblings)
    for sibling in reversed(siblings[-3:]):
        found = candidate(sibling)
        if found:
            return found

    parent = control.parent
    if isinstance(parent, Tag):
        for sibling in reversed(list(parent.previous_siblings)[-2:]):
            found = candidate(sibling)
            if found:
                return found
    return None


def _help_text(soup: BeautifulSoup, control: Tag) -> str | None:
    described_by = _ids(control, "aria-describedby")
    if described_by:
        parts = [_text(soup.find(id=item), limit=500) for item in described_by]
        joined = _clean(" ".join(part for part in parts if part))
        if joined:
            return joined

    title = _attr(control, "title")
    if title:
        return title

    for sibling in control.next_siblings:
        if not isinstance(sibling, Tag):
            continue
        classes = _class_text(sibling)
        if HELP_CLASS.search(classes) or sibling.name in {"small"}:
            text = _text(sibling, limit=500)
            if text:
                return text
    parent = control.parent
    if isinstance(parent, Tag):
        for sibling in parent.next_siblings:
            if not isinstance(sibling, Tag):
                continue
            if HELP_CLASS.search(_class_text(sibling)):
                return _text(sibling, limit=500)
    return None


def _option_label(option: Tag) -> str | None:
    option_id = _attr(option, "id")
    if option_id:
        parent = option.find_parent()
        label = parent.find("label", attrs={"for": option_id}) if parent else None
        text = _text(label, limit=250)
        if text:
            return text
    ancestor = option.find_parent("label")
    text = _text(ancestor, limit=250)
    if text:
        return text
    for attribute in ("aria-label", "title", "value"):
        value = _attr(option, attribute)
        if value:
            return _humanize(value)
    return None


def _group_label(inputs: list[Tag], soup: BeautifulSoup) -> str | None:
    first = inputs[0]
    for element in [first, *first.parents]:
        if not isinstance(element, Tag):
            continue
        role = (element.get("role") or "").casefold()
        if role in {"group", "radiogroup"} or element.name == "fieldset":
            labelled_by = _ids(element, "aria-labelledby")
            if labelled_by:
                joined = _clean(
                    " ".join(
                        text
                        for text in (_text(soup.find(id=item)) for item in labelled_by)
                        if text
                    )
                )
                if joined:
                    return joined
            aria_label = _attr(element, "aria-label")
            if aria_label:
                return aria_label
            if element.name == "fieldset":
                legend = element.find("legend", recursive=False)
                text = _text(legend)
                if text:
                    return text
            if role in {"group", "radiogroup"}:
                nearby = _nearby_text(element)
                if nearby:
                    return nearby
        if element.name in {"form", "body"}:
            break
    return _humanize(_attr(first, "name"))


def _group_help(inputs: list[Tag], soup: BeautifulSoup) -> str | None:
    for element in [inputs[0], *inputs[0].parents]:
        if not isinstance(element, Tag):
            continue
        described_by = _ids(element, "aria-describedby")
        if described_by:
            joined = _clean(
                " ".join(
                    text
                    for text in (_text(soup.find(id=item), limit=500) for item in described_by)
                    if text
                )
            )
            if joined:
                return joined
        if element.name in {"form", "body"}:
            break
    return _help_text(soup, inputs[0])


def _input_type(control: Tag) -> str:
    if control.name == "textarea":
        return "textarea"
    if control.name == "select":
        return "select"
    return (control.get("type") or "text").strip().casefold()


def _field_type(input_type: str) -> str:
    return {
        "email": "email",
        "tel": "phone",
        "number": "number",
        "date": "date",
        "datetime-local": "date",
        "month": "date",
        "url": "url",
        "textarea": "textarea",
        "select": "select",
        "file": "file",
        "password": "password",
        "hidden": "hidden",
        "checkbox": "checkbox",
        "radio": "radio",
    }.get(input_type, "text")


def _is_secret_value(input_type: str, name: str | None, html_id: str | None) -> bool:
    if input_type in {"password", "file", "hidden"}:
        return True
    joined = f"{name or ''} {html_id or ''}"
    return bool(SECRET_VALUE_NAME.search(joined))


def _option_value(option: Tag) -> str | None:
    value = _attr(option, "value")
    return value


def _current_value(
    control: Tag, input_type: str, name: str | None, html_id: str | None
) -> str | None:
    if _is_secret_value(input_type, name, html_id):
        return None
    if input_type in {"checkbox", "radio"}:
        return "checked" if control.has_attr("checked") else None
    if control.name == "textarea":
        return _text(control, limit=2000)
    if control.name == "select":
        selected = [
            text
            for option in control.find_all("option")
            if option.has_attr("selected")
            for text in [_text(option, limit=250) or _option_value(option)]
            if text
        ]
        return ", ".join(selected)[:500] if selected else None
    value = _attr(control, "value")
    if value is None:
        return None
    return value[:500]


def _captcha_elements(soup: BeautifulSoup) -> list[Tag]:
    found: list[Tag] = []
    seen: set[int] = set()

    def add(element: Tag) -> None:
        if id(element) not in seen:
            seen.add(id(element))
            found.append(element)

    for element in soup.find_all(attrs={"data-sitekey": True}):
        add(element)
    for element in soup.find_all(["div", "section", "span", "iframe"]):
        marker = " ".join(
            [
                _attr(element, "id") or "",
                _attr(element, "name") or "",
                _class_text(element),
                _attr(element, "src") or "",
                _attr(element, "title") or "",
            ]
        )
        if CAPTCHA_MARKER.search(marker):
            add(element)
    return found


def _captcha_field(element: Tag, order: int) -> RawFormField:
    label = (
        _attr(element, "aria-label")
        or _attr(element, "title")
        or _humanize(_attr(element, "id") or _attr(element, "name"))
        or "CAPTCHA challenge"
    )
    if not CAPTCHA_MARKER.search(label):
        label = f"CAPTCHA: {label}"
    return RawFormField(
        form_index=-1,
        field_type="captcha",
        label=_clean(label, limit=250) or "CAPTCHA challenge",
        name=_attr(element, "name") or _attr(element, "id"),
        html_id=_attr(element, "id"),
        placeholder=None,
        help_text="Complete this challenge yourself; ApplyPilot never solves it.",
        required=True,
        current_value=None,
        visible=not _hidden(element),
        order=order,
    )


def extract_form_snapshot(html: str) -> RawFormSnapshot:
    """Extract controls and labels from one serialized page."""

    soup = BeautifulSoup(html or "", "lxml")
    forms = soup.find_all("form")
    form_indexes = {id(form): index for index, form in enumerate(forms)}
    page_index = len(forms) if forms else 0

    def form_index_for(control: Tag) -> int:
        form = control.find_parent("form")
        return form_indexes.get(id(form), page_index)

    order = {
        id(control): position
        for position, control in enumerate(soup.find_all(["input", "textarea", "select"]))
    }
    controls = [
        control
        for control in soup.find_all(["input", "textarea", "select"])
        if not (
            control.name == "input"
            and _input_type(control) in SKIPPED_INPUT_TYPES
        )
        and not (
            control.name == "input"
            and TOKEN_HIDDEN.search(
                f"{_attr(control, 'name') or ''} {_attr(control, 'id') or ''}"
            )
        )
    ]

    grouped: dict[tuple[int, str], list[Tag]] = {}
    singles: list[tuple[Tag, str]] = []
    for control in controls:
        input_type = _input_type(control)
        name = _attr(control, "name")
        if input_type in {"radio", "checkbox"} and name:
            grouped.setdefault((form_index_for(control), name), []).append(control)
        else:
            singles.append((control, input_type))

    fields: list[RawFormField] = []
    for (form_index, name), group in grouped.items():
        input_type = _input_type(group[0])
        if input_type == "checkbox" and len(group) == 1:
            singles.append((group[0], input_type))
            continue
        options = [
            RawFieldOption(
                label=_option_label(option) or _humanize(_option_value(option)) or "Option",
                value=_option_value(option),
            )
            for option in group
        ]
        selected = [
            option_data.label
            for option, option_data in zip(group, options, strict=False)
            if option.has_attr("checked")
        ]
        fields.append(
            RawFormField(
                form_index=form_index,
                field_type="radio_group" if input_type == "radio" else "checkbox_group",
                label=_group_label(group, soup) or _humanize(name) or "Choice group",
                name=name,
                html_id=_attr(group[0], "id"),
                help_text=_group_help(group, soup),
                required=any(_required(item) for item in group),
                options=options,
                current_value=", ".join(selected)[:500] if selected else None,
                visible=any(not _hidden(item) for item in group),
                multiple=input_type == "checkbox",
                order=min(order[id(item)] for item in group),
            )
        )

    for control, input_type in singles:
        name = _attr(control, "name")
        html_id = _attr(control, "id")
        options: list[RawFieldOption] = []
        if control.name == "select":
            options = [
                RawFieldOption(
                    label=_text(option, limit=250) or _option_value(option) or "Option",
                    value=_option_value(option),
                )
                for option in control.find_all("option")
                if _text(option, limit=250) or _option_value(option)
            ]
        fields.append(
            RawFormField(
                form_index=form_index_for(control),
                field_type=_field_type(input_type),
                label=_find_label(soup, control) or "Unlabelled field",
                name=name,
                html_id=html_id,
                placeholder=_attr(control, "placeholder"),
                help_text=_help_text(soup, control),
                required=_required(control),
                options=options,
                current_value=_current_value(control, input_type, name, html_id),
                visible=not _hidden(control),
                multiple=control.has_attr("multiple"),
                order=order[id(control)],
            )
        )

    captcha = _captcha_elements(soup)
    for index, element in enumerate(captcha):
        fields.append(_captcha_field(element, order.get(id(element), 10_000 + index)))

    fields.sort(key=lambda item: item.order)

    form_count = len(forms) if forms else 1
    if forms and any(item.form_index == page_index for item in fields):
        form_count += 1
    return RawFormSnapshot(fields=tuple(fields), form_count=form_count)


__all__ = [
    "FormPageContent",
    "RawFieldOption",
    "RawFormField",
    "RawFormSnapshot",
    "StaticPageContent",
    "extract_form_snapshot",
]
