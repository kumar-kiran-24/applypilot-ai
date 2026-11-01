"""Reusable base class for ApplyPilot AI agents.

The contract is deliberately framework-agnostic: a single async ``execute`` that
takes a validated input model and returns a validated output model, plus
execution metadata. That maps directly onto a LangGraph node later on, without
rewriting any agent.

No agent in this module fabricates results. Concrete agents must be implemented
against real integrations (LLM, Drive, browser automation) before use.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, ClassVar, Generic, TypeVar

from pydantic import BaseModel, ConfigDict

from app.core.logging import get_logger


class AgentStatus(str, Enum):
    """Terminal state of an agent invocation."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"


class AgentInput(BaseModel):
    """Base class for structured agent input."""

    model_config = ConfigDict(extra="forbid")


class AgentOutput(BaseModel):
    """Base class for structured agent output."""

    model_config = ConfigDict(extra="forbid")


@dataclass(slots=True)
class AgentExecutionMetadata:
    """Non-functional details about one agent run."""

    agent_name: str
    status: AgentStatus
    started_at: datetime
    finished_at: datetime
    duration_ms: float
    error: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return data


class AgentExecutionError(RuntimeError):
    """Raised when an agent fails during execution."""

    def __init__(self, agent_name: str, cause: BaseException) -> None:
        self.agent_name = agent_name
        self.cause = cause
        super().__init__(f"Agent '{agent_name}' failed: {cause}")


InputT = TypeVar("InputT", bound=AgentInput)
OutputT = TypeVar("OutputT", bound=AgentOutput)


@dataclass(slots=True)
class AgentResult(Generic[OutputT]):
    """Output plus metadata returned by :meth:`BaseAgent.run`."""

    output: OutputT
    metadata: AgentExecutionMetadata


class BaseAgent(ABC, Generic[InputT, OutputT]):
    """Abstract async agent with logging, timing and error handling."""

    name: ClassVar[str] = "base_agent"
    description: ClassVar[str] = "Base agent contract."

    def __init__(self) -> None:
        self.logger = get_logger(f"app.agents.{self.name}")

    @abstractmethod
    async def execute(self, payload: InputT) -> OutputT:
        """Run the agent's task and return a validated output model."""
        raise NotImplementedError

    async def run(
        self,
        payload: InputT,
        *,
        context: dict[str, Any] | None = None,
    ) -> AgentResult[OutputT]:
        """Validate input, execute, measure, log, and wrap failures.

        ``context`` is carried for future LangGraph state (thread id, run id,
        candidate id) and is never forwarded to the LLM implicitly.
        """
        if not isinstance(payload, AgentInput):
            msg = f"{self.name}: payload must be a pydantic AgentInput instance"
            raise AgentExecutionError(self.name, TypeError(msg))

        started_at = datetime.now(timezone.utc)
        timer = time.perf_counter()
        self.logger.info(
            "Agent started",
            extra={"agent": self.name, "context": context or {}},
        )

        try:
            output = await self.execute(payload)
            if not isinstance(output, AgentOutput):
                msg = f"{self.name}: execute() must return a pydantic AgentOutput instance"
                raise TypeError(msg)
        except Exception as exc:
            finished_at = datetime.now(timezone.utc)
            duration_ms = round((time.perf_counter() - timer) * 1000, 2)
            self.logger.exception(
                "Agent failed",
                extra={
                    "agent": self.name,
                    "context": context or {},
                    "duration_ms": duration_ms,
                },
            )
            metadata = AgentExecutionMetadata(
                agent_name=self.name,
                status=AgentStatus.FAILED,
                started_at=started_at,
                finished_at=finished_at,
                duration_ms=duration_ms,
                error=f"{type(exc).__name__}: {exc}",
            )
            raise AgentExecutionError(self.name, exc) from exc
        else:
            finished_at = datetime.now(timezone.utc)
            duration_ms = round((time.perf_counter() - timer) * 1000, 2)
            self.logger.info(
                "Agent finished",
                extra={
                    "agent": self.name,
                    "context": context or {},
                    "duration_ms": duration_ms,
                },
            )
            metadata = AgentExecutionMetadata(
                agent_name=self.name,
                status=AgentStatus.SUCCEEDED,
                started_at=started_at,
                finished_at=finished_at,
                duration_ms=duration_ms,
            )

        return AgentResult(output=output, metadata=metadata)
