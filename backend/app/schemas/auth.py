"""Authentication request/response schemas."""

from __future__ import annotations

from pydantic import BaseModel, Field


class AuthCodeExchangeRequest(BaseModel):
    """Body of the one-time login-code exchange.

    The shape is validated before any lookup: a malformed value can never reach
    the database. The exchange endpoint is the only place a login code is
    accepted, and it consumes the code on first use.
    """

    code: str = Field(
        min_length=32,
        max_length=128,
        pattern=r"^[A-Za-z0-9_-]+$",
        description="Opaque single-use code from the Google login callback redirect.",
    )
