---
noteId: "75e420d0dc0811f1a81e4f0576e6a512"
tags: []

---

# Phase 2E Stage E - Live browser streaming

## 1. Architecture

Stage E uses an **in-process Chrome DevTools Protocol (CDP) screencast relay**.
It attaches a CDP session to the existing persistent Playwright page and streams
JPEG frames over an authenticated WebSocket. User pointer, wheel, text and
editing-key events are translated into a narrow allow-list of CDP `Input`
commands on that same page.

There is **no separate streaming service and no second browser**. The browser
displayed in ApplyPilot and the browser controlled by Stage C/D are the same
Playwright context, cookie jar and page.

Why this approach:

| Option | Decision |
| --- | --- |
| Headed Chromium + VNC + noVNC/websockify | Rejected: new service stack, virtual display (Xvfb) on Linux, VNC input surface, larger operational and security footprint. |
| Raw CDP WebSocket exposed to the browser | Rejected: every CDP method would be reachable, including navigation, cookies and storage. |
| CDP screencast relay in the API process | **Selected**: reuses Playwright's Chromium and dependencies, works headless on Windows and Linux, no public debugging port, frames/input stay inside the existing ownership model. |
| Separate CDP relay service | Not needed for the current single-process deployment. The provider boundary remains in `app/integrations/browser/view.py` if isolation is required later. |

Components:

* `app/integrations/browser/streaming.py` - attaches CDP, streams frames,
  validates input, detects navigation/expiry.
* `app/services/browser_view_service.py` - hashed single-use tickets, rate
  limits and the one-view-per-session registry.
* `app/api/v1/endpoints/browser_view.py` - ticket endpoint and WebSocket.
* `app/integrations/browser/persistent.py` - interaction lock, user-activity
  timestamp, navigation epoch and internal CDP accessor.
* `frontend/src/components/applications/application-browser-workspace.tsx` -
  live viewport, toolbar, automation status and final-review summary.

## 2. Security model

* A ticket is issued only after a normal bearer-authenticated request checks
  application ownership, session ownership and a genuinely live browser.
* Tickets are random, stored only as SHA-256 hashes, single use, bound to one
  user/session/application and expire after
  `APPLICATION_BROWSER_VIEW_TICKET_TTL_SECONDS` (default 45s).
* Ticket issuance is rate limited per user; a second live view cannot attach to
  the same session until the first releases it.
* The WebSocket checks `Origin` against `BACKEND_CORS_ORIGINS`; a missing Origin
  is refused in production. A refused ticket is reported with the close code the
  UI distinguishes: `4401` (expired/replayed/invalid), `4404` (browser session
  closed) and `4409` (another view already attached).
* The client can send only `mouse`, `key`, `text` and `ping` messages.
  Coordinates, button names, modifier bits, key names, text length and scroll
  deltas are validated; input is rate limited.
* No message contains cookies, `Set-Cookie`, storage state, authorization
  headers, CDP endpoints, filesystem paths or browser-control credentials.
* Existing navigation policy, SSRF/DNS checks, popup closure and download
  blocking still apply. The relay adds no navigation command.
* CAPTCHA, OTP, MFA, passwords, legal declarations and submission are never
  automated.

## 3. Synchronization with Stage C/D

* Stage C field actions and forwarded user input share one `asyncio.Lock`, so
  automation and the user cannot inject input at the same instant.
* Forwarded clicks/keys/wheel mark user activity. Before each field (and before
  finalising), the fill service pauses with `NEEDS_USER_INPUT` if the user acted
  in the last 1.5s. A direct fill attempt also refuses while the user is active.
* Every main-frame navigation increments a navigation epoch. Stage C pauses when
  the epoch changes, and the live view sends `navigated` with
  `analysis_stale: true`; the UI invalidates its queries and asks for
  re-analysis.
* Stage D re-analysis reads the current page and stores a new immutable analysis
  version. Resume rebuilds from the latest analysis and preserves verified
  results; user-edited values are detected as already filled and are not
  overwritten.

## 4. Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `APPLICATION_AUTOMATION_ENABLED` | `false` | Master switch for opening Playwright browsers. Streaming is unavailable while false. |
| `APPLICATION_BROWSER_STREAMING_ENABLED` | `true` | Enables the CDP relay when automation is enabled. |
| `APPLICATION_BROWSER_HEADLESS` | `true` | Headless Chromium still produces screencast frames; no Xvfb is required. |
| `APPLICATION_BROWSER_SESSION_TTL_SECONDS` | `900` | Live browser lifetime; expiry closes the view. |
| `APPLICATION_BROWSER_MAX_ACTIVE_PER_USER` | `1` | Live browser sessions per user. |
| `APPLICATION_BROWSER_VIEW_TICKET_TTL_SECONDS` | `45` | Single-use ticket lifetime (10-120s). |
| `APPLICATION_BROWSER_VIEW_TICKETS_PER_MINUTE` | `6` | Ticket issuance rate limit per user. |
| `APPLICATION_BROWSER_VIEW_INPUT_PER_SECOND` | `120` | Forwarded input event limit per view. |
| `APPLICATION_BROWSER_VIEW_MAX_SECONDS` | `3600` | Maximum view duration; the browser stays open. |
| `BACKEND_CORS_ORIGINS` | `http://127.0.0.1:3000` | Also the WebSocket Origin allow-list. |

The frontend derives the WebSocket origin from `NEXT_PUBLIC_API_BASE_URL`
(`http` -> `ws`, `https` -> `wss`) and appends the returned
`websocket_path`. Keep that value absolute (for example
`https://api.example.com/api/v1`); the ticket is the only value in the query
string and is single use.

## 5. Windows development

```powershell
conda activate venv_rating
# Chromium is already installed in this environment.
$env:APPLICATION_AUTOMATION_ENABLED = "true"
$env:APPLICATION_BROWSER_HEADLESS = "true"
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Run the frontend with its normal `npm run dev`. `NEXT_PUBLIC_API_BASE_URL` must
point at the API, e.g. `http://127.0.0.1:8000/api/v1`, and the frontend origin
must be listed in `BACKEND_CORS_ORIGINS` (default
`http://127.0.0.1:3000`).

Use **one backend process**. The browser and view registries are in process
memory; a WebSocket routed to another worker cannot attach to a browser owned by
the first worker.

## 6. Ubuntu/Linux production

1. Install Chromium's system libraries for the Playwright version in the
   environment (system-level dependency, not a Python package):
   `python -m playwright install --with-deps chromium`.
2. Run the API behind TLS, bound to loopback, with **one worker**:
   `uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1`.
3. Keep only 80/443 public. Never expose port 8000's debugging interfaces,
   Chromium's remote-debugging port, or any CDP port. Playwright's connection to
   Chromium stays inside the process; the app does not open a CDP listener.
4. If horizontal scaling is required, route a user's requests and WebSocket to
   the same worker (sticky routing). This repository does not implement a shared
   browser registry or remote browser workers.
5. Set `BACKEND_CORS_ORIGINS` to the exact HTTPS frontend origin(s); the
   WebSocket Origin check uses the same list.

Nginx (replace hostnames/certificate paths; do not invent real values):

```nginx
location /api/v1/browser-sessions/view/ws {
    proxy_pass http://127.0.0.1:8000;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_buffering off;
    proxy_read_timeout 3600s;
    # The ticket is short lived and single use, but keep it out of access logs.
    access_log off;
}

location /api/v1/ {
    proxy_pass http://127.0.0.1:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

TLS is required in production because the session cookie and view ticket travel
over the connection; use `wss://` end to end from the browser to the proxy.
Process management (systemd, container supervisor, etc.) should restart the API
on failure and send `SIGTERM`, which runs the lifespan shutdown and closes every
live browser and view.

## 7. Resource limits and cleanup

* One live browser per user by default; one joined view per browser session.
* Chromium uses several hundred MB per live session; size the host for the
  number of concurrent users, not the number of accounts.
* Frame rate is capped at 25 fps and JPEG quality is 55; frames are dropped, not
  queued, under backpressure.
* Session TTL defaults to 15 minutes; view TTL defaults to one hour.
* Closing the view panel detaches the screencast but does **not** cancel the
  browser. Cancelling the session, session expiry, browser crash, logout and API
  shutdown close the browser; the view then receives `session_closed` and a
  WebSocket close code. The view also re-checks session ownership/TTL about
  every five seconds, so an expired session closes it even when no other API
  request observes the expiry.
* Tickets are pruned in memory; no view-session table or migration is required.

## 8. Verification

```powershell
conda run -n venv_rating python -m pytest -q
conda run -n venv_rating python -m ruff check app tests
conda run -n venv_rating python -m alembic current
conda run -n venv_rating python -m alembic check

$env:APPLYPILOT_RUN_BROWSER_TESTS = "1"
conda run -n venv_rating python -m pytest tests/test_phase2e_browser_stream_browser.py -m browser -q
```

The real test starts the FastAPI app on loopback, attaches to a real persistent
Chromium page and verifies actual JPEG frames, click, text, Backspace, wheel,
navigation, a foreign-Origin handshake refusal, ticket expiry/replay, cross-user
ticket denial and cleanup. The mock suite
(`tests/test_phase2e_browser_view.py`) verifies authorisation, input validation,
the Origin allow-list and the absence of any click/submit action only; its
passing result is not evidence of live streaming.

## 9. Known limitations

* Single backend process only (in-memory browsers/tickets/views). Multi-worker
  deployment needs sticky routing or a remote browser service.
* Screencast is visual only; there is no audio, clipboard, file-picker or
  drag-and-drop forwarding. CAPTCHA/OTP/MFA are completed on the page by the
  user; file uploads still use the Stage C resume path.
* The page viewport is fixed at 1280x900 and scaled responsively; resizing the
  browser viewport from the UI is not supported.
* A downloaded file, native browser dialog or cross-origin popup is outside the
  relay and remains subject to the existing navigation policy.
* Multi-tab/worker browser sharing is not supported; one page per session.
* The frontend has not been visually exercised against a live employer site in
  this environment; it is typechecked, linted and production-built, and the
  WebSocket transport is exercised by the real integration test.
