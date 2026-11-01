"""Async Groq client for structured JSON completions.

Scope: one job - send a system/user prompt pair to Groq in JSON mode and return
the raw JSON text. Prompt construction and schema validation belong to the
agents, not to the transport layer.

The API key is resolved lazily so the module can be imported (and prompt/schema
tests can run) without credentials. A missing key raises a configuration error
instead of silently returning canned content.
"""

from __future__ import annotations

import asyncio

from groq import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncGroq,
    AuthenticationError,
    InternalServerError,
    RateLimitError,
)

from app.core.config import Settings, get_settings
from app.core.exceptions import AppError, ConfigurationError
from app.core.logging import get_logger

logger = get_logger(__name__)

_MAX_BACKOFF_SECONDS = 8.0
_BASE_BACKOFF_SECONDS = 0.75


class LLMError(AppError):
    """Upstream LLM failure (retries exhausted, empty response, bad status)."""

    status_code = 502
    code = "llm_error"
    message = "The language model request failed."


class LLMConfigurationError(ConfigurationError):
    """The LLM integration is not configured (e.g. missing API key)."""

    code = "llm_not_configured"
    message = "GROQ_API_KEY is not configured."


class GroqLLMClient:
    """Thin async wrapper over the Groq chat completions API."""

    RETRYABLE_EXCEPTIONS: tuple[type[Exception], ...] = (
        RateLimitError,
        APIConnectionError,
        APITimeoutError,
        InternalServerError,
    )

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        client: AsyncGroq | None = None,
        api_key: str | None = None,
        model: str | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._api_key = api_key if api_key is not None else self.settings.GROQ_API_KEY
        self.model = model or self.settings.GROQ_MODEL
        self._client = client

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key)

    def _get_client(self) -> AsyncGroq:
        if self._client is not None:
            return self._client
        if not self._api_key:
            raise LLMConfigurationError(
                "GROQ_API_KEY is not set. Add it to the environment to use LLM features."
            )
        self._client = AsyncGroq(
            api_key=self._api_key,
            timeout=self.settings.GROQ_TIMEOUT_SECONDS,
            max_retries=0,  # retries are handled here so backoff is observable
        )
        return self._client

    async def complete_json(self, *, system: str, user: str) -> str:
        """Return the raw JSON text produced by the model.

        Raises:
            LLMConfigurationError: no API key or the key was rejected.
            LLMError: retries exhausted, upstream error, or empty response.
        """
        client = self._get_client()
        attempts = self.settings.GROQ_MAX_RETRIES + 1
        last_error: Exception | None = None

        for attempt in range(attempts):
            try:
                completion = await client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    temperature=self.settings.GROQ_TEMPERATURE,
                    max_completion_tokens=self.settings.GROQ_MAX_COMPLETION_TOKENS,
                    response_format={"type": "json_object"},
                )
            except AuthenticationError as exc:
                raise LLMConfigurationError("Groq rejected the configured API key.") from exc
            except self.RETRYABLE_EXCEPTIONS as exc:
                last_error = exc
                if attempt == attempts - 1:
                    break
                delay = min(_BASE_BACKOFF_SECONDS * (2**attempt), _MAX_BACKOFF_SECONDS)
                logger.warning(
                    "Groq request failed; retrying",
                    extra={
                        "model": self.model,
                        "attempt": attempt + 1,
                        "retry_in_seconds": delay,
                        "error": type(exc).__name__,
                    },
                )
                await asyncio.sleep(delay)
                continue
            except APIStatusError as exc:
                raise LLMError(
                    f"Groq returned HTTP {exc.status_code} for model '{self.model}'."
                ) from exc
            else:
                content = completion.choices[0].message.content if completion.choices else None
                if not content or not content.strip():
                    raise LLMError("Groq returned an empty response.")

                usage = getattr(completion, "usage", None)
                logger.info(
                    "Groq completion received",
                    extra={
                        "model": self.model,
                        "prompt_tokens": getattr(usage, "prompt_tokens", None),
                        "completion_tokens": getattr(usage, "completion_tokens", None),
                    },
                )
                return content

        raise LLMError(
            f"Groq request failed after {attempts} attempt(s) "
            f"({type(last_error).__name__ if last_error else 'unknown error'})."
        ) from last_error
