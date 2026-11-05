---
noteId: "67b70230d85611f1828a3f102aebc428"
tags: []

---

# ApplyPilot AI — Frontend (Phase 1)

**Your Intelligent Job Application Agent**

Next.js App Router frontend for the existing FastAPI backend: Google sign-in,
a protected dashboard shell, Gmail connection management and read-only Glassdoor
job discovery.

---

## 1. Stack

| Concern   | Choice                                             |
| --------- | -------------------------------------------------- |
| Framework | Next.js 15.5 (App Router, React 19)                |
| Language  | TypeScript 5.7, `strict` + `noUncheckedIndexedAccess` |
| Styling   | Tailwind CSS 3.4 + design tokens in `tailwind.config.ts` |
| Data      | TanStack Query 5 (client cache) + fetch (no axios)  |
| Icons     | lucide-react                                        |
| Utils     | `clsx` + `tailwind-merge` (`cn`)                    |

No state library, auth SDK, UI kit or HTTP client beyond those packages.

---

## 2. Run it

### Backend

The API must be running on **`http://127.0.0.1:8000`**:

```bash
cd backend
conda activate venv_rating
uvicorn app.main:app --reload
```

Required backend settings for this frontend (see `backend/.env.example`):

```dotenv
BACKEND_CORS_ORIGINS=["http://127.0.0.1:3000"]
OAUTH_SESSION_SECRET="<>= 32 chars>"
GMAIL_TOKEN_ENCRYPTION_KEY="<fernet key>"
GOOGLE_CLIENT_ID="..."
GOOGLE_CLIENT_SECRET="..."
GOOGLE_REDIRECT_URI="http://127.0.0.1:8000/api/v1/auth/google/callback"
# Login handoff: Google login ends on this app's /auth/callback page.
AUTH_FRONTEND_REDIRECT_URI="http://127.0.0.1:3000/auth/callback"
AUTH_CODE_EXPIRE_SECONDS=60
GMAIL_REDIRECT_URI="http://127.0.0.1:8000/api/v1/gmail/callback"
# Optional but needed for the /gmail/callback landing page:
GMAIL_FRONTEND_REDIRECT_URI="http://127.0.0.1:3000/gmail/callback"
```

`GMAIL_FRONTEND_REDIRECT_URI` is a backend configuration change. If you prefer
not to set it, the Gmail callback ends on the backend's JSON response instead;
`/gmail/status` still reports the connection correctly.

### Frontend

```bash
cd frontend
npm install
cp .env.example .env.local     # Windows: copy .env.example .env.local
npm run dev                    # http://127.0.0.1:3000
```

**Use `http://127.0.0.1:3000`, never `localhost:3000`.** `localhost` and
`127.0.0.1` are different cookie sites; mixing them loses the backend's signed
OAuth state cookie and Gmail authorization fails with `400`. The dev script
already binds `127.0.0.1`.

### Scripts

| Command             | Purpose                                       |
| ------------------- | --------------------------------------------- |
| `npm run dev`       | Dev server on `127.0.0.1:3000`                |
| `npm run build`     | Production build (type-checks and lints)      |
| `npm start`         | Serve the production build on `127.0.0.1:3000`|
| `npm run lint`      | ESLint (`eslint-config-next`, core-web-vitals)|
| `npm run typecheck` | `tsc --noEmit`                                |
| `npm test`          | Vitest (auth handoff, cookie flags, guards)   |

---

## 3. Authentication: one-time code handoff

Login is a backend-owned OAuth flow that ends on a dedicated frontend callback:

1. `GET /api/v1/auth/google/login` — `302` browser redirect to Google, with the
   signed `applypilot_oauth_session` state cookie.
2. `GET /api/v1/auth/google/callback` — validates the state, verifies the Google
   identity and creates/retrieves the user. It then `303`-redirects the browser
   to `AUTH_FRONTEND_REDIRECT_URI` (default
   `http://127.0.0.1:3000/auth/callback`) with a 256-bit, single-use code valid
   for at most 60 seconds. The backend stores only the code's SHA-256 hash.
3. `/auth/callback` — the dedicated Next.js page. It posts the code to
   `POST /api/auth/exchange`, whose server-side handler calls the backend's
   `POST /api/v1/auth/login/exchange`, receives the ApplyPilot JWT and writes it
   to the **HttpOnly** `ap_session` cookie. The browser is then sent to
   `/dashboard`.

Guarantees:

- The JWT never appears in a URL, `localStorage`, `sessionStorage`, the console
  or any response body frontend JavaScript can read. Only the server-side BFF
  sees it.
- Codes are random, short-lived, stored hashed, and consumed by one atomic
  `UPDATE`; a reused, expired or unknown code produces a clear login error.
- Failure redirects carry only a fixed identifier (`state`, `oauth`, `identity`,
  `email_unverified`, `account_exists`, `inactive`, `unavailable`);
  `/auth/callback` maps it to safe, user-facing copy and never renders the raw
  parameter.
- `ap_session` is `HttpOnly`, `SameSite=Lax`, `Path=/`, `Secure` in production,
  and its `Max-Age` never exceeds the JWT's own expiry.
- `AUTH_FRONTEND_REDIRECT_URI` is allow-listed against `BACKEND_CORS_ORIGINS`
  and is never taken from the request, so the callback cannot become an open
  redirect.

Other BFF routes: `GET /api/auth/session` reports `{ authenticated, user }`;
`POST /api/auth/logout` clears the cookie; `/api/backend/*` proxies only
`auth/me`, `gmail/status`, `gmail/glassdoor/jobs` and `gmail/disconnect`, with
`Authorization: Bearer` attached server-side. Mutations are same-origin checked;
anything else returns `404`. The `(app)` route-group layout validates the token
against `/auth/me` on every request and redirects to `/login` when it is missing
or rejected. Middleware is deliberately not used for this: Next normalises a
loopback dev origin to `localhost`, so an absolute redirect from middleware would
switch hosts and split the OAuth cookies. A backend `401` sends the browser to
`/login?expired=1`.

Backend settings used by this flow (see `backend/.env.example`):

```dotenv
AUTH_FRONTEND_REDIRECT_URI="http://127.0.0.1:3000/auth/callback"
AUTH_CODE_EXPIRE_SECONDS=60
```

---

## 4. Gmail integration

Endpoints used: `GET /gmail/connect`, `GET /gmail/status`,
`DELETE /gmail/disconnect`, `GET /gmail/glassdoor/jobs`.

- **Connect** goes through `POST /api/gmail/connect` because the JWT lives in the
  server-side cookie. The BFF calls the backend with the bearer header and
  relays the backend's `Set-Cookie` for `applypilot_oauth_session` verbatim, then
  the browser is navigated to the returned `authorization_url` with
  `window.location.assign`. Cookies ignore ports, so with both apps on
  `127.0.0.1` that state cookie reaches `127.0.0.1:8000/api/v1/gmail/callback`.
- **Production note:** with the API on `api.example.com` and the app on
  `app.example.com`, a relayed host-only cookie would not reach the API. Either
  scope it to the shared parent domain or expose the connect step as a
  browser navigation on the API itself, which then sets the cookie on its own
  host.
- **Callback**: the backend `303`s to `GMAIL_FRONTEND_REDIRECT_URI`
  (`/gmail/callback`), which shows the result by re-reading `/gmail/status`. It
  never receives or displays a token.
- **Status/disconnect** go through the proxy. Disconnect is confirmed in the UI;
  `token_revoked=false` is surfaced as a warning, per the backend contract.
- **Discovery** calls `gmail/glassdoor/jobs` with `days` (`7/30/60/90`) and
  `max_messages` (`10/25/50`), a 90 s client timeout, a disabled button while it
  runs, and mapped errors for `404 gmail_not_connected`, `409
  gmail_credentials_invalid`, `403 gmail_scope_missing`, `429 gmail_rate_limited`
  and `502 gmail_api_error`. A `409` also refreshes status so the UI offers the
  reconnect action.

Job links are only rendered when the URL parses to `http(s)`, so a malformed
`job_url` cannot become a `javascript:` link.

---

## 5. Dashboard

Navigation: Overview, Job Discovery, My Applications, Resume Manager, Gmail
Integration, AI Assistant, Settings.

Overview contains a welcome panel, statistics, recent job recommendations,
Gmail connection status, resume overview and recent activity.

**No backend data is invented.** Application counts, interview counts and resume
counts have no API in Phase 1 and render `—` with an explanation. Recent activity
is derived only from the stored Gmail connection timestamp and the latest
discovery result; with neither, an empty state is shown. Applications, Resume
Manager and AI Assistant are honest “Phase 2” empty states.

---

## 6. Structure

```text
src/
  app/
    (app)/                  # protected shell + pages
      dashboard/ jobs/ applications/ resumes/ gmail/ assistant/ settings/
      gmail/callback/
    login/
    auth/callback/          # dedicated login handoff page
    api/
      auth/session|logout/  # BFF session routes
      auth/exchange/        # one-time code -> HttpOnly cookie
      backend/[...path]/    # allow-listed proxy
      gmail/connect/        # relays the OAuth state cookie
    error.tsx global-error.tsx not-found.tsx
  components/
    layout/  ui/  dashboard/  gmail/  jobs/  settings/
  features/
    auth/  gmail/  jobs/
  lib/
    api/     # client, server, error envelope
    auth/    # session cookie, current user, login errors
    http/    # same-origin guard
    utils/   # cn, dates, URL safety
  providers/ types/
tests/       # Vitest: exchange route, cookie flags, route guards
```

All backend types live in `src/types/api.ts` and mirror the FastAPI schemas.

---

## 7. Verification (this build)

Run on Node 24 / npm 11:

- `npm test` — 16 Vitest checks: the exchange route sets an HttpOnly,
  SameSite=Lax cookie and never returns the JWT, refuses cross-origin and
  malformed codes, fails closed on a bad backend response, the cookie is
  `Secure` in production, and the `(app)` layout redirects unauthenticated
  requests to `/login` / `/login?expired=1`.
- `npm run lint` — clean (ESLint, `next/core-web-vitals` + `next/typescript`).
- `npm run typecheck` — clean (`strict`, `noUncheckedIndexedAccess`).
- `npm run build` — success; all app routes dynamic, middleware-free.
- Manual HTTP checks against a stub API implementing the documented contract:
  - `/dashboard`, `/jobs`, … without a session → `307` to
    `http://127.0.0.1:3000/login` (host-preserving).
  - `/api/auth/exchange` with a bad or replayed code → `400`; cross-site
    `Origin` → `403`; valid code → `200` + `Set-Cookie: ap_session=…; HttpOnly;
    SameSite=lax` and no token in the body.
  - `/api/backend/gmail/status`, `/gmail/glassdoor/jobs`,
    `/gmail/disconnect` → proxied with the bearer header; non-allow-listed path
    → `404`; cross-site `DELETE` → `403`.
  - `/api/gmail/connect` → returns `authorization_url` and relays the backend's
    `applypilot_oauth_session` cookie.
  - `/api/auth/logout` → expires the cookie.
  - Dashboard/Gmail/Settings pages render `200` with a valid session.

**Not verified:** a real Google consent flow, a real Gmail scan, and real
backend data — those depend on the Google credentials and backend configured in
section 2.

---

## 8. Known limitations

1. Logout clears the cookie but cannot revoke a stateless backend JWT; it stays
   valid until expiry.
2. Discovery results are not persisted by the backend, so nothing is shared
   across sessions or devices.
3. Applications, resumes and the AI assistant are placeholders, as scoped for
   Phase 1. Nothing is auto-applied.
4. Profile fields are read-only; they come from Google.
5. A login code is bound to the browser redirect, not to the originating
   browser: anyone who can read the callback URL within 60 seconds could use
   it, which is why it is single-use and never logged.
