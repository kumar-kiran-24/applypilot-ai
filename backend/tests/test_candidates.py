"""End-to-end API tests for candidate profiles and nested records."""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient

from tests.conftest import create_candidate

BASE = "/api/v1/candidates"


# ---------------------------------------------------------------------------
# Candidate profile
# ---------------------------------------------------------------------------
async def test_create_candidate(client: AsyncClient, candidate_payload: dict[str, Any]) -> None:
    response = await client.post(BASE, json=candidate_payload)

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["id"]
    assert body["email"] == "ada@example.com"  # normalised
    assert body["full_name"] == "Ada Lovelace"
    assert body["created_at"] and body["updated_at"]


async def test_get_candidate_returns_nested_collections(
    client: AsyncClient, candidate_payload: dict[str, Any]
) -> None:
    created = await create_candidate(client, candidate_payload)

    response = await client.get(f"{BASE}/{created['id']}")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == created["id"]
    assert body["education"] == []
    assert body["experiences"] == []
    assert body["projects"] == []
    assert body["application_answers"] == []
    assert body["preference"] is None


async def test_get_unknown_candidate_returns_404(client: AsyncClient) -> None:
    response = await client.get(f"{BASE}/00000000-0000-0000-0000-000000000000")

    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


async def test_update_candidate(client: AsyncClient, candidate_payload: dict[str, Any]) -> None:
    created = await create_candidate(client, candidate_payload)

    response = await client.put(
        f"{BASE}/{created['id']}",
        json={"location": "Berlin, DE", "professional_summary": "Updated summary."},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["location"] == "Berlin, DE"
    assert body["professional_summary"] == "Updated summary."
    assert body["email"] == "ada@example.com"


async def test_duplicate_email_is_rejected(
    client: AsyncClient, candidate_payload: dict[str, Any]
) -> None:
    await create_candidate(client, candidate_payload)

    duplicate = dict(candidate_payload, email="ADA@example.com", full_name="Another Ada")
    response = await client.post(BASE, json=duplicate)

    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "duplicate_email"
    assert "already exists" in body["detail"]


async def test_update_to_existing_email_is_rejected(
    client: AsyncClient, candidate_payload: dict[str, Any]
) -> None:
    first = await create_candidate(client, candidate_payload)
    second = await create_candidate(
        client, dict(candidate_payload, email="grace@example.com", full_name="Grace Hopper")
    )

    response = await client.put(f"{BASE}/{second['id']}", json={"email": first["email"]})

    assert response.status_code == 409
    assert response.json()["code"] == "duplicate_email"


async def test_invalid_input_is_rejected(client: AsyncClient) -> None:
    bad_email = await client.post(BASE, json={"full_name": "No Email", "email": "not-an-email"})
    assert bad_email.status_code == 422
    assert bad_email.json()["code"] == "validation_error"

    missing_name = await client.post(BASE, json={"email": "valid@example.com"})
    assert missing_name.status_code == 422

    bad_url = await client.post(
        BASE, json={"full_name": "X", "email": "x@example.com", "github_url": "not-a-url"}
    )
    assert bad_url.status_code == 422


async def test_delete_candidate(client: AsyncClient, candidate_payload: dict[str, Any]) -> None:
    created = await create_candidate(client, candidate_payload)

    response = await client.delete(f"{BASE}/{created['id']}")
    assert response.status_code == 200
    assert response.json() == {"id": created["id"], "deleted": True}

    assert (await client.get(f"{BASE}/{created['id']}")).status_code == 404


# ---------------------------------------------------------------------------
# Education
# ---------------------------------------------------------------------------
async def test_education_lifecycle(client: AsyncClient, candidate_payload: dict[str, Any]) -> None:
    candidate = await create_candidate(client, candidate_payload)

    created = await client.post(
        f"{BASE}/{candidate['id']}/education",
        json={
            "institution": "University of London",
            "degree": "BSc",
            "specialization": "Computer Science",
            "start_date": "2018-09-01",
            "end_date": "2022-06-30",
            "cgpa": 8.7,
        },
    )
    assert created.status_code == 201, created.text
    education = created.json()
    assert education["candidate_id"] == candidate["id"]

    listed = await client.get(f"{BASE}/{candidate['id']}/education")
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [education["id"]]

    detail = await client.get(f"{BASE}/{candidate['id']}")
    assert len(detail.json()["education"]) == 1

    deleted = await client.delete(f"/api/v1/education/{education['id']}")
    assert deleted.status_code == 200
    assert (await client.get(f"{BASE}/{candidate['id']}/education")).json() == []


async def test_education_rejects_inverted_dates(
    client: AsyncClient, candidate_payload: dict[str, Any]
) -> None:
    candidate = await create_candidate(client, candidate_payload)

    response = await client.post(
        f"{BASE}/{candidate['id']}/education",
        json={
            "institution": "University",
            "degree": "MSc",
            "start_date": "2022-01-01",
            "end_date": "2021-01-01",
        },
    )

    assert response.status_code == 422


async def test_education_for_unknown_candidate_returns_404(client: AsyncClient) -> None:
    response = await client.post(
        f"{BASE}/00000000-0000-0000-0000-000000000000/education",
        json={"institution": "University", "degree": "BSc"},
    )

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Experience
# ---------------------------------------------------------------------------
async def test_experience_creation(client: AsyncClient, candidate_payload: dict[str, Any]) -> None:
    candidate = await create_candidate(client, candidate_payload)

    response = await client.post(
        f"{BASE}/{candidate['id']}/experience",
        json={
            "company": "Acme Corp",
            "designation": "Backend Engineer",
            "employment_type": "full_time",
            "location": "Remote",
            "start_date": "2022-07-01",
            "is_current": True,
            "technologies": ["Python", "FastAPI", "PostgreSQL"],
        },
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["company"] == "Acme Corp"
    assert body["employment_type"] == "full_time"
    assert body["technologies"] == ["Python", "FastAPI", "PostgreSQL"]

    listed = await client.get(f"{BASE}/{candidate['id']}/experience")
    assert len(listed.json()) == 1


async def test_experience_rejects_end_date_when_current(
    client: AsyncClient, candidate_payload: dict[str, Any]
) -> None:
    candidate = await create_candidate(client, candidate_payload)

    response = await client.post(
        f"{BASE}/{candidate['id']}/experience",
        json={
            "company": "Acme",
            "designation": "Engineer",
            "is_current": True,
            "end_date": "2024-01-01",
        },
    )

    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------
async def test_project_creation(client: AsyncClient, candidate_payload: dict[str, Any]) -> None:
    candidate = await create_candidate(client, candidate_payload)

    response = await client.post(
        f"{BASE}/{candidate['id']}/projects",
        json={
            "name": "ApplyPilot",
            "description": "Job application agent",
            "technologies": ["FastAPI", "LangGraph"],
            "project_url": "https://github.com/ada/applypilot",
        },
    )

    assert response.status_code == 201, response.text
    project = response.json()
    assert project["name"] == "ApplyPilot"

    listed = await client.get(f"{BASE}/{candidate['id']}/projects")
    assert len(listed.json()) == 1

    deleted = await client.delete(f"/api/v1/projects/{project['id']}")
    assert deleted.status_code == 200


# ---------------------------------------------------------------------------
# Preferences
# ---------------------------------------------------------------------------
async def test_preference_creation_and_update(
    client: AsyncClient, candidate_payload: dict[str, Any]
) -> None:
    candidate = await create_candidate(client, candidate_payload)

    created = await client.post(
        f"{BASE}/{candidate['id']}/preferences",
        json={
            "preferred_roles": ["Backend Engineer", "AI Engineer"],
            "preferred_locations": ["Berlin", "Remote"],
            "preferred_job_types": ["full_time", "contract"],
            "preferred_work_modes": ["remote", "hybrid"],
            "relocation_preference": "negotiable",
            "minimum_salary": 60000,
            "expected_salary": 85000,
            "currency": "eur",
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["preferred_job_types"] == ["full_time", "contract"]
    assert body["preferred_work_modes"] == ["remote", "hybrid"]
    assert body["currency"] == "EUR"

    duplicate = await client.post(f"{BASE}/{candidate['id']}/preferences", json={})
    assert duplicate.status_code == 409

    updated = await client.put(
        f"{BASE}/{candidate['id']}/preferences",
        json={"preferred_work_modes": ["remote"], "expected_salary": 95000},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["preferred_work_modes"] == ["remote"]
    assert updated.json()["expected_salary"] == "95000.00"

    fetched = await client.get(f"{BASE}/{candidate['id']}/preferences")
    assert fetched.status_code == 200
    assert fetched.json()["expected_salary"] == "95000.00"


async def test_preference_missing_returns_404(
    client: AsyncClient, candidate_payload: dict[str, Any]
) -> None:
    candidate = await create_candidate(client, candidate_payload)

    response = await client.get(f"{BASE}/{candidate['id']}/preferences")

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Application answers
# ---------------------------------------------------------------------------
async def test_answer_creation_and_update(
    client: AsyncClient, candidate_payload: dict[str, Any]
) -> None:
    candidate = await create_candidate(client, candidate_payload)

    created = await client.post(
        f"{BASE}/{candidate['id']}/answers",
        json={
            "question_key": "work_authorization",
            "question_text": "Are you authorised to work in the EU?",
            "answer": "Yes",
            "answer_type": "boolean",
        },
    )
    assert created.status_code == 201, created.text
    answer = created.json()
    assert answer["verification_status"] == "unknown"
    assert answer["verified_at"] is None

    updated = await client.put(
        f"{BASE}/{candidate['id']}/answers/work_authorization",
        json={"answer": "Yes, EU citizen", "verification_status": "verified"},
    )
    assert updated.status_code == 200, updated.text
    body = updated.json()
    assert body["answer"] == "Yes, EU citizen"
    assert body["verification_status"] == "verified"
    assert body["verified_at"] is not None

    reverted = await client.put(
        f"{BASE}/{candidate['id']}/answers/work_authorization",
        json={"verification_status": "unknown"},
    )
    assert reverted.status_code == 200
    assert reverted.json()["verified_at"] is None

    listed = await client.get(f"{BASE}/{candidate['id']}/answers")
    assert len(listed.json()) == 1


async def test_answer_requires_value_when_verified(
    client: AsyncClient, candidate_payload: dict[str, Any]
) -> None:
    candidate = await create_candidate(client, candidate_payload)

    response = await client.post(
        f"{BASE}/{candidate['id']}/answers",
        json={
            "question_key": "salary_expectation",
            "question_text": "What is your expected salary?",
            "verification_status": "verified",
        },
    )

    assert response.status_code == 422


async def test_duplicate_answer_key_is_rejected(
    client: AsyncClient, candidate_payload: dict[str, Any]
) -> None:
    candidate = await create_candidate(client, candidate_payload)
    payload = {
        "question_key": "notice_period",
        "question_text": "What is your notice period?",
        "answer": "30 days",
    }
    assert (await client.post(f"{BASE}/{candidate['id']}/answers", json=payload)).status_code == 201

    duplicate = await client.post(f"{BASE}/{candidate['id']}/answers", json=payload)

    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "duplicate_answer"


async def test_answer_upsert_creates_when_missing(
    client: AsyncClient, candidate_payload: dict[str, Any]
) -> None:
    candidate = await create_candidate(client, candidate_payload)

    response = await client.put(
        f"{BASE}/{candidate['id']}/answers/relocation",
        json={"question_text": "Are you willing to relocate?", "answer": "Yes"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["question_key"] == "relocation"


async def test_answers_for_unknown_candidate_returns_404(client: AsyncClient) -> None:
    response = await client.get(f"{BASE}/00000000-0000-0000-0000-000000000000/answers")

    assert response.status_code == 404
