
"""ORM model package.

Importing this package registers every model on Base.metadata.
"""

from app.models.application import (
    AnswerSource,
    Application,
    ApplicationQuestion,
    ApplicationStage,
    ApplicationStatus,
    AutomationMode,
    InspectionMode,
    QuestionStatus,
    QuestionType,
)
from app.models.application_answer import (
    AnswerType,
    ApplicationAnswer,
    VerificationStatus,
)
from app.models.auth_code import AuthCode
from app.models.candidate import Candidate
from app.models.certification import Certification
from app.models.education import Education
from app.models.experience import EmploymentType, Experience
from app.models.gmail_connection import GmailConnection
from app.models.job import Job
from app.models.job_match import JobResumeMatch
from app.models.preference import JobPreference, RelocationPreference, WorkMode
from app.models.project import Project
from app.models.resume import Resume, ResumeParsingStatus
from app.models.user import User

__all__ = [
    "AnswerSource",
    "AnswerType",
    "Application",
    "ApplicationAnswer",
    "ApplicationQuestion",
    "ApplicationStage",
    "ApplicationStatus",
    "AuthCode",
    "AutomationMode",
    "Candidate",
    "Certification",
    "Education",
    "EmploymentType",
    "Experience",
    "GmailConnection",
    "InspectionMode",
    "Job",
    "JobPreference",
    "JobResumeMatch",
    "Project",
    "QuestionStatus",
    "QuestionType",
    "RelocationPreference",
    "Resume",
    "ResumeParsingStatus",
    "User",
    "VerificationStatus",
    "WorkMode",
]
