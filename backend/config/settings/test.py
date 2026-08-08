"""Test settings.

The LLM is never called in CI (docs/PLAN.md §11) — LLM_BACKEND is pinned to the
fake so a stray real call fails loudly rather than silently costing money.
The embedder stays real: fastembed on CPU is deterministic, and mocking it would
mean the retrieval tests test nothing.
"""

from .base import *  # noqa: F403

DEBUG = False
LLM_BACKEND = "fake"
ANTHROPIC_API_KEY = ""

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
