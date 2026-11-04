"""Read-only end-to-end check of the Gmail integration against a real account.

Run from ``backend/`` while the API is running:

    python scripts/gmail_e2e_verify.py
    python scripts/gmail_e2e_verify.py --days 60 --max-messages 50
    python scripts/gmail_e2e_verify.py --disconnect

The ApplyPilot JWT is read from a hidden prompt (or ``APPLYPILOT_JWT``) and is
never printed, logged or written to disk. Output contains only connection state,
counts and extracted job fields; no token, cookie or email body is shown.

``--disconnect`` revokes the Google token and deletes the stored credentials, so
it is opt-in and confirmed interactively.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys

import httpx

DEFAULT_BASE_URL = "http://127.0.0.1:8000/api/v1"
TOKEN_KEYS = ("access_token", "refresh_token", "encrypted_refresh_token", "id_token")


def _read_token() -> str:
    token = os.environ.get("APPLYPILOT_JWT", "").strip()
    if token:
        return token
    token = getpass.getpass("ApplyPilot access token (input hidden): ").strip()
    if not token:
        sys.exit("No access token provided.")
    return token


def _mask(email: str | None) -> str:
    if not email or "@" not in email:
        return "<unknown>"
    local, _, domain = email.partition("@")
    return f"{local[:1]}***@{domain}"


def _check(response: httpx.Response, label: str) -> dict:
    """Print a short result and return the parsed body (never the request)."""
    try:
        body = response.json()
    except ValueError:
        body = {"detail": response.text[:200]}
    print(f"[{label}] {response.status_code}")
    if response.status_code >= 400:
        print(f"  detail: {body.get('detail')}")
        if body.get("code"):
            print(f"  code:   {body['code']}")
    return body if isinstance(body, dict) else {}


def _assert_no_tokens(payload: object, label: str) -> None:
    text = str(payload)
    exposed = [key for key in TOKEN_KEYS if key in text]
    print(f"[{label}] token-like keys in response: {exposed or 'none'}")


def _print_jobs(jobs: list[dict], limit: int = 25) -> None:
    print(f"  extracted jobs: {len(jobs)} (showing {min(limit, len(jobs))})")
    for index, job in enumerate(jobs[:limit], start=1):
        print(
            f"  {index:>2}. {job.get('job_title')!r}"
            f" | company={job.get('company')!r}"
            f" | location={job.get('location')!r}"
            f" | received={job.get('received_at')}"
        )
        print(f"      url: {job.get('job_url')}")
        print(f"      subject: {job.get('email_subject')!r} | message={job.get('message_id')}")
    missing = {
        field: sum(1 for job in jobs if not job.get(field))
        for field in ("company", "location", "received_at", "job_url", "job_title")
    }
    print(f"  missing fields: {missing}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--max-messages", type=int, default=25)
    parser.add_argument("--no-discover", action="store_true", help="skip Glassdoor discovery")
    parser.add_argument(
        "--disconnect",
        action="store_true",
        help="revoke the Google token and delete the stored credentials",
    )
    args = parser.parse_args()

    token = _read_token()
    headers = {"Authorization": f"Bearer {token}"}

    with httpx.Client(base_url=args.base_url, headers=headers, timeout=180) as client:
        me = _check(client.get("/auth/me"), "auth/me")
        if not me.get("id"):
            print("The access token was rejected; sign in again and rerun this check.")
            return 1
        print(f"  user: {_mask(me.get('email'))} id={me.get('id')}")

        status = _check(client.get("/gmail/status"), "gmail/status")
        print(
            f"  connected={status.get('connected')} is_active={status.get('is_active')} "
            f"email={_mask(status.get('gmail_email'))} "
            f"connected_at={status.get('connected_at')}"
        )
        print(f"  scopes: {status.get('granted_scopes')}")
        _assert_no_tokens(status, "gmail/status")

        if not status.get("connected"):
            print("Gmail is not connected for this account; run the Gmail consent step first.")
            return 2

        if not args.no_discover:
            jobs_body = _check(
                client.get(
                    "/gmail/glassdoor/jobs",
                    params={"days": args.days, "max_messages": args.max_messages},
                ),
                "gmail/glassdoor/jobs",
            )
            if jobs_body and "jobs" in jobs_body:
                print(
                    f"  messages_scanned={jobs_body.get('messages_scanned')} "
                    f"jobs_found={jobs_body.get('jobs_found')}"
                )
                _print_jobs(jobs_body.get("jobs") or [])
                if jobs_body.get("messages_scanned") == 0:
                    print("  No Glassdoor emails found in the requested window.")
            _assert_no_tokens(jobs_body, "gmail/glassdoor/jobs")

        if args.disconnect:
            print("This revokes the Google token and deletes the stored credentials.")
            if input("Type 'disconnect' to continue: ").strip().lower() != "disconnect":
                print("Disconnect cancelled.")
                return 0
            result = _check(client.delete("/gmail/disconnect"), "gmail/disconnect")
            print(f"  token_revoked={result.get('token_revoked')}")
            after = _check(client.get("/gmail/status"), "gmail/status (after)")
            print(f"  connected={after.get('connected')} is_active={after.get('is_active')}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
