"""Database bootstrap helpers.

Schema changes in every real environment are applied by Alembic. The helpers
here exist for local development and tests only; ``create_all`` is never called
from the application startup path.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import settings
from app.core.logging import get_logger
from app.db.base import Base
from app.db.session import engine

logger = get_logger(__name__)


def _load_models() -> None:
    """Import every model module so it registers on ``Base.metadata``."""
    import app.models  # noqa: F401  (import side effect: model registration)


async def check_database_connection(target: AsyncEngine | None = None) -> bool:
    """Return True when a trivial query succeeds against the database."""
    active_engine = target or engine
    async with active_engine.connect() as connection:
        await connection.execute(text("SELECT 1"))
    return True


async def init_db(target: AsyncEngine | None = None) -> None:
    """Create all tables directly from metadata. Development/testing only."""
    if settings.is_production:
        msg = "init_db() must not run in production; use Alembic migrations."
        raise RuntimeError(msg)

    _load_models()
    active_engine = target or engine
    async with active_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    logger.info("Database schema created via metadata (development only)")


async def drop_db(target: AsyncEngine | None = None) -> None:
    """Drop all tables. Development/testing only."""
    if settings.is_production:
        msg = "drop_db() must not run in production."
        raise RuntimeError(msg)

    _load_models()
    active_engine = target or engine
    async with active_engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
    logger.info("Database schema dropped (development only)")


async def _main() -> None:
    from app.core.logging import setup_logging

    setup_logging()
    await init_db()
    logger.info("Database initialised")


if __name__ == "__main__":
    asyncio.run(_main())
