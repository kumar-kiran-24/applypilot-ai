"""Indeed SmartApply application-page detection.

Glassdoor Easy Apply sometimes hands the application off to Indeed SmartApply.
This module recognises that destination without trusting a URL merely because it
contains the word "indeed":

* the hostname must be exactly ``smartapply.indeed.com`` (case-insensitive),
  over https on the default port, with no credentials and no look-alike suffix;
* the application step is derived from the path (the observed flow starts at
  ``/beta/indeedapply/form/resume-selection-module/resume-selection``);
* page signals are read from already-fetched HTML only - nothing is clicked,
  submitted, scraped from a protected page or sent to Indeed.

``inspect`` never raises for an unfamiliar step: it reports ``UNKNOWN`` and the
caller decides whether to continue with manual inspection. Every helper here is
pure, so the whole module is unit-testable without a browser.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from urllib.parse import urlsplit

SMARTAPPLY_HOST = "smartapply.indeed.com"
SMARTAPPLY_ORIGIN = f"https://{SMARTAPPLY_HOST}"

#: Exact Indeed hosts used for interactive sign-in. They are allow-listed for
#: the live session so the user can authenticate manually, but they are NEVER
#: treated as an application destination or trusted beyond these host names.
AUTHENTICATION_HOSTS: frozenset[str] = frozenset(
    {"secure.indeed.com", "www.indeed.com"}
)
_AUTH_PATH = re.compile(
    r"(login|log-in|signin|sign-in|account|auth|challenge|verification)",
    re.IGNORECASE,
)

_RESUME_SELECTION_PATH = re.compile(r"resume[-_]?selection", re.IGNORECASE)
_RESUME_UPLOAD_PATH = re.compile(r"(upload|attach)[-_]?resume|resume[-_]?upload", re.I)
_QUESTIONS_PATH = re.compile(r"(questions?|questionnaire|application[-_]?questions)", re.I)
_REVIEW_PATH = re.compile(r"\breview\b|review-module", re.I)

_RESUME_SELECTION_SIGNALS = re.compile(
    r"resume[-_\s]?selection|choose\s+(a\s+|your\s+)?resume|"
    r"select\s+(a\s+|your\s+)?resume|resume[-_\s]?card",
    re.IGNORECASE,
)
_RESUME_UPLOAD_SIGNALS = re.compile(
    r"upload\s+(a\s+|your\s+)?resume|attach\s+(a\s+|your\s+)?resume|"
    r"<input[^>]+type=[\"']file[\"']",
    re.IGNORECASE,
)
_AUTH_SIGNALS = re.compile(
    r"sign\s+in|log\s+in|create\s+(an\s+)?account|password", re.IGNORECASE
)
_CAPTCHA_SIGNALS = re.compile(
    r"captcha|recaptcha|hcaptcha|turnstile|human\s+verification", re.IGNORECASE
)
_OTP_SIGNALS = re.compile(
    r"one[-_\s]?time\s+(code|password)|verification\s+code|enter\s+the\s+code",
    re.IGNORECASE,
)


class SmartApplyStep(str, Enum):
    """Steps ApplyPilot can name on an Indeed SmartApply flow."""

    RESUME_SELECTION = "resume_selection"
    RESUME_UPLOAD = "resume_upload"
    QUESTIONS = "questions"
    REVIEW = "review"
    UNKNOWN = "unknown"


#: Steps whose form structure ApplyPilot can analyse. Unknown steps are still
#: reported honestly; the user inspects them manually.
SUPPORTED_STEPS: frozenset[SmartApplyStep] = frozenset(
    {
        SmartApplyStep.RESUME_SELECTION,
        SmartApplyStep.RESUME_UPLOAD,
        SmartApplyStep.QUESTIONS,
        SmartApplyStep.REVIEW,
    }
)

STEP_LABELS: dict[SmartApplyStep, str] = {
    SmartApplyStep.RESUME_SELECTION: "Resume selection",
    SmartApplyStep.RESUME_UPLOAD: "Resume upload",
    SmartApplyStep.QUESTIONS: "Application questions",
    SmartApplyStep.REVIEW: "Application review",
    SmartApplyStep.UNKNOWN: "Unrecognised SmartApply step",
}

#: Human-readable reasons a SmartApply destination may be refused.
UNSUPPORTED_HOST_MESSAGE = (
    "That destination is not the supported Indeed SmartApply host "
    "(smartapply.indeed.com). ApplyPilot did not store it."
)


def _validated_parts(url: str) -> tuple[str, str] | None:
    """Return ``(host, path)`` for a strict SmartApply URL, else ``None``."""
    try:
        parts = urlsplit((url or "").strip())
        port = parts.port
    except ValueError:
        return None
    if parts.scheme.lower() != "https":
        return None
    if parts.username is not None or parts.password is not None:
        return None
    if port not in (None, 443):
        return None
    host = (parts.hostname or "").lower()
    if not is_smartapply_host(host):
        return None
    return host, parts.path or "/"


def is_smartapply_host(host: str | None) -> bool:
    """True only for the exact observed SmartApply hostname.

    ``smartapply.indeed.com.evil.example``, ``notsmartapply.indeed.com`` and
    ``indeed.com`` are all refused: nothing here matches on a substring.
    """
    return (host or "").strip().lower().rstrip(".") == SMARTAPPLY_HOST


def is_smartapply_url(url: str | None) -> bool:
    """True for an https SmartApply page on the approved host and port."""
    return _validated_parts(url or "") is not None


def is_authentication_url(url: str | None) -> bool:
    """True for an interactive Indeed sign-in page on an exact auth host.

    The path must look like authentication; a normal `www.indeed.com/viewjob`
    listing is not treated as an auth step. Credentials are never read.
    """
    try:
        parts = urlsplit((url or "").strip())
        port = parts.port
    except ValueError:
        return False
    if parts.scheme.lower() != "https" or port not in (None, 443):
        return False
    if parts.username is not None or parts.password is not None:
        return False
    host = (parts.hostname or "").lower().rstrip(".")
    if host not in AUTHENTICATION_HOSTS and host != SMARTAPPLY_HOST:
        return False
    target = f"{parts.path} {parts.query}"
    return bool(_AUTH_PATH.search(target))


def application_step_for_url(url: str | None) -> SmartApplyStep:
    """Classify the SmartApply step from the URL path alone."""
    parts = _validated_parts(url or "")
    if parts is None:
        return SmartApplyStep.UNKNOWN
    path = parts[1].lower()
    if _RESUME_UPLOAD_PATH.search(path):
        return SmartApplyStep.RESUME_UPLOAD
    if _RESUME_SELECTION_PATH.search(path):
        return SmartApplyStep.RESUME_SELECTION
    if _QUESTIONS_PATH.search(path):
        return SmartApplyStep.QUESTIONS
    if _REVIEW_PATH.search(path):
        return SmartApplyStep.REVIEW
    return SmartApplyStep.UNKNOWN


@dataclass(frozen=True)
class SmartApplyPage:
    """What ApplyPilot could learn from one SmartApply page (metadata only)."""

    url: str
    host: str
    step: SmartApplyStep
    supported: bool
    resume_selection_detected: bool
    resume_upload_detected: bool
    authentication_detected: bool
    captcha_detected: bool
    otp_detected: bool
    notes: tuple[str, ...] = ()

    @property
    def step_label(self) -> str:
        return STEP_LABELS[self.step]

    @property
    def requires_manual_step(self) -> bool:
        return bool(
            self.authentication_detected or self.captcha_detected or self.otp_detected
        )


def inspect_page(url: str, html: str | None) -> SmartApplyPage | None:
    """Describe a SmartApply page from its URL and already-fetched HTML.

    Returns ``None`` when the URL is not a strict SmartApply destination, so a
    caller can report "unsupported destination" instead of pretending.
    """
    parts = _validated_parts(url)
    if parts is None:
        return None
    host, path = parts
    step = application_step_for_url(url)
    markup = html or ""
    resume_selection = bool(_RESUME_SELECTION_SIGNALS.search(markup))
    resume_upload = bool(_RESUME_UPLOAD_SIGNALS.search(markup))
    authentication = bool(_AUTH_SIGNALS.search(markup))
    captcha = bool(_CAPTCHA_SIGNALS.search(markup))
    otp = bool(_OTP_SIGNALS.search(markup))

    if step is SmartApplyStep.UNKNOWN and resume_selection:
        step = SmartApplyStep.RESUME_SELECTION
    if step is SmartApplyStep.UNKNOWN and resume_upload:
        step = SmartApplyStep.RESUME_UPLOAD

    notes: list[str] = []
    if step is SmartApplyStep.RESUME_SELECTION:
        notes.append(
            "This is Indeed SmartApply's resume-selection step. Selecting or "
            "uploading a resume stays under your control; ApplyPilot only reads "
            "the visible labels."
        )
    if authentication:
        notes.append(
            "The page shows a sign-in or account step. ApplyPilot never enters "
            "passwords, one-time codes or MFA details."
        )
    if captcha:
        notes.append(
            "A CAPTCHA was detected. ApplyPilot never solves or bypasses it."
        )
    if otp:
        notes.append(
            "A verification-code step was detected. Complete it yourself."
        )
    if step not in SUPPORTED_STEPS:
        notes.append(
            "This SmartApply step is not recognised yet. Use the live browser to "
            "review it manually; ApplyPilot will not guess at its controls."
        )

    return SmartApplyPage(
        url=f"{SMARTAPPLY_ORIGIN}{path}",
        host=host,
        step=step,
        supported=step in SUPPORTED_STEPS,
        resume_selection_detected=resume_selection,
        resume_upload_detected=resume_upload,
        authentication_detected=authentication,
        captcha_detected=captcha,
        otp_detected=otp,
        notes=tuple(notes),
    )


__all__ = [
    "AUTHENTICATION_HOSTS",
    "SMARTAPPLY_HOST",
    "SMARTAPPLY_ORIGIN",
    "STEP_LABELS",
    "SUPPORTED_STEPS",
    "UNSUPPORTED_HOST_MESSAGE",
    "SmartApplyPage",
    "SmartApplyStep",
    "application_step_for_url",
    "inspect_page",
    "is_authentication_url",
    "is_smartapply_host",
    "is_smartapply_url",
]
