"""Phase 2J.1: direct job import, manual destination and session recovery.

No browser is launched and no network call is made. A fake persistent browser
and a controlled DNS resolver keep the tests deterministic while still
exercising the real validation, dedupe and recovery code paths.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
import pytest_asyncio
from app.core.config import settings
from app.models.candidate import Candidate
from app.models.user import User
from app.schemas.application_fill import FillFieldStatus
from app.schemas.application_form import (
    DetectedFormField,
    FieldCategory,
    FieldSource,
    FieldSourceMatch,
    FormFieldType,
)
from app.services import application_url_security
from app.services.application_field_planner import FieldExecutionPlanner
from app.services.browser_session_manager import (
    BrowserSessionManager,
    set_browser_session_manager,
)
from app.services.job_import import _title_from_url, import_job_url
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.test_applications import (
    bearer,
    create_job,
    create_resume,
    create_user,
    prepare_profile,
)

SessionFactory = async_sessionmaker[AsyncSession]

JOBS = "/api/v1/jobs"
APPLICATIONS = "/api/v1/applications"
BROWSER = "/api/v1/browser-sessions"

GLASSDOOR = (
    "https://www.glassdoor.com/job-listing/senior-ai-engineer-JV_IC1_KO2,7.htm"
    "?jl=100987654321&pos=101&src=alert"
)
GLASSDOOR_SAME_JOB = (
    "https://www.glassdoor.com/job-listing/senior-ai-engineer-JV_IC1_KO2,7.htm"
    "?jl=100987654321&utm_source=email"
)
GLASSDOOR_OTHER = (
    "https://www.glassdoor.com/job-listing/backend-engineer-JV_IC9_KO2,7.htm"
    "?jl=100111222333"
)
GLASSDOOR_LOOKALIKE = "https://glassdoor.com.evil.com/job-listing/engineer-JV_1.htm"
GLASSDOOR_NON_LISTING = "https://www.glassdoor.com/Overview/Working-at-Acme-EI_IE1.htm"
LINKEDIN = "https://www.linkedin.com/jobs/view/123456"
GREENHOUSE = "https://boards.greenhouse.io/acme/jobs/123"
PUBLIC_ADDRESS = "93.184.216.34"
PRIVATE_ADDRESS = "10.0.0.7"


class FakeHandle:
    def __init__(self, url: str) -> None:
        self.url = url
        self.alive = True
        self.closed = False
        self.unsafe_navigation = False
        self.blocked_navigation = False

    async def current_url(self) -> str:
        return self.url

    async def content(self) -> str:
        return "<html><body>form</body></html>"

    async def is_alive(self) -> bool:
        return self.alive and not self.closed

    async def close(self) -> None:
        self.closed = True
        self.alive = False


class FakeDriver:
    def __init__(self) -> None:
        self.opened: list[str] = []
        self.handles: list[FakeHandle] = []

    async def open(
        self,
        url: str,
        *,
        policy: Any,
        user_id: str,
        pinned_address: str | None = None,
    ) -> FakeHandle:
        del policy, user_id, pinned_address
        handle = FakeHandle(url)
        self.opened.append(url)
        self.handles.append(handle)
        return handle


async def _public_resolver(host: str, port: int) -> list[str]:
    del host, port
    return [PUBLIC_ADDRESS]


async def _private_resolver(host: str, port: int) -> list[str]:
    del host, port
    return [PRIVATE_ADDRESS]


@pytest.fixture(autouse=True)
def automation_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "APPLICATION_AUTOMATION_ENABLED", False)


@pytest.fixture(autouse=True)
def public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(application_url_security, "_resolve_host", _public_resolver)


@pytest_asyncio.fixture
async def browser_runtime(monkeypatch: pytest.MonkeyPatch):
    driver = FakeDriver()
    manager = BrowserSessionManager(driver=driver, ttl_seconds=900)
    set_browser_session_manager(manager)
    yield SimpleNamespace(driver=driver, manager=manager)
    await manager.close_all()
    set_browser_session_manager(None)


async def _import(client: AsyncClient, user: User, url: str):
    return await client.post(
        f"{JOBS}/import", json={"url": url}, headers=bearer(user)
    )


async def _prepare_application(
    client: AsyncClient,
    session_factory: SessionFactory,
    *,
    email: str = "import@example.com",
    google_sub: str = "google-import",
    job_url: str = GLASSDOOR,
) -> tuple[User, Any, dict[str, Any]]:
    user = await create_user(session_factory, email=email, google_sub=google_sub)
    job = await create_job(session_factory, user, job_url=job_url)
    await create_resume(session_factory, user)
    await prepare_profile(client, user)
    response = await client.post(
        f"{APPLICATIONS}/analyze",
        json={"job_id": str(job.id)},
        headers=bearer(user),
    )
    assert response.status_code == 200, response.text
    return user, job, response.json()


# ---------------------------------------------------------------------------
# 1. URL import: validation, extraction, dedupe and ownership
# ---------------------------------------------------------------------------
async def test_import_creates_a_job_and_labels_derived_details(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory, email="importer@example.com", google_sub="g-imp")

    response = await _import(client, user, GLASSDOOR)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["created"] is True
    assert body["reused"] is False
    assert body["extraction"]["source"] == "glassdoor"
    assert body["extraction"]["title_source"] == "url_slug"
    assert body["extraction"]["description_available"] is False
    assert "company" in body["extraction"]["missing_fields"]
    assert "description" in body["extraction"]["missing_fields"]
    # A derived title is honest: it is readable, not invented employer metadata.
    assert body["job"]["job_title"].lower().startswith("senior ai engineer")
    assert body["job"]["company"] is None
    assert body["job"]["location"] is None
    assert body["job"]["job_description"] is None
    assert body["job"]["source"] == "glassdoor"
    # The canonical URL keeps the Glassdoor id but drops tracking parameters.
    assert "jl=100987654321" in body["job"]["job_url"]
    assert "utm_source" not in body["job"]["job_url"]

    fetched = await client.get(f"{JOBS}/{body['job']['id']}", headers=bearer(user))
    assert fetched.status_code == 200, fetched.text


async def test_repeated_import_reuses_one_record(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory, email="dedupe@example.com", google_sub="g-dedupe")

    first = await _import(client, user, GLASSDOOR)
    assert first.status_code == 200, first.text
    second = await _import(client, user, GLASSDOOR_SAME_JOB)
    assert second.status_code == 200, second.text

    assert second.json()["created"] is False
    assert second.json()["reused"] is True
    assert second.json()["job"]["id"] == first.json()["job"]["id"]
    assert second.json()["extraction"]["title_source"] == "stored_record"

    listing = await client.get(JOBS, headers=bearer(user))
    assert listing.status_code == 200, listing.text
    assert listing.json()["total"] == 1


async def test_import_rejects_unsupported_and_unsafe_urls(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory, email="reject@example.com", google_sub="g-reject")

    lookalike = await _import(client, user, GLASSDOOR_LOOKALIKE)
    assert lookalike.status_code == 400
    assert lookalike.json()["code"] == "unsupported_job_listing_domain"

    linkedin = await _import(client, user, LINKEDIN)
    assert linkedin.status_code == 400
    assert linkedin.json()["code"] == "unsupported_job_listing_source"

    non_listing = await _import(client, user, GLASSDOOR_NON_LISTING)
    assert non_listing.status_code == 400
    assert non_listing.json()["code"] in {
        "unsupported_job_listing_domain",
        "unsupported_job_url_shape",
    }

    private_ip = await _import(
        client, user, "http://127.0.0.1/job-listing/engineer-JV_1.htm"
    )
    assert private_ip.status_code == 400

    credentials = await _import(
        client, user, "https://user:pass@www.glassdoor.com/job-listing/x-JV_1.htm"
    )
    assert credentials.status_code == 400


async def test_import_refuses_a_private_dns_resolution(
    client: AsyncClient,
    session_factory: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = await create_user(session_factory, email="dns@example.com", google_sub="g-dns")
    monkeypatch.setattr(application_url_security, "_resolve_host", _private_resolver)

    response = await _import(client, user, GLASSDOOR)
    assert response.status_code == 400
    assert response.json()["code"] == "unsafe_application_host"


async def test_import_is_scoped_to_the_authenticated_user(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    owner = await create_user(session_factory, email="one@example.com", google_sub="g-one")
    other = await create_user(session_factory, email="two@example.com", google_sub="g-two")

    first = await _import(client, owner, GLASSDOOR)
    second = await _import(client, other, GLASSDOOR)
    assert first.status_code == second.status_code == 200
    assert first.json()["job"]["id"] != second.json()["job"]["id"]

    # The other account cannot read the first account's posting.
    forbidden = await client.get(
        f"{JOBS}/{first.json()['job']['id']}", headers=bearer(other)
    )
    assert forbidden.status_code == 404


async def test_import_requires_authentication(client: AsyncClient) -> None:
    response = await client.post(f"{JOBS}/import", json={"url": GLASSDOOR})
    assert response.status_code == 401


def test_title_derivation_never_claims_more_than_the_url() -> None:
    title, derived = _title_from_url(GLASSDOOR)
    assert derived is True
    assert title == "Senior Ai Engineer".replace("Ai", "AI")

    title, derived = _title_from_url("https://www.glassdoor.com/partner/jobListing.htm?jl=1")
    assert derived is False
    assert title == "Glassdoor job posting"


async def test_module_import_is_idempotent(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory, email="module@example.com", google_sub="g-mod")
    async with session_factory() as session:
        first = await import_job_url(session, user, GLASSDOOR)
    async with session_factory() as session:
        second = await import_job_url(session, user, GLASSDOOR)
    assert first.created is True
    assert second.created is False
    assert first.job.id == second.job.id


# ---------------------------------------------------------------------------
# 2. Manual destination: validation, preservation and session targeting
# ---------------------------------------------------------------------------
async def test_manual_destination_is_validated_and_preserves_the_listing(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user, _job, preview = await _prepare_application(client, session_factory)
    application_id = preview["application_id"]

    response = await client.post(
        f"{BROWSER}/by-application/{application_id}/destination",
        json={"destination_url": GREENHOUSE},
        headers=bearer(user),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "validated"
    assert body["destination_validated"] is True
    assert body["manual_destination"] is True
    assert body["destination_url"] == GREENHOUSE
    assert body["listing_url"] == GLASSDOOR
    assert body["application_platform"] == "greenhouse"

    saved = await client.get(f"{APPLICATIONS}/{application_id}", headers=bearer(user))
    assert saved.status_code == 200, saved.text
    payload = saved.json()
    assert payload["application_url"] == GLASSDOOR
    assert payload["job_listing_url"] == GLASSDOOR
    assert payload["application_destination_url"] == GREENHOUSE
    assert payload["destination_validated"] is True


async def test_manual_destination_rejects_job_boards_and_private_hosts(
    client: AsyncClient,
    session_factory: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, _job, preview = await _prepare_application(client, session_factory)
    application_id = preview["application_id"]

    board = await client.post(
        f"{BROWSER}/by-application/{application_id}/destination",
        json={"destination_url": GLASSDOOR_OTHER},
        headers=bearer(user),
    )
    assert board.status_code == 400
    assert board.json()["code"] == "job_board_url_not_supported"

    monkeypatch.setattr(application_url_security, "_resolve_host", _private_resolver)
    private = await client.post(
        f"{BROWSER}/by-application/{application_id}/destination",
        json={"destination_url": GREENHOUSE},
        headers=bearer(user),
    )
    assert private.status_code == 400
    assert private.json()["code"] == "unsafe_application_host"


async def test_new_session_opens_the_validated_manual_destination(
    client: AsyncClient,
    session_factory: SessionFactory,
    browser_runtime: Any,
) -> None:
    user, _job, preview = await _prepare_application(client, session_factory)
    application_id = preview["application_id"]

    stored = await client.post(
        f"{BROWSER}/by-application/{application_id}/destination",
        json={"destination_url": GREENHOUSE},
        headers=bearer(user),
    )
    assert stored.status_code == 200, stored.text

    started = await client.post(
        BROWSER,
        json={"application_id": application_id},
        headers=bearer(user),
    )
    assert started.status_code == 200, started.text
    assert started.json()["browser_status"] == "open"
    # The browser opens the validated form, never the board listing.
    assert browser_runtime.driver.opened == [GREENHOUSE]

    saved = await client.get(f"{APPLICATIONS}/{application_id}", headers=bearer(user))
    assert saved.json()["job_listing_url"] == GLASSDOOR


# ---------------------------------------------------------------------------
# 3. Browser-session recovery and duplicate prevention
# ---------------------------------------------------------------------------
async def _start(client: AsyncClient, user: User, application_id: str):
    return await client.post(
        BROWSER, json={"application_id": application_id}, headers=bearer(user)
    )


async def test_repeated_start_reuses_the_live_session(
    client: AsyncClient, session_factory: SessionFactory, browser_runtime: Any
) -> None:
    user, _job, preview = await _prepare_application(client, session_factory)
    application_id = preview["application_id"]

    first = await _start(client, user, application_id)
    second = await _start(client, user, application_id)
    assert first.status_code == second.status_code == 200
    assert first.json()["session_id"] == second.json()["session_id"]
    assert len(browser_runtime.driver.opened) == 1
    assert browser_runtime.manager.live_count() == 1


async def test_concurrent_starts_never_open_two_browsers(
    client: AsyncClient, session_factory: SessionFactory, browser_runtime: Any
) -> None:
    user, _job, preview = await _prepare_application(client, session_factory)
    application_id = preview["application_id"]

    responses = await asyncio.gather(
        _start(client, user, application_id),
        _start(client, user, application_id),
    )
    statuses = sorted(response.status_code for response in responses)
    assert 200 in statuses
    assert set(statuses) <= {200, 409}
    assert len(browser_runtime.driver.opened) == 1
    assert browser_runtime.manager.live_count() == 1


async def test_recovery_reconciles_an_expired_browser_before_blocking(
    client: AsyncClient, session_factory: SessionFactory, browser_runtime: Any
) -> None:
    user = await create_user(session_factory, email="recover@example.com", google_sub="g-rec")
    await prepare_profile(client, user)
    await create_resume(session_factory, user)
    job_one = await create_job(session_factory, user, job_url=GLASSDOOR)
    job_two = await create_job(session_factory, user, job_url=GLASSDOOR_OTHER)
    first_preview = await client.post(
        f"{APPLICATIONS}/analyze",
        json={"job_id": str(job_one.id)},
        headers=bearer(user),
    )
    second_preview = await client.post(
        f"{APPLICATIONS}/analyze",
        json={"job_id": str(job_two.id)},
        headers=bearer(user),
    )
    assert first_preview.status_code == second_preview.status_code == 200
    first_app = first_preview.json()["application_id"]
    second_app = second_preview.json()["application_id"]

    started = await _start(client, user, first_app)
    assert started.status_code == 200, started.text

    # The process browser died while the row still says "open".
    browser_runtime.driver.handles[0].alive = False

    recovered = await client.post(
        f"{BROWSER}/by-application/{second_app}/recover",
        headers=bearer(user),
    )
    assert recovered.status_code == 200, recovered.text
    body = recovered.json()
    assert body["recovered"] is True
    assert body["session"]["application_id"] == second_app
    assert body["blocked_by"] == []
    assert body["closed_session_ids"] == []

    # The dead row is reported honestly instead of claiming a live browser.
    stale = await client.get(
        f"{BROWSER}/by-application/{first_app}", headers=bearer(user)
    )
    assert stale.status_code == 200, stale.text
    assert stale.json()["browser_status"] == "expired"
    assert stale.json()["failure_code"] == "browser_session_expired"


async def test_recovery_names_a_live_blocker_and_force_is_explicit(
    client: AsyncClient, session_factory: SessionFactory, browser_runtime: Any
) -> None:
    user = await create_user(session_factory, email="blocked@example.com", google_sub="g-blk")
    await prepare_profile(client, user)
    await create_resume(session_factory, user)
    job_one = await create_job(session_factory, user, job_url=GLASSDOOR)
    job_two = await create_job(session_factory, user, job_url=GLASSDOOR_OTHER)
    first_preview = await client.post(
        f"{APPLICATIONS}/analyze",
        json={"job_id": str(job_one.id)},
        headers=bearer(user),
    )
    second_preview = await client.post(
        f"{APPLICATIONS}/analyze",
        json={"job_id": str(job_two.id)},
        headers=bearer(user),
    )
    first_app = first_preview.json()["application_id"]
    second_app = second_preview.json()["application_id"]

    started = await _start(client, user, first_app)
    assert started.status_code == 200, started.text
    first_session = started.json()["session_id"]

    blocked = await client.post(
        f"{BROWSER}/by-application/{second_app}/recover",
        headers=bearer(user),
    )
    assert blocked.status_code == 200, blocked.text
    body = blocked.json()
    assert body["recovered"] is False
    assert body["session"] is None
    assert len(body["blocked_by"]) == 1
    assert body["blocked_by"][0]["application_id"] == first_app
    assert body["blocked_by"][0]["session_id"] == first_session
    # Nothing was ended without permission.
    assert browser_runtime.manager.live_count() == 1

    forced = await client.post(
        f"{BROWSER}/by-application/{second_app}/recover",
        json={"force": True},
        headers=bearer(user),
    )
    assert forced.status_code == 200, forced.text
    forced_body = forced.json()
    assert forced_body["recovered"] is True
    assert forced_body["closed_session_ids"] == [first_session]
    assert forced_body["session"]["application_id"] == second_app
    assert browser_runtime.manager.live_count() == 1

    ended = await client.get(
        f"{BROWSER}/by-application/{first_app}", headers=bearer(user)
    )
    assert ended.json()["browser_status"] == "closed"
    assert ended.json()["failure_code"] == "closed_by_recovery"


# ---------------------------------------------------------------------------
# 4. Fill plan: verified values only, never legal/sensitive automation
# ---------------------------------------------------------------------------
def _field(
    *,
    field_id: str,
    label: str,
    category: FieldCategory,
    field_type: FormFieldType,
    source: FieldSource,
    value: str | None = None,
    current_value: str | None = None,
    required: bool = True,
    is_legal: bool = False,
) -> DetectedFormField:
    return DetectedFormField(
        field_id=field_id,
        form_index=0,
        label=label,
        field_type=field_type,
        classification=category,
        required=required,
        current_value=current_value,
        confidence=0.95,
        requires_human_input=False,
        requires_confirmation=False,
        is_sensitive=False,
        is_legal=is_legal,
        automation_eligible=True,
        classification_reason="Test fixture.",
        source=FieldSourceMatch(
            source=source,
            suggested_value=value,
            confidence=0.97 if source is FieldSource.VERIFIED_PROFILE else 0.6,
        ),
    )


def test_fill_plan_covers_common_fields_and_protects_human_decisions() -> None:
    candidate = Candidate(
        full_name="Ada Lovelace",
        email="ada@example.com",
        phone="+1 555 0100",
        address_line1="12 Analytical Way",
        skills=["Python", "FastAPI"],
    )
    plan = FieldExecutionPlanner().build(
        [
            _field(
                field_id="first",
                label="First name",
                category=FieldCategory.PERSONAL_NAME,
                field_type=FormFieldType.TEXT,
                source=FieldSource.VERIFIED_PROFILE,
                value="Ada",
            ),
            _field(
                field_id="email",
                label="Email address",
                category=FieldCategory.EMAIL,
                field_type=FormFieldType.EMAIL,
                source=FieldSource.VERIFIED_PROFILE,
                value="ada@example.com",
            ),
            _field(
                field_id="address",
                label="Street address",
                category=FieldCategory.ADDRESS,
                field_type=FormFieldType.TEXT,
                source=FieldSource.VERIFIED_PROFILE,
                value="12 Analytical Way",
            ),
            _field(
                field_id="skills",
                label="Technical skills",
                category=FieldCategory.SKILLS,
                field_type=FormFieldType.TEXTAREA,
                source=FieldSource.VERIFIED_PROFILE,
                value="Python, FastAPI",
            ),
            _field(
                field_id="education",
                label="Highest degree",
                category=FieldCategory.EDUCATION,
                field_type=FormFieldType.TEXT,
                source=FieldSource.NONE,
                required=True,
            ),
            _field(
                field_id="work_auth",
                label="Are you authorized to work in the US?",
                category=FieldCategory.WORK_AUTHORIZATION,
                field_type=FormFieldType.RADIO_GROUP,
                source=FieldSource.VERIFIED_PROFILE,
                value="Yes",
            ),
            _field(
                field_id="legal",
                label="I certify this information is true",
                category=FieldCategory.LEGAL_DECLARATION,
                field_type=FormFieldType.CHECKBOX,
                source=FieldSource.NONE,
                is_legal=True,
            ),
            _field(
                field_id="password",
                label="Account password",
                category=FieldCategory.UNKNOWN,
                field_type=FormFieldType.PASSWORD,
                source=FieldSource.NONE,
            ),
            _field(
                field_id="salary",
                label="Expected salary",
                category=FieldCategory.EXPECTED_SALARY,
                field_type=FormFieldType.NUMBER,
                source=FieldSource.NONE,
                required=True,
            ),
        ],
        candidate=candidate,
        resume=None,
        reusable_answers=[],
        user_answers={},
    )
    by_id = {item.field_id: item for item in plan.fields}
    assert by_id["first"].status is FillFieldStatus.READY
    assert by_id["email"].status is FillFieldStatus.READY
    assert by_id["address"].status is FillFieldStatus.READY
    assert by_id["skills"].status is FillFieldStatus.READY
    # No education evidence exists, so the field asks instead of assuming.
    assert by_id["education"].status is FillFieldStatus.NEEDS_HUMAN_INPUT
    assert by_id["education"].proposed_value is None
    assert by_id["work_auth"].status is FillFieldStatus.NEEDS_HUMAN_INPUT
    assert by_id["work_auth"].proposed_value is None
    assert by_id["legal"].status is FillFieldStatus.NEEDS_HUMAN_INPUT
    assert by_id["password"].status is FillFieldStatus.NEEDS_HUMAN_INPUT
    assert by_id["salary"].status is FillFieldStatus.NEEDS_HUMAN_INPUT
    # Only verified values are executable; nothing sensitive is proposed.
    assert plan.eligible_count == 4
    assert plan.executable is True


def test_fill_plan_never_overwrites_existing_values_or_guesses_missing_ones() -> None:
    candidate = Candidate(full_name="Ada Lovelace", email="ada@example.com")
    plan = FieldExecutionPlanner().build(
        [
            _field(
                field_id="email",
                label="Email",
                category=FieldCategory.EMAIL,
                field_type=FormFieldType.EMAIL,
                source=FieldSource.VERIFIED_PROFILE,
                value="ada@example.com",
                current_value="typed-by-user@example.com",
            ),
            _field(
                field_id="linkedin",
                label="LinkedIn profile",
                category=FieldCategory.LINKEDIN,
                field_type=FormFieldType.URL,
                source=FieldSource.NONE,
                required=False,
            ),
        ],
        candidate=candidate,
        resume=None,
        reusable_answers=[],
        user_answers={},
    )
    by_id = {item.field_id: item for item in plan.fields}
    assert by_id["email"].status is FillFieldStatus.ALREADY_FILLED
    assert by_id["email"].proposed_value is None
    assert by_id["linkedin"].status is FillFieldStatus.SKIPPED
    assert plan.eligible_count == 0


def test_no_submit_primitive_exists_on_the_recovery_session() -> None:
    # The recovery endpoint may start a browser, but there is intentionally no
    # submission action anywhere in the session surface.
    assert not hasattr(FakeHandle, "click")
    assert not hasattr(FakeHandle, "submit")
