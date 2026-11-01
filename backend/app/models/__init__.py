"""ORM model package.

Importing this package registers every model on ``Base.metadata``; Alembic and
the test fixtures rely on that side effect.
"""

from app.models.application_answer import (
    AnswerType,
    ApplicationAnswer,
    VerificationStatus,
)
from app.models.candidate import Candidate
from app.models.education import Education
from app.models.experience import EmploymentType, Experience
from app.models.preference import JobPreference, RelocationPreference, WorkMode
from app.models.project import Project

__all__ = [
    "AnswerType",
    "ApplicationAnswer",
    "Candidate",
    "Education",
    "EmploymentType",
    "Experience",
    "JobPreference",
    "Project",
    "RelocationPreference",
    "VerificationStatus",
    "WorkMode",
]
