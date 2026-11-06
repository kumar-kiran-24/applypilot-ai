---
noteId: "0aba7a50d61011f1b4504faf6f0394e5"
tags: []

---

# ApplyPilot AI - Backend

**ApplyPilot AI - Your Intelligent Job Application Agent**
*Discover. Analyze. Match. Apply.*

FastAPI backend for candidate profile management: profiles, education, work
experience, projects, job-search preferences and reusable application answers
with explicit verification states. Job discovery, job-description analysis,
resume selection (Google Drive) and authorized application automation are added
module by module on top of this foundation.

---

## 1. Project overview

The backend currently implements the **candidate domain** end to end:

- Asynchronous FastAPI service with versioned routing (`/api/v1`).
- PostgreSQL persistence through SQLAlchemy 2.0 async + asyncpg.
- Alembic migrations (async environment).
- Repository -> Service -> API layering: no SQL in route handlers.
- Pydantic v2 validation for every request and response.
- A framework-agnostic `BaseAgent` contract for future LangGraph nodes.
- Structured JSON logging with secret redaction, CORS, and safe error handling.

### API surface

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/health` | Liveness probe |
| POST | `/api/v1/candidates` | Create profile |
| GET | `/api/v1/candidates/{candidate_id}` | Get profile + nested records |
| PUT | `/api/v1/candidates/{candidate_id}` | Partial profile update |
| DELETE | `/api/v1/candidates/{candidate_id}` | Delete profile (cascades) |
| POST/GET | `/api/v1/candidates/{candidate_id}/education` | Add / list education |
| DELETE | `/api/v1/education/{education_id}` | Delete education |
| POST/GET | `/api/v1/candidates/{candidate_id}/experience` | Add / list experience |
| DELETE | `/api/v1/experience/{experience_id}` | Delete experience |
| POST/GET | `/api/v1/candidates/{candidate_id}/projects` | Add / list projects |
| DELETE | `/api/v1/projects/{project_id}` | Delete project |
| POST/GET/PUT | `/api/v1/candidates/{candidate_id}/preferences` | Job preferences |
| POST/GET | `/api/v1/candidates/{candidate_id}/answers` | Reusable answers |
| PUT | `/api/v1/candidates/{candidate_id}/answers/{question_key}` | Update / create answer |
| GET | `/api/v1/auth/google/login` | Start Google login |
| GET | `/api/v1/auth/google/callback` | Google login callback (redirects to the frontend with a one-time code) |
| POST | `/api/v1/auth/login/exchange` | Exchange the one-time login code for a JWT |
| GET | `/api/v1/auth/me` | Authenticated user |
| GET | `/api/v1/gmail/connect` | Start Gmail read-only authorization (JWT required) |
| GET | `/api/v1/gmail/callback` | Complete Gmail authorization |
| GET | `/api/v1/gmail/status` | Gmail connection status |
| DELETE | `/api/v1/gmail/disconnect` | Revoke and remove stored Gmail credentials |
| GET | `/api/v1/gmail/glassdoor/jobs` | Extract Glassdoor job emails (read-only) |

---

## 2. Architecture overview

```
HTTP request
    |
    v
app/api/v1/endpoints/*.py     validation (Pydantic) + HTTP status codes
    |
    v
app/services/*.py             business rules, duplicate/not-found handling,
    |                         transaction boundaries, verification semantics
    v
app/repositories/*.py         all SQL (SQLAlchemy 2.0 async queries)
    |
    v
app/models/*.py               ORM entities (UUID PKs, FKs, cascades, indexes)
    |
    v
PostgreSQL (asyncpg)
```

Cross-cutting concerns live in `app/core` (settings, logging, security,
exceptions) and `app/db` (engine, session, declarative base, dev bootstrap).

Design notes:

- **Aggregate ownership** - education, experience, projects, preferences and
  answers are owned by a candidate; database FKs use `ON DELETE CASCADE` and
  ORM relationships use `cascade="all, delete-orphan"`.
- **Answers are never guessed** - every answer carries
  `verification_status` in `{verified, derived, unknown}` plus `verified_at`.
  Only an explicit `verified` status sets `verified_at`; downgrading clears it.
- **Transactions** - services commit, repositories flush; the request-scoped
  session rolls back on exception.
- **No startup DDL** - tables are created by Alembic only. `app/db/init_db.py`
  exists for local development and is blocked in production.

---

## 3. Technology stack

| Component | Technology |
|---|---|
| Backend | FastAPI 0.121 |
| Language | Python 3.10 (existing `venv_rating` environment) |
| Validation | Pydantic v2 + pydantic-settings |
| Database | PostgreSQL 17 |
| ORM | SQLAlchemy 2.0 async (`asyncpg`) |
| Migrations | Alembic |
| Testing | Pytest + pytest-asyncio + httpx + aiosqlite |
| Code quality | Ruff (lint + format) |
| AI orchestration | LangGraph (foundation contract in `BaseAgent`) |
| LLM | Groq (`openai/gpt-oss-120b` by default) |
| Embeddings | Hugging Face (reserved) |
| Vector DB | Qdrant (reserved) |
| Cache | Redis (reserved) |
| Browser automation | Playwright (reserved) |

---

## 4. Folder structure

```text
backend/
|-- app/
|   |-- main.py                     # FastAPI app, lifespan, CORS, error handlers
|   |-- api/v1/
|   |   |-- router.py               # v1 aggregation
|   |   `-- endpoints/
|   |       |-- health.py
|   |       |-- auth.py             # Google login + /auth/me
|   |       |-- gmail.py            # Gmail connect/callback/status/disconnect
|   |       `-- candidates.py       # candidate + nested resource routes
|   |-- core/
|   |   |-- config.py               # typed settings + production validation
|   |   |-- exceptions.py           # domain error hierarchy
|   |   |-- logging.py              # JSON logs + redaction
|   |   `-- security.py             # JWT helpers (no placeholder auth)
|   |-- db/
|   |   |-- base.py                 # DeclarativeBase, mixins, naming convention
|   |   |-- session.py              # async engine, session factory, get_db
|   |   `-- init_db.py              # dev-only create/drop helpers
|   |-- models/                     # Candidate, Education, Experience, Project,
|   |                               # JobPreference, ApplicationAnswer
|   |-- schemas/                    # Pydantic v2 create/update/response models
|   |-- repositories/               # candidate_repository.py (all SQL)
|   |-- services/                   # candidate + Google/Gmail OAuth, Gmail API,
|   |                               # token encryption, Glassdoor extraction
|   |-- agents/
|   |   |-- base_agent.py           # abstract async agent contract
|   |   `-- job_analysis.py         # Groq-backed JD analysis agent
|   `-- integrations/
|       |-- llm/groq_client.py      # async Groq JSON client
|       |-- google_drive/           # reserved
|       `-- gmail/                  # reserved (see app/services/gmail_*.py)
|-- alembic/
|   |-- env.py                      # async migration environment
|   |-- script.py.mako
|   `-- versions/                   # generated revisions
|-- scripts/                        # operational helpers
|-- tests/
|   |-- conftest.py                 # in-memory SQLite + dependency override
|   |-- test_health.py
|   `-- test_candidates.py
|-- alembic.ini
|-- pyproject.toml                  # Ruff + Pytest configuration
|-- requirements.txt
`-- requirements-dev.txt
```

---

## 5. Prerequisites

- Windows with PowerShell.
- Conda (Miniconda/Anaconda).
- Existing Conda environment **`venv_rating`** (Python 3.10) - this project uses
  it as-is; do **not** create another environment.
- PostgreSQL 17 running locally with a database for this app.

```powershell
conda env list
conda activate venv_rating
python --version
python -m pip --version
```

---

## 6. Dependency installation

From the backend directory, with `venv_rating` activated:

```powershell
cd C:\Agentic_ai\applypilot-ai\backend
conda activate venv_rating
python -m pip install -r requirements.txt
python -m pip install -r requirements-dev.txt
```

`requirements.txt` pins the versions already present in `venv_rating`;
nothing is upgraded unnecessarily. In this environment the following were added
on top of the existing packages: `alembic`, `asyncpg`, `pytest-asyncio`, `ruff`
(and `redis`, reserved for the cache module).

---

## 7. `.env` configuration

```powershell
cd C:\Agentic_ai\applypilot-ai\backend
Copy-Item .env.example .env
```

Then edit `.env`:

```dotenv
ENVIRONMENT="development"
DEBUG=true

DATABASE_URL="postgresql+asyncpg://postgres:YOUR_PASSWORD@localhost:5432/applypilot"

JWT_SECRET_KEY="dev-insecure-jwt-secret-change-me"
OAUTH_SESSION_SECRET=""
OAUTH_SESSION_MAX_AGE_SECONDS=600
AUTH_FRONTEND_REDIRECT_URI="http://127.0.0.1:3000/auth/callback"
AUTH_CODE_EXPIRE_SECONDS=60
BACKEND_CORS_ORIGINS=["http://127.0.0.1:3000"]

GROQ_API_KEY=""
GOOGLE_CLIENT_ID=""
GOOGLE_CLIENT_SECRET=""
GOOGLE_REDIRECT_URI="http://127.0.0.1:8000/api/v1/auth/google/callback"
GMAIL_REDIRECT_URI="http://127.0.0.1:8000/api/v1/gmail/callback"
GMAIL_FRONTEND_REDIRECT_URI="http://127.0.0.1:3000/gmail/callback"
GMAIL_TOKEN_ENCRYPTION_KEY=""
GMAIL_API_TIMEOUT_SECONDS=30
GMAIL_MAX_RETRIES=2
QDRANT_URL="http://localhost:6333"
QDRANT_API_KEY=""
REDIS_URL="redis://localhost:6379/0"
```

Generate a real JWT secret when you leave development:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Generate the Gmail token-encryption key once and store it with the database
credentials:

```powershell
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

`GMAIL_TOKEN_ENCRYPTION_KEY` is a Fernet key (urlsafe-base64 of exactly 32
bytes). It encrypts Gmail refresh tokens at rest and must be different from
`JWT_SECRET_KEY` and `OAUTH_SESSION_SECRET`. The application never creates or
rotates it automatically: generate it once, back it up together with the
database, and keep `.env` out of version control. If the key is lost, every
stored refresh token becomes undecryptable and each user must reconnect Gmail;
rotation therefore means decrypting with the old key and re-encrypting with the
new one (or asking users to reconnect).

`ENVIRONMENT=production` refuses to boot when the JWT secret is the development
default or shorter than 32 characters, when `DEBUG=true`, when the default
database URL is used, when CORS is `*`, when `GMAIL_TOKEN_ENCRYPTION_KEY` is
missing, when `GMAIL_REDIRECT_URI` is missing or not `https`, when
`GMAIL_FRONTEND_REDIRECT_URI` is not `https` / its origin is not listed in
`BACKEND_CORS_ORIGINS`, or when `AUTH_FRONTEND_REDIRECT_URI` still points at
the local default, is not `https`, or its origin is not allow-listed. Groq,
Qdrant and Redis keys remain optional.

### Login handoff

The Google login callback never renders a token. After verifying the Google
identity the backend stores the SHA-256 hash of a 256-bit random code (60-second
expiry, single use) and `303`-redirects the browser to
`AUTH_FRONTEND_REDIRECT_URI` with `?code=...`. The frontend's server-side BFF
calls `POST /api/v1/auth/login/exchange` once, receives the JWT and stores it in
the HttpOnly `ap_session` cookie. Redemption is a single conditional `UPDATE`,
so a code cannot be replayed; invalid, expired and reused codes return `400`
with a safe message. The exchange endpoint is the only route that returns a JWT,
and it is never logged, placed in a URL or returned to browser JavaScript.

### Local host contract

Use **`http://127.0.0.1`** for the frontend, the backend and both OAuth
callbacks. `localhost` and `127.0.0.1` are different sites for cookies, and
mixing them means the signed OAuth state cookie set by `/gmail/connect` (or
`/auth/google/login`) is not sent to the callback. `GOOGLE_REDIRECT_URI` and
`GMAIL_REDIRECT_URI` are set explicitly to the registered `127.0.0.1:8000`
callbacks, and the frontend must call the connect endpoint with
`credentials: "include"` from its own `127.0.0.1:3000` origin, which is the only
entry allowed by `BACKEND_CORS_ORIGINS` locally. Serve the dev server on
`127.0.0.1`, not `localhost` (Next.js: `next dev -H 127.0.0.1`; Vite:
`vite --host 127.0.0.1`). The state cookie is `HttpOnly`, `SameSite=Lax`, lasts
`OAUTH_SESSION_MAX_AGE_SECONDS` (10 minutes by default) and is `Secure` outside
local development.

---

## 8. Database setup

Create the database (psql is on PATH with PostgreSQL 17):

```powershell
psql -U postgres -c "CREATE DATABASE applypilot;"
```

Verify connectivity through the app (prints `True`):

```powershell
python -c "import asyncio; from app.db.init_db import check_database_connection; print(asyncio.run(check_database_connection()))"
```

> The API starts even if PostgreSQL is unreachable - it logs a warning and keeps
> serving `/api/v1/health`. Candidate endpoints will then return `500` until the
> database is configured.
>
> Local setup done: database `applypilot` created, `alembic upgrade head` applied
> (7 tables incl. `alembic_version`), and the full CRUD flow exercised against it.
> `.env` holds the local credentials and is git-ignored.

---

## 9. Alembic migrations

Alembic reads `DATABASE_URL` from the application settings, so no credentials
live in `alembic.ini`.

The initial schema revision is already committed at
`alembic/versions/68cb6cf67ee7_initial_candidate_schema.py` (candidates,
education, experiences, projects, job_preferences, application_answers).

```powershell
# apply the committed migration (already applied in the local database)
alembic upgrade head

# after changing models, create a new revision and review it before committing
alembic revision --autogenerate -m "describe the change"

# inspect / roll back
alembic current
alembic history
alembic downgrade -1
```

Never edit an applied revision - add a new one instead.

---

## 10. Running the backend

```powershell
cd C:\Agentic_ai\applypilot-ai\backend
conda activate venv_rating
uvicorn app.main:app --reload
```

- Swagger UI: <http://127.0.0.1:8000/docs>
- ReDoc: <http://127.0.0.1:8000/redoc>
- OpenAPI JSON: <http://127.0.0.1:8000/openapi.json>
- Health: <http://127.0.0.1:8000/api/v1/health>

Use `127.0.0.1` consistently with the frontend and the registered OAuth redirect
URIs; see "Local host contract" in section 7.

```json
{ "status": "healthy", "service": "ApplyPilot AI Backend", "version": "0.1.0" }
```

Binding to another host/port:

```powershell
uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload
```

Logs are single-line JSON on stdout; credential-like fields are redacted.

---

## 11. Running tests

Tests use an in-memory SQLite database and FastAPI dependency overrides, so no
PostgreSQL instance and no API keys are required.

```powershell
cd C:\Agentic_ai\applypilot-ai\backend
conda activate venv_rating
pytest
pytest -v
pytest tests/test_candidates.py -k education
```

Coverage of the suite: health, candidate creation/retrieval/update/delete,
duplicate email rejection, input validation, education, experience, projects,
preferences, and application-answer creation/update/verification rules, plus the
job-description analysis agent (prompt construction, JSON extraction, schema
validation, repair behaviour and failure paths).

The Gmail suite (`tests/test_gmail_*.py`, `tests/test_glassdoor_jobs.py`) mocks
every Google call: Fernet encryption and key failures, OAuth state/session
binding, different-account and unverified-email rejection, missing refresh
tokens, duplicate connection handling, status/disconnect, Gmail API pagination,
rate limits, revoked credentials and Glassdoor extraction/deduplication.

The suite also contains one **real Groq API test**,
`tests/test_job_analysis_agent.py::test_live_groq_job_description_analysis`.
It calls the live API whenever `GROQ_API_KEY` is configured and skips (with the
reason) when the key is missing or rejected:

```powershell
pytest -m live -v          # show the live test and its skip reason
$env:GROQ_API_KEY = "gsk_..."   # then re-run to execute it for real
pytest -m live -v
```

Environment variables take precedence over `.env`, so a stale exported
`GROQ_API_KEY` will shadow the value in `.env`.

---

## 12. Linting and formatting

```powershell
ruff check .
ruff format --check .
ruff format .
```

Configuration lives in `pyproject.toml` (target `py310`, line length 100).

---

## 13. Security and reliability notes

- Settings are centralised and validated; no credentials are committed.
- CORS origins are explicit and environment-driven.
- SQL injection is prevented by SQLAlchemy parameterised queries.
- Errors return a stable envelope (`detail`, `code`, optional `details`) without
  stack traces; unexpected errors are logged server-side only.
- Secrets are never logged (`app/core/logging.py` redacts sensitive keys).
- No password column exists; when accounts arrive, only hashes may be stored.
- `JWT_SECRET_KEY` is required to be strong in production; tokens are HS256.
- No placeholder authentication is wired into endpoints.
- Application answers distinguish `verified`, `derived` and `unknown`;
  sensitive questions and legal declarations must be confirmed by the user.
- Future automation will never bypass CAPTCHA, MFA, rate limits or platform
  anti-bot protections, and only performs applications the user authorized.
- Outbound URL fetching is not implemented; future fetches must be allow-listed.

---

## 14. Job-description analysis agent

`app/agents/job_analysis.py` implements the first real agent on top of
`BaseAgent`. It sends the posting to Groq in JSON mode, validates the response
against a Pydantic schema, and retries exactly once with the validation error if
the model returns unusable JSON.

```python
from app.agents.job_analysis import (
    JobDescriptionAnalysisAgent,
    JobDescriptionAnalysisInput,
)

agent = JobDescriptionAnalysisAgent()  # uses GroqLLMClient + settings
result = await agent.run(JobDescriptionAnalysisInput(job_description=posting_text, company="Acme"))
analysis = result.output  # JobDescriptionAnalysisOutput
result.metadata.status  # AgentStatus.SUCCEEDED / FAILED
result.metadata.duration_ms
```

Output fields: `role_title`, `company`, `seniority`, `location`, `work_mode`,
`employment_type`, `min_experience_years`, `max_experience_years`,
`salary_range_text`, `required_skills`, `preferred_skills`, `responsibilities`,
`qualifications`, `application_requirements`, `ats_keywords`, `red_flags` and
`summary`.

Safety properties:

- The posting is wrapped in `<job_description>` tags and declared untrusted;
the system prompt tells the model to ignore instructions inside it (prompt
injection defence).
- Missing facts must be `null` / `[]` - the prompt forbids invention, and
`salary_range_text` is copied verbatim rather than parsed into numbers.
- No candidate data is sent, and the prompt forbids inferring personal or
sensitive characteristics.
- Output is schema-validated; lists are stripped, deduplicated and capped; ATS
keywords are lowercased. Unusable output triggers one repair call, then a
wrapped `AgentExecutionError`.
- `GroqLLMClient` resolves the API key lazily, retries rate limits, connection
errors and 5xx responses with backoff, and maps a rejected key to
`LLMConfigurationError`. Prompts and keys are never logged.

Configuration (all optional except the key):

```dotenv
GROQ_API_KEY="gsk_..."
GROQ_MODEL="openai/gpt-oss-120b"   # or openai/gpt-oss-20b
GROQ_TIMEOUT_SECONDS=30
GROQ_MAX_RETRIES=2
GROQ_MAX_COMPLETION_TOKENS=4096
GROQ_TEMPERATURE=0.0
```

## 15. Gmail integration

ApplyPilot can read job-related email through a separate, incremental OAuth flow
that never touches Google login.

### Flow

1. `GET /api/v1/gmail/connect` (bearer token required) creates an OAuth state in
the signed session cookie, binds it to the authenticated user id and returns
Google's consent URL. Requested scopes are `openid email profile` plus
`https://www.googleapis.com/auth/gmail.readonly`, with `access_type=offline`,
`include_granted_scopes=true` and `prompt=select_account consent` (account
selection plus a reliable refresh token). Open the returned URL in a browser.
2. `GET /api/v1/gmail/callback` validates that state, exchanges the code, checks
the Google `sub` equals the ApplyPilot user's `google_sub`, requires a verified
email and the Gmail scope, then stores the refresh token encrypted. A different
Google account is rejected; no second ApplyPilot user is ever created and no
token is returned. When `GMAIL_FRONTEND_REDIRECT_URI` is set, the browser is
redirected (`303`) to that server-configured frontend route; otherwise the JSON
connection payload is returned. The redirect target is never read from the
request.
3. `GET /api/v1/gmail/status` reports `connected`, the Gmail address, granted
scopes and the connection timestamp.
4. `DELETE /api/v1/gmail/disconnect` revokes the token at Google (failure is
reported in `token_revoked` but never blocks the disconnect) and deletes the
stored credential.
5. `GET /api/v1/gmail/glassdoor/jobs?days=30&max_messages=25` refreshes a
short-lived access token, searches ``from:glassdoor.com newer_than:<days>d``,
extracts job recommendations and returns them without persisting anything.
Duplicate postings (same Glassdoor job id or title/company/location) are dropped.

### Security properties

- Refresh tokens are encrypted with Fernet (`GMAIL_TOKEN_ENCRYPTION_KEY`) before
  storage; access tokens stay in memory for one call.
- The user id is never read from the callback query string - it comes from the
  signed session cookie that `/gmail/connect` wrote.
- Attachments are skipped (`attachmentId` parts are never decoded) and response
  bodies are capped.
- Gmail `nextPageToken` is followed with a hard cap; expired/revoked
  credentials, missing scopes, 429s and API errors map to distinct errors with
  safe messages.
- Glassdoor is never scraped and CAPTCHA/MFA/anti-bot protections are never
  bypassed: only emails the user already received are parsed.

### Frontend contract

The SPA obtains its ApplyPilot JWT from Google login, then:

```js
// credentials: "include" stores the signed OAuth state cookie
const response = await fetch("http://127.0.0.1:8000/api/v1/gmail/connect", {
  headers: { Authorization: `Bearer ${jwt}` },
  credentials: "include",
});
const { authorization_url } = await response.json();
window.location.assign(authorization_url);  // Google consent
```

After consent, the callback redirects to `GMAIL_FRONTEND_REDIRECT_URI`. The SPA
then calls `GET /api/v1/gmail/status` with the JWT to show the connected address.
Do not proxy the connect call through a different host: the cookie must belong to
the API host and be sent back by the browser after Google's top-level redirect.

### Google Cloud Console

Register the Gmail callback URL (e.g.
`http://127.0.0.1:8000/api/v1/gmail/callback`) in the same OAuth client as the
login callback, and keep `gmail.readonly` enabled for the project. Register
the exact same host (`http://127.0.0.1:8000`) for both callbacks so the OAuth
state cookie is sent back.

### Tests

```powershell
pytest tests/test_gmail_token_encryption.py tests/test_gmail_service.py -q
pytest tests/test_gmail_api.py tests/test_glassdoor_jobs.py -q
```

## 16. Roadmap

1. ~~Job description analysis agent~~ - implemented; next: expose it via an API
   endpoint and persist analyses.
2. Resume inventory from Google Drive and embedding-based matching (Hugging Face
   + Qdrant).
3. Job discovery and fit scoring.
4. Application execution agent using Playwright with explicit user
   authorization and per-application confirmation.
5. Authentication (OAuth2/JWT) for multi-user access, plus Redis-backed cache
   and rate limiting.
