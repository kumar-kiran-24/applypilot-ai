"""Read and update per-user auto-apply settings.

Defaults are conservative and never invent candidate data:

* automation is disabled;
* an explicit approval and a final user review are required;
* no salary bounds and no relocation willingness are assumed;
* the daily cap is small and the minimum resume match is moderate.

Updating settings never touches the existing :class:`JobPreference` row; those
preferences remain the user's search preferences and are only used as a
fallback when an auto-apply list is empty.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.auto_apply import UserAutoApplySettings
from app.models.preference import RelocationPreference
from app.models.user import User
from app.schemas.auto_apply import AutoApplySettingsUpdate

DEFAULT_MINIMUM_RESUME_MATCH = 60
DEFAULT_MAX_APPLICATIONS_PER_DAY = 5

_SETTING_LIST_FIELDS = (
    "preferred_job_titles",
    "excluded_job_titles",
    "preferred_companies",
    "excluded_companies",
    "preferred_locations",
)


class AutoApplySettingsService:
    """Persistence and defaults for one user's automation settings."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_or_create(self, user: User) -> UserAutoApplySettings:
        """Return the user's settings, creating conservative defaults once."""
        settings = await self.session.scalar(
            select(UserAutoApplySettings).where(
                UserAutoApplySettings.user_id == user.id
            )
        )
        if settings is not None:
            return settings

        settings = UserAutoApplySettings(
            id=uuid.uuid4(),
            user_id=user.id,
            automation_enabled=False,
            require_user_approval=True,
            require_user_review=True,
            max_applications_per_day=DEFAULT_MAX_APPLICATIONS_PER_DAY,
            minimum_resume_match_percentage=DEFAULT_MINIMUM_RESUME_MATCH,
            preferred_job_titles=[],
            excluded_job_titles=[],
            preferred_companies=[],
            excluded_companies=[],
            preferred_locations=[],
            work_modes=[],
            employment_types=[],
            currency="USD",
            relocation_willingness=RelocationPreference.UNKNOWN,
        )
        self.session.add(settings)
        await self.session.flush()
        return settings

    async def get_settings(self, user: User) -> UserAutoApplySettings:
        settings = await self.get_or_create(user)
        await self.session.commit()
        await self.session.refresh(settings)
        return settings

    async def update_settings(
        self, user: User, payload: AutoApplySettingsUpdate
    ) -> UserAutoApplySettings:
        settings = await self.get_or_create(user)
        values = payload.model_dump(exclude_unset=True)

        for field in _SETTING_LIST_FIELDS:
            if field in values and values[field] is not None:
                setattr(settings, field, list(values[field]))
        if "work_modes" in values and values["work_modes"] is not None:
            settings.work_modes = [
                item.value if hasattr(item, "value") else str(item)
                for item in values["work_modes"]
            ]
        if "employment_types" in values and values["employment_types"] is not None:
            settings.employment_types = [
                item.value if hasattr(item, "value") else str(item)
                for item in values["employment_types"]
            ]
        if (
            "relocation_willingness" in values
            and values["relocation_willingness"] is not None
        ):
            relocation = values["relocation_willingness"]
            settings.relocation_willingness = (
                relocation
                if isinstance(relocation, RelocationPreference)
                else RelocationPreference(relocation)
            )
        for field in (
            "automation_enabled",
            "require_user_approval",
            "require_user_review",
            "max_applications_per_day",
            "minimum_resume_match_percentage",
            "minimum_salary",
            "maximum_salary",
            "currency",
        ):
            if field in values:
                setattr(settings, field, values[field])

        await self.session.commit()
        await self.session.refresh(settings)
        return settings


__all__ = [
    "DEFAULT_MAX_APPLICATIONS_PER_DAY",
    "DEFAULT_MINIMUM_RESUME_MATCH",
    "AutoApplySettingsService",
]
