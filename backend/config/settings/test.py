"""Test settings.

The LLM is never called in CI (docs/PLAN.md §11) — LLM_BACKEND is pinned to the
fake so a stray real call fails loudly rather than silently costing money.
The embedder stays real: fastembed on CPU is deterministic, and mocking it would
mean the retrieval tests test nothing.
"""

import secrets

from config.logging import configure_structlog

from .base import *  # noqa: F403

DEBUG = False
LLM_BACKEND = "fake"
LLM_FAKE_DELAY_S = 0.0
ANTHROPIC_API_KEY = ""

# Generated per process rather than hard-coded. Tests need a *valid* key, not a
# secret one — but a committed literal is a password-shaped string in a public
# repository, which is the thing this project just finished removing. Nothing
# here depends on the key being stable across runs: cookies are signed and
# verified within a single test process.
SECRET_KEY = secrets.token_urlsafe(32)

# Tests assert on log *shape*, so they run against the production renderer.
LOG_FORMAT = "json"
LOGGING = configure_structlog(fmt=LOG_FORMAT)

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
