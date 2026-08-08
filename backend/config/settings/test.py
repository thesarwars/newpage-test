"""Test settings.

The LLM is never called in CI (docs/PLAN.md §11) — LLM_BACKEND is pinned to the
fake so a stray real call fails loudly rather than silently costing money.
The embedder stays real: fastembed on CPU is deterministic, and mocking it would
mean the retrieval tests test nothing.
"""

from config.logging import configure_structlog

from .base import *  # noqa: F403

DEBUG = False
LLM_BACKEND = "fake"
ANTHROPIC_API_KEY = ""

# Tests assert on log *shape*, so they run against the production renderer.
LOG_FORMAT = "json"
LOGGING = configure_structlog(fmt=LOG_FORMAT)

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
