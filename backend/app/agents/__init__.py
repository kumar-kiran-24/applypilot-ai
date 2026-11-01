"""Agent package.

``BaseAgent`` is the reusable contract; ``JobDescriptionAnalysisAgent`` is the
first concrete agent, backed by the real Groq API.
"""

from app.agents.base_agent import (
    AgentExecutionError,
    AgentExecutionMetadata,
    AgentInput,
    AgentOutput,
    AgentResult,
    AgentStatus,
    BaseAgent,
)
from app.agents.job_analysis import (
    AgentOutputError,
    JobDescriptionAnalysisAgent,
    JobDescriptionAnalysisInput,
    JobDescriptionAnalysisOutput,
    SeniorityLevel,
    StructuredLLM,
)

__all__ = [
    "AgentExecutionError",
    "AgentExecutionMetadata",
    "AgentInput",
    "AgentOutput",
    "AgentOutputError",
    "AgentResult",
    "AgentStatus",
    "BaseAgent",
    "JobDescriptionAnalysisAgent",
    "JobDescriptionAnalysisInput",
    "JobDescriptionAnalysisOutput",
    "SeniorityLevel",
    "StructuredLLM",
]
