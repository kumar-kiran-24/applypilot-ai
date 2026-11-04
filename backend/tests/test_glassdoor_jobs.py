"""Tests for Glassdoor email discovery and extraction."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from app.services.glassdoor_jobs import (
    GLASSDOOR_SENDER_QUERY,
    GlassdoorJob,
    canonicalize_job_url,
    extract_glassdoor_jobs,
    extract_glassdoor_jobs_from_messages,
    glassdoor_search_query,
    is_glassdoor_host,
    is_glassdoor_job_email,
    is_glassdoor_job_url,
    is_glassdoor_sender,
)
from app.services.gmail_service import GmailMessage

RECEIVED_AT = datetime(2026, 11, 2, 18, 0, tzinfo=timezone.utc)

ALERT_HTML = """
<html>
  <body>
    <p>New jobs that match your search</p>
    <table>
      <tr><td><h2>Software Engineer</h2></td></tr>
      <tr><td>Acme Corp</td></tr>
      <tr><td>Austin, TX</td></tr>
      <tr>
        <td>
          <a href="https://www.glassdoor.com/partner/jobListing.htm?pos=101&amp;jobListingId=1234567890&amp;src=alert">
            View job
          </a>
        </td>
      </tr>
    </table>
    <table>
      <tr><td><h2>Senior Backend Engineer</h2></td></tr>
      <tr><td>Globex</td></tr>
      <tr><td>Remote</td></tr>
      <tr>
        <td>
          <a href="https://www.glassdoor.com/job-listing/senior-backend-engineer-globex-JV_IC1147401_KO0,24_KE25,31.htm?jl=1009876543210&amp;utm_source=alert">
            Senior Backend Engineer
          </a>
        </td>
      </tr>
    </table>
    <p>You are receiving this email because you subscribed to job alerts.</p>
  </body>
</html>
"""


def make_message(**overrides: object) -> GmailMessage:
    values: dict[str, object] = {
        "id": "msg-1",
        "thread_id": "thread-1",
        "subject": "5 new jobs for you",
        "sender": "Glassdoor Jobs <noreply@glassdoor.com>",
        "received_at": RECEIVED_AT,
        "snippet": "New jobs",
        "plain_text": "",
        "html": ALERT_HTML,
        "label_ids": ("INBOX",),
    }
    values.update(overrides)
    return GmailMessage(**values)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Sender detection
# ---------------------------------------------------------------------------
def test_glassdoor_sender_variants() -> None:
    assert is_glassdoor_sender("Glassdoor <noreply@glassdoor.com>")
    assert is_glassdoor_sender("jobs@mail.glassdoor.com")
    assert is_glassdoor_sender("alerts@glassdoor.co.uk")
    assert not is_glassdoor_sender("noreply@example.com")
    assert not is_glassdoor_sender("someone@notglassdoor.com")
    assert not is_glassdoor_sender("")


def test_search_query_covers_glassdoor_and_the_time_window() -> None:
    assert glassdoor_search_query(30) == "from:glassdoor.com newer_than:30d"
    assert GLASSDOOR_SENDER_QUERY == "from:glassdoor.com"


# ---------------------------------------------------------------------------
# URL handling
# ---------------------------------------------------------------------------
def test_canonical_url_drops_tracking_but_keeps_job_id() -> None:
    url = (
        "https://www.glassdoor.com/partner/jobListing.htm?"
        "pos=101&jobListingId=1234567890&src=alert&utm_source=email#frag"
    )

    assert canonicalize_job_url(url) == (
        "https://www.glassdoor.com/partner/jobListing.htm?jobListingId=1234567890"
    )


def test_dedupe_key_prefers_the_glassdoor_job_id() -> None:
    job = GlassdoorJob(
        job_title="Data Engineer",
        company="Acme",
        location="Austin, TX",
        job_url="https://www.glassdoor.com/partner/jobListing.htm?jobListingId=42",
        email_subject="Jobs",
        received_at=RECEIVED_AT,
        job_description=None,
        message_id="m1",
    )

    assert job.dedupe_key == "glassdoor-id:42"


# ---------------------------------------------------------------------------
# Email classification
# ---------------------------------------------------------------------------
def test_glassdoor_job_email_is_recognised() -> None:
    assert is_glassdoor_job_email(make_message())


def test_non_glassdoor_email_is_not_a_job_email() -> None:
    message = make_message(sender="Recruiter <recruiter@example.com>")

    assert not is_glassdoor_job_email(message)
    assert extract_glassdoor_jobs(message) == []


def test_glassdoor_email_without_job_links_is_ignored() -> None:
    message = make_message(
        html="<html><body><p>Update your alert preferences</p>"
        '<a href="https://www.glassdoor.com/member/settings">Settings</a></body></html>'
    )

    assert not is_glassdoor_job_email(message)
    assert extract_glassdoor_jobs(message) == []


# ---------------------------------------------------------------------------
# HTML extraction
# ---------------------------------------------------------------------------
def test_extracts_jobs_from_alert_email() -> None:
    jobs = extract_glassdoor_jobs(make_message())

    assert len(jobs) == 2
    first, second = jobs

    assert first.job_title == "Software Engineer"
    assert first.company == "Acme Corp"
    assert first.location == "Austin, TX"
    assert first.job_url == (
        "https://www.glassdoor.com/partner/jobListing.htm?jobListingId=1234567890"
    )
    assert first.email_subject == "5 new jobs for you"
    assert first.received_at == RECEIVED_AT
    assert first.message_id == "msg-1"
    assert first.job_description is None

    assert second.job_title == "Senior Backend Engineer"
    assert second.company == "Globex"
    assert second.location == "Remote"
    assert "jl=1009876543210" in second.job_url
    assert "utm_source" not in second.job_url


def test_extraction_is_deduplicated_within_an_email() -> None:
    duplicate_link = (
        '<a href="https://www.glassdoor.com/partner/jobListing.htm'
        '?jobListingId=1234567890&src=footer">View job</a></body>'
    )
    duplicated = ALERT_HTML.replace("</body>", duplicate_link)

    jobs = extract_glassdoor_jobs(make_message(html=duplicated))

    assert len(jobs) == 2


def test_duplicate_postings_across_emails_are_skipped() -> None:
    first_email = make_message(id="msg-1")
    second_email = make_message(
        id="msg-2",
        subject="More jobs for you",
        html=ALERT_HTML.replace(
            "jobListingId=1234567890", "jobListingId=1234567890&utm_campaign=repeat"
        ),
    )

    jobs = extract_glassdoor_jobs_from_messages([first_email, second_email])

    assert len(jobs) == 2
    assert {job.message_id for job in jobs} == {"msg-1"}


def test_extraction_uses_the_block_title_over_the_link_hint() -> None:
    html = """
    <table>
      <tr><td><h2>Platform Engineer</h2></td></tr>
      <tr><td>Initech</td></tr>
      <tr><td>Berlin, Germany</td></tr>
      <tr><td>
        <a title="Site Reliability Engineer"
           href="https://www.glassdoor.com/job-listing/sre-initech-JV_1.htm?jobListingId=777">
           View job
        </a>
      </td></tr>
    </table>
    """

    jobs = extract_glassdoor_jobs(make_message(html=html))

    assert len(jobs) == 1
    assert jobs[0].job_title == "Platform Engineer"
    assert jobs[0].company == "Initech"
    assert jobs[0].location == "Berlin, Germany"


def test_link_title_attribute_is_used_when_no_block_text_exists() -> None:
    html = (
        '<p><a title="Site Reliability Engineer" '
        'href="https://www.glassdoor.com/job-listing/sre-initech-JV_1.htm?jobListingId=777">'
        "Apply</a></p>"
    )

    jobs = extract_glassdoor_jobs(make_message(html=html))

    assert len(jobs) == 1
    assert jobs[0].job_title == "Site Reliability Engineer"
    assert jobs[0].company is None
    assert jobs[0].location is None


# ---------------------------------------------------------------------------
# Plain-text fallback
# ---------------------------------------------------------------------------
def test_extracts_jobs_from_plain_text_email() -> None:
    message = make_message(
        html="",
        plain_text=(
            "New jobs for you\n"
            "\n"
            "Staff Data Engineer\n"
            "Initech\n"
            "Seattle, WA\n"
            "https://www.glassdoor.com/job-listing/staff-data-engineer-initech-JV_9.htm?jl=5555&src=email\n"
        ),
    )

    jobs = extract_glassdoor_jobs(message)

    assert len(jobs) == 1
    assert jobs[0].job_title == "Staff Data Engineer"
    assert jobs[0].company == "Initech"
    assert jobs[0].location == "Seattle, WA"
    assert jobs[0].job_url.endswith("?jl=5555")


def test_limited_description_is_kept_when_present() -> None:
    html = """
    <table>
      <tr><td><h2>Backend Engineer</h2></td></tr>
      <tr><td>Hooli</td></tr>
      <tr><td>Denver, CO</td></tr>
      <tr><td>Build reliable Python services for our payments platform and help
      the team scale to millions of requests per day across regions.</td></tr>
      <tr><td>
        <a href="https://www.glassdoor.com/job-listing/be-JV_2.htm?jobListingId=888">View job</a>
      </td></tr>
    </table>
    """

    jobs = extract_glassdoor_jobs(make_message(html=html))

    assert len(jobs) == 1
    assert jobs[0].job_description is not None
    assert "payments platform" in jobs[0].job_description


def test_limit_caps_the_number_of_jobs_returned() -> None:
    jobs = extract_glassdoor_jobs_from_messages([make_message()], limit=1)

    assert len(jobs) == 1


# ---------------------------------------------------------------------------
# Domain and URL allow-listing (regression: lookalike hosts)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "sender",
    [
        "Alerts <x@glassdoor.com.evil.com>",
        "x@www.glassdoor.com.evil.com",
        "x@glassdoor.co.uk.evil.com",
        "x@evilglassdoor.com",
        "x@glassdoor.evil",
        "x@glassdoor.com-",
        "x@notglassdoor.com",
        "no-at-sign",
        "",
    ],
)
def test_lookalike_sender_hosts_are_rejected(sender: str) -> None:
    assert not is_glassdoor_sender(sender)
    assert extract_glassdoor_jobs(make_message(sender=sender)) == []


@pytest.mark.parametrize(
    "sender",
    [
        # The trusted host appears only in the display name; the mailbox is evil.
        '"noreply@glassdoor.com" <attacker@evil.com>',
        'Glassdoor "x@glassdoor.com" <attacker@evil.com>',
        # A quoted local part must not be treated as the domain either.
        '"x@glassdoor.com"@evil.com',
        # Multiple @ signs: only the real addr-spec domain counts.
        "attacker@glassdoor.com@evil.com",
        # A port in the sender address is not a valid Glassdoor mailbox.
        "noreply@glassdoor.com:2525",
    ],
)
def test_display_names_cannot_spoof_a_glassdoor_sender(sender: str) -> None:
    assert not is_glassdoor_sender(sender)
    assert not is_glassdoor_job_email(make_message(sender=sender))
    assert extract_glassdoor_jobs(make_message(sender=sender)) == []


def test_display_name_with_glassdoor_brand_is_still_accepted() -> None:
    assert is_glassdoor_sender('"Glassdoor Jobs" <alerts@glassdoor.com>')
    assert is_glassdoor_sender("Glassdoor <noreply@mail.glassdoor.com>")


@pytest.mark.parametrize(
    "host",
    [
        "glassdoor.com",
        "www.glassdoor.com",
        "mail.glassdoor.com",
        "glassdoor.co.uk",
        "www.glassdoor.co.uk",
        "glassdoor.com.au",
        "glassdoor.ca",
        "glassdoor.com.",
    ],
)
def test_supported_glassdoor_hosts_are_accepted(host: str) -> None:
    assert is_glassdoor_host(host)
    assert is_glassdoor_host(host.upper())
    assert is_glassdoor_sender(f"Glassdoor <alerts@{host}>")


@pytest.mark.parametrize(
    "host",
    [
        "glassdoor.com.evil.com",
        "evilglassdoor.com",
        "glassdoor.co.uk.evil.com",
        "glassdoor.evil",
        "",
        None,
    ],
)
def test_lookalike_hosts_fail_host_validation(host: str | None) -> None:
    assert not is_glassdoor_host(host)


@pytest.mark.parametrize(
    "url",
    [
        "https://www.glassdoor.com/job-listing/data-engineer-JV_1.htm?jobListingId=1",
        "http://glassdoor.com/partner/jobListing.htm?jobListingId=2",
        "https://glassdoor.co.uk/job-listing/sre-JV_2.htm?jl=3",
        "https://www.glassdoor.com/Job/software-engineer-acme-JV_3.htm",
    ],
)
def test_legitimate_glassdoor_job_urls_are_accepted(url: str) -> None:
    assert is_glassdoor_job_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://www.glassdoor.com.evil.com/job-listing/x-JV_1.htm?jobListingId=1",
        "https://glassdoor.com@evil.com/job-listing/x",
        "https://user:pass@www.glassdoor.com/job-listing/x",
        "ftp://www.glassdoor.com/job-listing/x",
        "javascript:alert(1)//www.glassdoor.com/job-listing/x",
        "https://www.glassdoor.com:8443/job-listing/x",
        "https://www.glassdoor.com/not-a-job-path",
        "//www.glassdoor.com/job-listing/x",
        "not a url",
        "",
    ],
)
def test_unsafe_or_non_job_urls_are_rejected(url: str) -> None:
    assert not is_glassdoor_job_url(url)


MIXED_LINK_HTML = """
<div>
  <h2>Real Job</h2><div>Acme Corp</div><div>Austin, TX</div>
  <a href="https://www.glassdoor.com/job-listing/r-JV_1.htm?jl=101">View job</a>
</div>
<div>
  <h2>Fake Job</h2><div>Evil Corp</div><div>Remote</div>
  <a href="https://www.glassdoor.com.evil.com/job-listing/f-JV_2.htm?jl=202">View job</a>
</div>
"""


def test_lookalike_job_links_are_not_extracted() -> None:
    jobs = extract_glassdoor_jobs(make_message(html=MIXED_LINK_HTML))

    assert [job.job_title for job in jobs] == ["Real Job"]
    assert all(".evil.com" not in job.job_url for job in jobs)


def test_spoofed_glassdoor_sender_yields_nothing() -> None:
    message = make_message(sender="Alerts <x@glassdoor.com.evil.com>")

    assert not is_glassdoor_job_email(message)
    assert extract_glassdoor_jobs(message) == []


# ---------------------------------------------------------------------------
# Multi-job layouts (regression: one table, several postings)
# ---------------------------------------------------------------------------
MULTI_JOB_LINK_LAST_HTML = """
<table>
  <tr><td><h2>Data Engineer</h2></td></tr>
  <tr><td>Acme Corp</td></tr>
  <tr><td>Austin, TX</td></tr>
  <tr><td>
    <a href="https://www.glassdoor.com/partner/jobListing.htm?jobListingId=111">View job</a>
  </td></tr>
  <tr><td><h2>Product Manager</h2></td></tr>
  <tr><td>Globex</td></tr>
  <tr><td>Remote</td></tr>
  <tr><td>
    <a href="https://www.glassdoor.com/partner/jobListing.htm?jobListingId=222">View job</a>
  </td></tr>
</table>
"""

MULTI_JOB_TITLE_FIRST_HTML = """
<table>
  <tr><td>
    <a href="https://www.glassdoor.com/job-listing/sde-JV_1.htm?jl=111">Senior Data Engineer</a>
  </td></tr>
  <tr><td>Acme Corp</td></tr>
  <tr><td>Austin, TX</td></tr>
  <tr><td>
    <a href="https://www.glassdoor.com/job-listing/pm-JV_2.htm?jl=222">Product Manager</a>
  </td></tr>
  <tr><td>Globex</td></tr>
  <tr><td>Remote</td></tr>
</table>
"""

MULTI_JOB_NESTED_TABLES_HTML = """
<table>
  <tr><td><table>
    <tr><td><h2>Data Engineer</h2></td></tr>
    <tr><td>Acme Corp</td></tr>
    <tr><td>Austin, TX</td></tr>
    <tr><td>
      <a href="https://www.glassdoor.com/partner/jobListing.htm?jobListingId=111">View job</a>
    </td></tr>
  </table></td></tr>
  <tr><td><table>
    <tr><td><h2>Product Manager</h2></td></tr>
    <tr><td>Globex</td></tr>
    <tr><td>Remote</td></tr>
    <tr><td>
      <a href="https://www.glassdoor.com/partner/jobListing.htm?jobListingId=222">View job</a>
    </td></tr>
  </table></td></tr>
</table>
"""

MULTI_JOB_DIV_CARDS_HTML = """
<div>
  <h2>Site Reliability Engineer</h2><div>Initech</div><div>Seattle, WA</div>
  <a href="https://www.glassdoor.com/job-listing/sre-JV_3.htm?jobListingId=333">View job</a>
</div>
<div>
  <h2>Staff Engineer</h2><div>Hooli</div><div>Denver, CO</div>
  <a href="https://www.glassdoor.com/job-listing/staff-JV_4.htm?jobListingId=444">View job</a>
</div>
"""


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        (
            MULTI_JOB_LINK_LAST_HTML,
            [
                ("Data Engineer", "Acme Corp", "Austin, TX"),
                ("Product Manager", "Globex", "Remote"),
            ],
        ),
        (
            MULTI_JOB_TITLE_FIRST_HTML,
            [
                ("Senior Data Engineer", "Acme Corp", "Austin, TX"),
                ("Product Manager", "Globex", "Remote"),
            ],
        ),
        (
            MULTI_JOB_NESTED_TABLES_HTML,
            [
                ("Data Engineer", "Acme Corp", "Austin, TX"),
                ("Product Manager", "Globex", "Remote"),
            ],
        ),
        (
            MULTI_JOB_DIV_CARDS_HTML,
            [
                ("Site Reliability Engineer", "Initech", "Seattle, WA"),
                ("Staff Engineer", "Hooli", "Denver, CO"),
            ],
        ),
    ],
)
def test_extracts_every_job_from_multi_job_layouts(
    html: str, expected: list[tuple[str, str, str]]
) -> None:
    jobs = extract_glassdoor_jobs(make_message(html=html))

    assert [(job.job_title, job.company, job.location) for job in jobs] == expected


def test_multi_job_table_links_stay_with_their_own_jobs() -> None:
    jobs = extract_glassdoor_jobs(make_message(html=MULTI_JOB_LINK_LAST_HTML))

    assert [job.job_url for job in jobs] == [
        "https://www.glassdoor.com/partner/jobListing.htm?jobListingId=111",
        "https://www.glassdoor.com/partner/jobListing.htm?jobListingId=222",
    ]


def test_sibling_row_layout_does_not_borrow_the_previous_job_metadata() -> None:
    jobs = extract_glassdoor_jobs(make_message(html=MULTI_JOB_TITLE_FIRST_HTML))

    assert len(jobs) == 2
    assert (jobs[0].job_title, jobs[0].company, jobs[0].location) == (
        "Senior Data Engineer",
        "Acme Corp",
        "Austin, TX",
    )
    assert (jobs[1].job_title, jobs[1].company, jobs[1].location) == (
        "Product Manager",
        "Globex",
        "Remote",
    )


def test_same_posting_under_two_url_shapes_is_deduplicated_in_one_email() -> None:
    html = """
    <div><h2>Backend Engineer</h2><div>Hooli</div><div>Denver, CO</div>
      <a href="https://www.glassdoor.com/partner/jobListing.htm?jobListingId=555">View job</a></div>
    <div><h2>Backend Engineer</h2><div>Hooli</div><div>Denver, CO</div>
      <a href="https://www.glassdoor.com/job-listing/be-JV_5.htm?jl=999">View job</a></div>
    """

    jobs = extract_glassdoor_jobs(make_message(html=html))

    assert len(jobs) == 1
    assert jobs[0].job_title == "Backend Engineer"
