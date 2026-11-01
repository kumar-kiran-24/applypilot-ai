"""Job-description analysis agent tests.

Two layers:

* Deterministic tests that stub the *transport* only: they prove prompt
  construction, JSON extraction, schema validation, repair and failure
  behaviour without calling a model.
* One real API-backed test that calls Groq end to end. It runs when
  ``GROQ_API_KEY`` is configured and is skipped otherwise - it is never mocked.
"""

from __future__ import annotations

import json

import pytest
from app.agents.base_agent import AgentExecutionError, AgentStatus
from app.agents.job_analysis import (
    AgentOutputError,
    JobDescriptionAnalysisAgent,
    JobDescriptionAnalysisInput,
    JobDescriptionAnalysisOutput,
    SeniorityLevel,
)
from app.core.config import get_settings
from app.integrations.llm.groq_client import GroqLLMClient, LLMConfigurationError
from pydantic import ValidationError

SAMPLE_JD = """
Backend Engineer (Python) - Northwind Pay

We are hiring a backend engineer to build payment APIs.

Requirements:
- 3+ years of professional Python experience
- Strong knowledge of FastAPI and PostgreSQL
- Experience designing REST APIs and working with Docker
- Familiarity with asynchronous programming

Nice to have:
- Redis, Celery, or Kafka experience
- Exposure to financial systems

Location: Berlin. Hybrid: 3 days onsite per week.
Salary: EUR 70,000 - 90,000 per year.
"""

VALID_MODEL_RESPONSE = {
    "role_title": "Backend Engineer (Python)",
    "company": "Northwind Pay",
    "seniority": "mid",
    "location": "Berlin",
    "work_mode": "hybrid",
    "employment_type": "full_time",
    "min_experience_years": 3,
    "max_experience_years": None,
    "salary_range_text": "EUR 70,000 - 90,000 per year",
    "required_skills": ["Python", "FastAPI", "PostgreSQL", "REST APIs", "Docker"],
    "preferred_skills": ["Redis", "Celery", "Kafka"],
    "responsibilities": ["Build payment APIs"],
    "qualifications": ["3+ years of Python experience"],
    "application_requirements": [],
    "ats_keywords": ["python", "fastapi", "postgresql", "rest", "docker"],
    "red_flags": [],
    "summary": (
        "Northwind Pay is hiring a backend engineer to build payment APIs with "
        "Python, FastAPI and PostgreSQL. The role is hybrid in Berlin and asks "
        "for at least three years of experience."
    ),
}


class StubLLM:
    """Test double for the transport layer only (never shipped)."""

    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, str]] = []

    async def complete_json(self, *, system: str, user: str) -> str:
        self.calls.append({"system": system, "user": user})
        if not self.responses:
            raise AssertionError("StubLLM received more calls than expected")
        return self.responses.pop(0)


def _payload() -> JobDescriptionAnalysisInput:
    return JobDescriptionAnalysisInput(
        job_description=SAMPLE_JD,
        job_title="Backend Engineer (Python)",
        company="Northwind Pay",
    )


# ---------------------------------------------------------------------------
# Deterministic behaviour
# ---------------------------------------------------------------------------
async def test_agent_returns_validated_output() -> None:
    llm = StubLLM([json.dumps(VALID_MODEL_RESPONSE)])
    agent = JobDescriptionAnalysisAgent(llm=llm)

    result = await agent.run(_payload())

    assert result.metadata.status is AgentStatus.SUCCEEDED
    assert result.metadata.agent_name == "job_description_analysis"
    output = result.output
    assert isinstance(output, JobDescriptionAnalysisOutput)
    assert output.role_title == "Backend Engineer (Python)"
    assert "Python" in output.required_skills
    assert output.work_mode.value == "hybrid"
    assert output.seniority is SeniorityLevel.MID
    assert len(llm.calls) == 1


async def test_prompt_marks_the_posting_as_untrusted_data() -> None:
    llm = StubLLM([json.dumps(VALID_MODEL_RESPONSE)])
    agent = JobDescriptionAnalysisAgent(llm=llm)

    await agent.run(_payload())

    system_prompt = llm.calls[0]["system"]
    user_prompt = llm.calls[0]["user"]
    assert "untrusted" in system_prompt.lower()
    assert "ignore" in system_prompt.lower()
    assert "<job_description>" in user_prompt
    assert "Python" in user_prompt
    assert "Northwind Pay" in user_prompt


async def test_agent_repairs_one_unparseable_response() -> None:
    llm = StubLLM(["this is not json", json.dumps(VALID_MODEL_RESPONSE)])
    agent = JobDescriptionAnalysisAgent(llm=llm)

    result = await agent.run(_payload())

    assert len(llm.calls) == 2
    assert "<previous_response_problem>" in llm.calls[1]["user"]
    assert result.output.required_skills


async def test_agent_accepts_json_wrapped_in_code_fences() -> None:
    llm = StubLLM([f"```json\n{json.dumps(VALID_MODEL_RESPONSE)}\n```"])
    agent = JobDescriptionAnalysisAgent(llm=llm)

    result = await agent.run(_payload())

    assert result.output.company == "Northwind Pay"
    assert len(llm.calls) == 1


async def test_agent_fails_when_repair_also_fails() -> None:
    llm = StubLLM(["not json", "still not json"])
    agent = JobDescriptionAnalysisAgent(llm=llm)

    with pytest.raises(AgentExecutionError) as exc_info:
        await agent.run(_payload())

    assert "job_description_analysis" in str(exc_info.value)
    assert len(llm.calls) == 2


async def test_agent_rejects_schema_violation_after_repair() -> None:
    invalid = dict(VALID_MODEL_RESPONSE, min_experience_years=10, max_experience_years=2)
    llm = StubLLM([json.dumps(invalid), json.dumps(invalid)])
    agent = JobDescriptionAnalysisAgent(llm=llm)

    with pytest.raises(AgentExecutionError):
        await agent.run(_payload())


def test_output_normalises_and_dedupes_lists() -> None:
    output = JobDescriptionAnalysisOutput.model_validate(
        dict(
            VALID_MODEL_RESPONSE,
            required_skills=[" Python ", "python", "FastAPI", "", "x" * 500],
            ats_keywords=["Python", "PYTHON", "FastAPI"],
        )
    )

    assert output.required_skills == ["Python", "FastAPI"]
    assert output.ats_keywords == ["python", "fastapi"]


def test_output_rejects_inverted_experience_range() -> None:
    with pytest.raises(ValidationError):
        JobDescriptionAnalysisOutput.model_validate(
            dict(VALID_MODEL_RESPONSE, min_experience_years=8, max_experience_years=2)
        )


def test_input_rejects_too_short_description() -> None:
    with pytest.raises(ValidationError):
        JobDescriptionAnalysisInput(job_description="Too short")


def test_parse_output_rejects_non_object_json() -> None:
    with pytest.raises(AgentOutputError):
        JobDescriptionAnalysisAgent.parse_output("[1, 2, 3]")


async def test_missing_api_key_raises_configuration_error() -> None:
    client = GroqLLMClient(api_key="")

    assert client.is_configured is False

    with pytest.raises(LLMConfigurationError):
        # No network call happens: the client refuses before sending.
        await client.complete_json(system="s", user="u")


# ---------------------------------------------------------------------------
# Real Groq API test (never mocked; skipped without credentials)
# ---------------------------------------------------------------------------
@pytest.mark.live
@pytest.mark.skipif(
    not get_settings().GROQ_API_KEY,
    reason="GROQ_API_KEY is not configured; live Groq test skipped",
)
async def test_live_groq_job_description_analysis() -> None:
    agent = JobDescriptionAnalysisAgent()

    try:
        result = await agent.run(_payload(), context={"test": "live"})
    except AgentExecutionError as exc:
        # A rejected key is a credential problem, not a code regression: skip
        # with the upstream reason so the suite stays honest and runnable.
        if isinstance(exc.cause, LLMConfigurationError):
            pytest.skip(f"Groq rejected the configured GROQ_API_KEY: {exc.cause}")
        raise

    assert result.metadata.status is AgentStatus.SUCCEEDED
    output = result.output

    # The model must ground its output in the posting it was given.
    assert output.company is not None
    assert output.company.casefold() == "northwind pay"
    assert any("python" in skill.lower() for skill in output.required_skills)
    assert output.ats_keywords
    assert all(keyword == keyword.lower() for keyword in output.ats_keywords)
    assert output.min_experience_years is not None
    assert output.min_experience_years >= 3
    assert output.work_mode is not None
    assert output.work_mode.value == "hybrid"
    assert len(output.summary) >= 20
