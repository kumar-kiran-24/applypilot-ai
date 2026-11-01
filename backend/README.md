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
|   |-- services/                   # candidate_service.py (business rules)
|   |-- agents/
|   |   |-- base_agent.py           # abstract async agent contract
|   |   `-- job_analysis.py         # Groq-backed JD analysis agent
|   `-- integrations/
|       |-- llm/groq_client.py      # async Groq JSON client
|       |-- google_drive/           # reserved
|       `-- gmail/                  # reserved
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
BACKEND_CORS_ORIGINS=["http://localhost:3000","http://127.0.0.1:3000"]

GROQ_API_KEY=""
GOOGLE_CLIENT_ID=""
GOOGLE_CLIENT_SECRET=""
GOOGLE_REDIRECT_URI=""
QDRANT_URL="http://localhost:6333"
QDRANT_API_KEY=""
REDIS_URL="redis://localhost:6379/0"
```

Generate a real JWT secret when you leave development:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

`ENVIRONMENT=production` refuses to boot when the JWT secret is the development
default or shorter than 32 characters, when `DEBUG=true`, when the default
database URL is used, or when CORS is `*`. API keys for Groq, Google, Qdrant and
Redis are **optional** at this stage - no module requires them yet.

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

## 15. Roadmap

1. ~~Job description analysis agent~~ - implemented; next: expose it via an API
   endpoint and persist analyses.
2. Resume inventory from Google Drive and embedding-based matching (Hugging Face
   + Qdrant).
3. Job discovery and fit scoring.
4. Application execution agent using Playwright with explicit user
   authorization and per-application confirmation.
5. Authentication (OAuth2/JWT) for multi-user access, plus Redis-backed cache
   and rate limiting.
