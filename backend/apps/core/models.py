"""Base models and the anonymous session that owns every tenant-scoped row."""

from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timedelta
from decimal import Decimal

from django.db import models
from django.utils import timezone

from apps.core.managers import SessionScopedManager

# A résumé is PII. Holding it indefinitely because nobody wrote a deletion path
# is the default failure of a demo app, so the TTL exists from the first commit
# and `purge_expired` enforces it.
SESSION_TTL = timedelta(days=7)


def new_session_token() -> str:
    """64 URL-safe characters from the system CSPRNG."""
    return secrets.token_urlsafe(48)


def default_expiry() -> datetime:
    return timezone.now() + SESSION_TTL


class TimeStamped(models.Model):
    """UUID-keyed, timestamped base for every model in the project.

    UUID4 rather than a sequential integer: primary keys appear in URLs
    (`/api/v1/documents/{id}/`), and a guessable key turns a missing tenancy
    filter into trivially enumerable data rather than a bug you have to work for.

    UUID4 and not uuid7 despite uuid7's sortability — `uuid.uuid7` is 3.14
    stdlib and this project pins 3.12; a hand-rolled v7 on the primary-key path
    of every model is not worth it.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Session(TimeStamped):
    """An anonymous, cookie-bearing workspace. The tenant.

    There is no user model and no login: the assignment does not ask for one, and
    auth is well-understood plumbing that would demonstrate nothing about RAG.
    What matters is that the *tenancy column exists from day one* — every
    scoped model has a `session` FK, so introducing real accounts later is a
    middleware swap plus Postgres RLS, not a data migration. See docs/PLAN.md §16.
    """

    token = models.CharField(
        max_length=64,
        unique=True,
        editable=False,
        default=new_session_token,
        help_text="Bearer credential carried in the signed cis_session cookie.",
    )
    expires_at = models.DateTimeField(default=default_expiry, db_index=True)

    # Running usage totals, denormalised onto the session so the budget meter is
    # a single row read rather than an aggregate over the whole call ledger.
    tokens_used = models.PositiveIntegerField(default=0)
    cost_usd = models.DecimalField(max_digits=10, decimal_places=6, default=Decimal("0"))

    demo_seeded = models.BooleanField(default=False)

    class Meta:
        indexes = [models.Index(fields=["expires_at"])]

    def __str__(self) -> str:
        return f"Session {self.pk}"

    @property
    def is_expired(self) -> bool:
        return self.expires_at <= timezone.now()

    def touch(self) -> None:
        """Slide the TTL forward on activity."""
        self.expires_at = default_expiry()
        self.save(update_fields=["expires_at", "updated_at"])


class SessionScopedModel(TimeStamped):
    """Base for every model a session owns.

    Carrying the FK here rather than on each subclass is what makes the tenancy
    guard test possible: it can enumerate `SessionScopedModel.__subclasses__()`
    and assert each one is only ever queried through `for_session()`.
    """

    session = models.ForeignKey(Session, on_delete=models.CASCADE, related_name="+")

    objects = SessionScopedManager()

    class Meta:
        abstract = True
