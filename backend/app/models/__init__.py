
"""ORM model package.

Importing this package registers every model on Base.metadata.
"""

from app.models.application_answer import (
    AnswerType,
    ApplicationAnswer,
    VerificationStatus,
)
from app.models.candidate import Candidate
from app.models.education import Education
from app.models.experience import EmploymentType, Experience
from app.models.gmail_connection import GmailConnection
from app.models.preference import JobPreference, RelocationPreference, WorkMode
from app.models.project import Project
from app.models.user import User

__all__ = [
    "AnswerType",
    "ApplicationAnswer",
    "Candidate",
    "Education",
    "EmploymentType",
    "Experience",
    "GmailConnection",
    "JobPreference",
    "Project",
    "RelocationPreference",
    "User",
    "VerificationStatus",
    "WorkMode",
]
