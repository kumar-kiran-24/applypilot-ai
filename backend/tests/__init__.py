"""Backend test suite.

Importing this package happens before ``tests.conftest`` imports the
application, so the placeholder settings below are applied first. They let the
suite run without a developer ``.env`` and without real Google credentials; the
OAuth network calls themselves are mocked in the tests.
"""

from __future__ import annotations

import os

os.environ.setdefault("OAUTH_SESSION_SECRET", "test-oauth-session-secret-" + "a" * 40)
os.environ.setdefault("JWT_SECRET_KEY", "test-jwt-secret-key-" + "b" * 40)
os.environ.setdefault("GOOGLE_CLIENT_ID", "test-client-id.apps.googleusercontent.com")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test-client-secret")
os.environ.setdefault(
    "GOOGLE_REDIRECT_URI", "http://testserver/api/v1/auth/google/callback"
)
