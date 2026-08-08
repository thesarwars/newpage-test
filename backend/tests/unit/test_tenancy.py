"""The tenancy guard.

docs/PLAN.md marks this as never-cut. Cross-tenant leakage here is one forgotten
`.filter(session=...)` away and it fails *silently* — a 200 containing somebody
else's résumé. So scoping is tested as a property of the manager, and the
per-view probes land alongside each view as it ships (M2 onward).
"""

from __future__ import annotations

import pytest

from apps.core.managers import SessionScopedQuerySet
from apps.core.models import Session, SessionScopedModel

pytestmark = pytest.mark.django_db


def test_every_session_scoped_model_uses_the_scoping_manager() -> None:
    """Enumerate subclasses rather than trusting each author to remember.

    A new tenant-owned model that defines its own plain `objects` manager loses
    `for_session()` and gets an unscoped default. This fails the moment that
    happens, not the moment it leaks.

    Vacuous at M1 — Document is the first subclass and arrives in M2. It is
    written now so the guard is already in place when the first tenant-owned
    model lands, rather than being remembered afterwards.
    """
    offenders = [
        model.__name__
        for model in SessionScopedModel.__subclasses__()
        if not isinstance(model._default_manager.get_queryset(), SessionScopedQuerySet)
    ]

    assert not offenders, (
        f"{offenders} inherit SessionScopedModel but do not use SessionScopedManager — "
        "their default manager returns every tenant's rows"
    )


def test_every_session_scoped_model_has_a_session_column() -> None:
    for model in SessionScopedModel.__subclasses__():
        assert model._meta.get_field("session"), f"{model.__name__} has no tenant column"


def test_for_session_refuses_none() -> None:
    """Scoping to None must raise, not return everything.

    `for_session(request.cia_session)` on an unauthenticated request is the exact
    shape of this bug, and a silently-unfiltered queryset is the worst possible
    outcome — a 200 with another tenant's résumé in it.
    """
    queryset = SessionScopedQuerySet(model=Session)

    with pytest.raises(ValueError, match="every tenant's rows"):
        queryset.for_session(None)


# The *positive* path — that for_session(s) actually restricts to s — cannot be
# asserted here: Session is the tenant root and has no `session` column, and
# Document (the first scoped model) arrives in M2. It is asserted there, per
# model, as a cross-tenant probe: session A requesting session B's row by UUID
# must 404. That is the assertion that matters anyway; a queryset-shape check
# would pass just as happily against a filter on the wrong column.


def test_session_tokens_are_unique_and_unguessable() -> None:
    tokens = {Session.objects.create().token for _ in range(25)}

    assert len(tokens) == 25
    assert all(len(token) >= 43 for token in tokens), "token has too little entropy"
