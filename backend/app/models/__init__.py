
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
from app.models.application_form_analysis import ApplicationFormAnalysis
from app.models.auth_code import AuthCode
from app.models.auto_apply import AutoApplyApproval, UserAutoApplySettings
from app.models.browser_session import (
    BrowserSession,
    BrowserSessionState,
    BrowserSessionStatus,
)
from app.models.candidate import Candidate
from app.models.certification import Certification
from app.models.education import Education
from app.models.experience import EmploymentType, Experience
from app.models.gmail_connection import GmailConnection
from app.models.job import Job
from app.models.job_match import JobResumeMatch
from app.models.missing_information import (
    MissingInformationItemStatus,
    MissingInformationQueue,
    MissingInformationStatus,
)
from app.models.preference import JobPreference, RelocationPreference, WorkMode
from app.models.project import Project
from app.models.resume import Resume, ResumeParsingStatus
from app.models.user import User

__all__ = [
    "AnswerSource",
    "AnswerType",
    "Application",
    "ApplicationAnswer",
    "ApplicationFormAnalysis",
    "ApplicationQuestion",
    "ApplicationStage",
    "ApplicationStatus",
    "AuthCode",
    "AutoApplyApproval",
    "AutomationMode",
    "BrowserSession",
    "BrowserSessionState",
    "BrowserSessionStatus",
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
    "MissingInformationItemStatus",
    "MissingInformationQueue",
    "MissingInformationStatus",
    "Project",
    "QuestionStatus",
    "QuestionType",
    "RelocationPreference",
    "Resume",
    "ResumeParsingStatus",
    "User",
    "UserAutoApplySettings",
    "VerificationStatus",
    "WorkMode",
]
