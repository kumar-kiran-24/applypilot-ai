---
noteId: "ae4e2e50d81811f1b351cbd265064a5d"
tags: []

---

# Frontend API Integration - Google Login and Gmail

This document describes the API as implemented today. It is the contract for the
future frontend; do not add fields or flows that are not listed here.

Base URL (development): `http://127.0.0.1:8000/api/v1`

All responses are JSON unless the endpoint is documented as a browser redirect.
Timestamps are ISO 8601 UTC strings. Tokens, cookies and OAuth codes must never be
logged or persisted by the frontend beyond the access token described below.

## 1. Authentication model

- ApplyPilot uses its own HS256 JWT issued by the Google login callback. Send it
  as `Authorization: Bearer <jwt>` on every protected endpoint.
- The JWT is **not** an OAuth token and is never sent to Google.
- Google login and Gmail authorization are two separate OAuth flows that share
  one signed, HttpOnly session cookie (`applypilot_oauth_session`).
- The Gmail flow adds `gmail.readonly` incrementally; login keeps
  `openid email profile` only.

## 2. Endpoint summary

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/auth/google/login` | none | Browser redirect to Google login |
| GET | `/auth/google/callback` | OAuth state cookie | Login callback, redirects to the frontend with a one-time code |
| POST | `/auth/login/exchange` | none (single-use code) | Exchange the login code for the ApplyPilot JWT |
| GET | `/auth/me` | Bearer | Current user |
| GET | `/gmail/connect` | Bearer + cookie | Start Gmail read-only authorization |
| GET | `/gmail/callback` | OAuth state cookie | Finish Gmail authorization (browser navigation) |
| GET | `/gmail/status` | Bearer | Connection status |
| DELETE | `/gmail/disconnect` | Bearer | Revoke token and delete stored credentials |
| GET | `/gmail/glassdoor/jobs` | Bearer | Extract Glassdoor jobs from recent email |

## 3. Google login

### `GET /auth/google/login`

No request parameters. Must be opened as a **top-level browser navigation**
(`window.location.assign(...)` or a link), not fetched.

Response: `302` redirect to `accounts.google.com`.

### `GET /auth/google/callback`

Browser navigation from Google. Do not call with `fetch`: it depends on the
HttpOnly state cookie and on the `code`/`state` query parameters added by Google.

It never renders a token. On success it responds `303` with an empty body and
redirects to the configured frontend login callback
(`AUTH_FRONTEND_REDIRECT_URI`, `http://127.0.0.1:3000/auth/callback` by default):

```text
http://127.0.0.1:3000/auth/callback?code=<single-use-code>
```

The code is random (256 bits), valid for at most 60 seconds, stored only as a
SHA-256 hash, and redeemable exactly once.

Failures redirect to the same frontend route with a fixed identifier instead of
a JSON error, so the browser never lands on a raw API page:

| `?error=` | Meaning |
|---|---|
| `state` | OAuth state cookie missing/expired or did not match |
| `oauth` | Google returned an error or the token exchange failed |
| `identity` | Google returned no usable verified identity |
| `email_unverified` | The Google email address is not verified |
| `account_exists` | An ApplyPilot account already uses this email |
| `inactive` | The ApplyPilot account is inactive |
| `unavailable` | A transient failure (e.g. concurrent account creation) |

No provider error text, authorization code or token is forwarded in the URL.

### `POST /auth/login/exchange`

Request (no bearer token; call from trusted server-side code only):

```json
{ "code": "<code from the callback redirect>" }
```

`200` response - the same shape the callback used to render:

```json
{
  "message": "Google authentication successful.",
  "access_token": "<applypilot-jwt>",
  "token_type": "bearer",
  "expires_in": 3600,
  "user": {
    "id": "uuid",
    "email": "user@example.com",
    "full_name": "Full Name",
    "profile_picture": "https://..."
  }
}
```

| Status | Meaning |
|---|---|
| 400 | Code is invalid, expired, or was already redeemed |
| 401 | The code's account is unavailable |
| 403 | The account is inactive |
| 422 | The code does not match the expected format |

The code is consumed by a single atomic `UPDATE` before the token is issued, so
retrying a consumed code always fails. Never log the code or the returned token.

### `GET /auth/me`

Request: `Authorization: Bearer <jwt>`, no body.

`200`:

```json
{ "id": "uuid", "email": "user@example.com", "full_name": "Full Name", "profile_picture": "https://..." }
```

`401`: `Authentication required.` or `Invalid or expired access token.`
`403`: inactive account.

## 4. Gmail connection

### 4.1 Sequence

1. SPA calls `GET /gmail/connect` with the JWT and `credentials: "include"`.
2. The API stores the OAuth state in the signed session cookie and returns
   Google's consent URL.
3. The SPA navigates the whole page to `authorization_url`.
4. The user signs in with the **same Google account** used for ApplyPilot login
   and grants read-only Gmail access.
5. Google redirects the browser to `GET /gmail/callback`. The API validates the
   state, verifies the Google `sub` against the ApplyPilot user, requires a
   verified email and the `gmail.readonly` scope, then stores the refresh token
   encrypted.
6. If `GMAIL_FRONTEND_REDIRECT_URI` is configured, the browser is redirected
   (`303`) to that frontend route; otherwise the callback returns JSON.
7. The SPA calls `GET /gmail/status` to render the connected address.

### 4.2 `GET /gmail/connect`

Headers: `Authorization: Bearer <jwt>`; browser fetch must use
`credentials: "include"`.

`200`:

```json
{
  "authorization_url": "https://accounts.google.com/o/oauth2/v2/auth?...",
  "required_scopes": [
    "openid",
    "email",
    "profile",
    "https://www.googleapis.com/auth/gmail.readonly"
  ],
  "message": "Open the authorization URL in a browser to grant read-only Gmail access with the same Google account."
}
```

Side effect: sets `applypilot_oauth_session` (`HttpOnly`, `SameSite=Lax`,
`Path=/`, `Max-Age=600`, `Secure` outside local development). JavaScript cannot
and must not read it.

Errors:

| Status | Meaning |
|---|---|
| 401 | Missing/invalid JWT |
| 503 | Gmail is not configured, the encryption key is invalid, or (in production) the callback URL is missing or not HTTPS |

`503` bodies contain a fixed `detail` string and never a configuration value.

### 4.3 `GET /gmail/callback`

Browser navigation only, no `Authorization` header. Query parameters are added
by Google.

`200` (only when `GMAIL_FRONTEND_REDIRECT_URI` is empty):

```json
{
  "connected": true,
  "gmail_email": "user@example.com",
  "connected_at": "2026-11-04T06:20:00Z",
  "granted_scopes": [
    "https://www.googleapis.com/auth/gmail.readonly",
    "email",
    "openid",
    "profile"
  ],
  "message": "Gmail connected. ApplyPilot can now read job-related emails."
}
```

`303` when `GMAIL_FRONTEND_REDIRECT_URI` is configured: `Location` is that
frontend route. It is server-configured, never taken from the request.

Errors (all JSON, no token):

| Status | Meaning |
|---|---|
| 400 | Missing/expired OAuth session, state mismatch, or `gmail.readonly` was not granted |
| 401 | Google identity missing/unverified, or the ApplyPilot account is unavailable |
| 403 | Consent was given with a different Google account than the ApplyPilot login |
| 409 | Google did not return a refresh token and no earlier valid token exists |
| 503 | Credential encryption/storage is not configured |

### 4.4 `GET /gmail/status`

Headers: `Authorization: Bearer <jwt>`.

`200` connected:

```json
{
  "connected": true,
  "is_active": true,
  "gmail_email": "user@example.com",
  "connected_at": "2026-11-04T06:20:00Z",
  "granted_scopes": ["https://www.googleapis.com/auth/gmail.readonly", "email", "openid", "profile"]
}
```

`200` not connected:

```json
{ "connected": false, "is_active": false, "gmail_email": null, "connected_at": null, "granted_scopes": [] }
```

No access token, refresh token or encrypted credential is ever returned.

### 4.5 `DELETE /gmail/disconnect`

Headers: `Authorization: Bearer <jwt>`, no body.

`200`:

```json
{
  "disconnected": true,
  "token_revoked": true,
  "message": "Gmail disconnected and stored credentials removed."
}
```

`token_revoked` is `false` when Google did not confirm revocation (network error,
already-invalid token, non-2xx/400 response). The local credential is deleted in
both cases.

Errors: `404 gmail_not_connected` (`Gmail is not connected for this account.`),
`401`.

## 5. Glassdoor job discovery

### `GET /gmail/glassdoor/jobs`

Headers: `Authorization: Bearer <jwt>`.

Query parameters:

| Name | Type | Default | Constraints |
|---|---|---|---|
| `days` | integer | 30 | 1-365 |
| `max_messages` | integer | 25 | 1-100 |

The API searches recent email with `from:glassdoor.com newer_than:<days>d`,
fetches up to `max_messages`, and parses job recommendations. Nothing is
persisted, no attachment is downloaded and Glassdoor is never scraped.

`200`:

```json
{
  "messages_scanned": 3,
  "jobs_found": 4,
  "jobs": [
    {
      "job_title": "Backend Engineer",
      "company": "Acme",
      "location": "Remote",
      "job_url": "https://www.glassdoor.com/job-listing/...",
      "email_subject": "Jobs for you",
      "received_at": "2026-11-03T09:12:00Z",
      "job_description": null,
      "message_id": "18f..."
    }
  ]
}
```

`company`, `location`, `received_at` and `job_description` can be `null`: the
parser never invents missing fields. Jobs are deduplicated by Glassdoor job id,
canonical URL and title/company/location content.

Errors:

| Status | `code` / meaning |
|---|---|
| 401 | Missing/invalid JWT |
| 403 | `gmail_scope_missing` - read-only access missing |
| 404 | `gmail_not_connected` |
| 409 | `gmail_credentials_invalid` - token expired/revoked; the connection is marked inactive and the user must reconnect |
| 429 | `gmail_rate_limited` |
| 502 | `gmail_api_error` - Gmail unreachable or returned an unexpected response |
| 422 | `validation_error` - `days`/`max_messages` out of range |

Discovery can take tens of seconds (messages are fetched sequentially, one
request each). Use a client timeout of at least 60 seconds and disable duplicate
submissions while it runs.

## 6. Error envelope

Domain errors (`AppError` subclasses):

```json
{ "detail": "Gmail is not connected for this account.", "code": "gmail_not_connected" }
```

Framework errors (FastAPI `HTTPException`) have only `detail`.

Request validation errors (`422`):

```json
{
  "detail": "Request validation failed.",
  "code": "validation_error",
  "details": [{ "loc": ["query", "days"], "msg": "Input should be less than or equal to 365" }]
}
```

Unexpected failures: `500 { "detail": "Internal server error.", "code": "internal_error" }`.
`401` responses also carry `WWW-Authenticate: Bearer`.

## 7. Cookie, CORS and host requirements

- Call `/gmail/connect` with `credentials: "include"` **directly against the API
  origin**. Do not proxy it through the frontend host: the cookie belongs to the
  API host and must be sent back after Google's redirect.
- `BACKEND_CORS_ORIGINS` must contain the exact frontend origin
  (e.g. `http://127.0.0.1:3000`). CORS allows credentials and the headers
  `Authorization`, `Content-Type`, `Accept`.
- Use one host for the whole flow. `localhost` and `127.0.0.1` are different
  cookie sites; mixing them loses the OAuth state cookie and the callback fails
  with `400`. This project's local setup therefore uses `127.0.0.1` for the API,
  the frontend (`http://127.0.0.1:3000`) and both callbacks; serve the dev server
  on `127.0.0.1` (Next.js: `next dev -H 127.0.0.1`; Vite: `vite --host 127.0.0.1`).
  The host used at `/gmail/connect` must also be the host in the registered
  `GMAIL_REDIRECT_URI`.
- The state cookie is `HttpOnly` and `SameSite=Lax`; the Google redirect is a
  top-level navigation, so the cookie is sent. Any fetch-based callback will not
  work.
- The cookie lasts `OAUTH_SESSION_MAX_AGE_SECONDS` (600 s). A slow consent screen
  requires restarting the flow from `/gmail/connect`.

## 8. Safe client examples

```js
const API = "http://127.0.0.1:8000/api/v1";

// 1. Google login: full-page navigation, not fetch.
window.location.assign(`${API}/auth/google/login`);

// 2. The login callback redirects to AUTH_FRONTEND_REDIRECT_URI with ?code=...
//    Exchange it once from server-side code, then store the JWT in an HttpOnly
//    cookie. Never log the code or the token, and never put the token in a URL.
const exchange = await fetch(`${API}/auth/login/exchange`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ code }),
});
const { access_token: jwt } = await exchange.json();

// 3. Current user.
const me = await fetch(`${API}/auth/me`, {
  headers: { Authorization: `Bearer ${jwt}` },
}).then((r) => r.json());

// 4. Start Gmail authorization.
const connectResponse = await fetch(`${API}/gmail/connect`, {
  method: "GET",
  headers: { Authorization: `Bearer ${jwt}` },
  credentials: "include", // REQUIRED: stores the signed state cookie
});
if (!connectResponse.ok) {
  const error = await connectResponse.json();
  throw new Error(error.detail);
}
const { authorization_url } = await connectResponse.json();
window.location.assign(authorization_url); // full-page navigation to Google

// 5. After the callback (JSON or the configured frontend redirect), read status.
const status = await fetch(`${API}/gmail/status`, {
  headers: { Authorization: `Bearer ${jwt}` },
}).then((r) => r.json());

// 6. Discover jobs.
const jobs = await fetch(`${API}/gmail/glassdoor/jobs?days=30&max_messages=25`, {
  headers: { Authorization: `Bearer ${jwt}` },
  signal: AbortSignal.timeout(90_000),
}).then((r) => r.json());

// 7. Disconnect.
await fetch(`${API}/gmail/disconnect`, {
  method: "DELETE",
  headers: { Authorization: `Bearer ${jwt}` },
});
```

Never send the ApplyPilot JWT, the session cookie or any OAuth token to
`accounts.google.com`, analytics or logs. The only Google URL the frontend
receives is the short-lived `authorization_url` returned by `/gmail/connect`.

## 9. Local setup checklist (frontend)

1. Backend running on `http://127.0.0.1:8000` (same host as the redirect URIs).
2. `http://127.0.0.1:8000/api/v1/auth/google/callback` and
   `http://127.0.0.1:8000/api/v1/gmail/callback` registered verbatim in Google
   Cloud Console, including host, port and path.
3. `GMAIL_TOKEN_ENCRYPTION_KEY` and `OAUTH_SESSION_SECRET` set; the backend
   refuses to start without the session secret.
4. `BACKEND_CORS_ORIGINS` contains `http://127.0.0.1:3000`.
5. Frontend calls `/gmail/connect` with `credentials: "include"`.
6. `GMAIL_FRONTEND_REDIRECT_URI` set (and its origin in `BACKEND_CORS_ORIGINS`)
   if the callback should land on a SPA route instead of JSON.
