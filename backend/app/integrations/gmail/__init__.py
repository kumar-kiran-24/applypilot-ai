"""Gmail integration (read-only, user-authorised access).

The implementation lives in ``app.services`` so it can use the shared domain
errors and settings:

- ``gmail_oauth.py`` - separate OAuth client with incremental ``gmail.readonly``;
- ``gmail_service.py`` - token refresh, Gmail REST client, parsing, revocation;
- ``gmail_token_encryption.py`` - Fernet encryption for refresh tokens;
- ``glassdoor_jobs.py`` - Glassdoor job-alert email discovery and extraction.
"""
