"""Tenancy scoping.

Cross-tenant leakage in this app is one forgotten `.filter(session=...)` away,
and it is silent — the endpoint returns 200 with somebody else's résumé. So the
scoping is a named operation rather than a convention: `for_session()` is the
only intended entry point, and `tests/api/test_tenancy.py` enforces it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django.db import models

if TYPE_CHECKING:
    from apps.core.models import Session


class SessionScopedQuerySet(models.QuerySet[Any]):
    def for_session(self, session: Session | Any) -> SessionScopedQuerySet:
        """Restrict to rows owned by `session`.

        Accepts a Session or its primary key. Passing None is a programming
        error and raises rather than quietly returning everything — an
        unscoped queryset is exactly the bug this exists to prevent.
        """
        if session is None:
            raise ValueError(
                "for_session(None) would return every tenant's rows. "
                "Resolve the session first, or return an empty queryset explicitly."
            )
        return self.filter(session=session)


class SessionScopedManager(models.Manager.from_queryset(SessionScopedQuerySet)):  # type: ignore[misc]
    """Default manager for session-owned models."""
