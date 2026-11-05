"""Browser-automation contracts used by the application adapters.

The driver is optional. Importing this package never imports Playwright, so the
application works (through the guided manual path) when it is not installed.
"""

from app.integrations.browser.session import (
    AutomationSession,
    BrowserUnavailableError,
    ManualInterventionRequired,
    RawFormField,
)

__all__ = [
    "AutomationSession",
    "BrowserUnavailableError",
    "ManualInterventionRequired",
    "RawFormField",
]
