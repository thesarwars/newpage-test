"""The daily spend ceiling.

A per-session cap is trivially bypassed by looping sessions — the cookie is
free. So the ceiling is global and reads the ledger, which is the only place
spend is recorded. It exists to protect the API key of whoever runs this, and
the README says so in those words.

Checked inside the gateway rather than in the view, because the gateway is the
only call site: a future endpoint that forgets to check cannot spend past it.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db.models import Sum
from django.utils import timezone

from apps.observability.models import LLMCall


class BudgetExhaustedError(Exception):
    """The daily ceiling is spent. Carries the numbers so the error can say so."""

    def __init__(self, spent: Decimal, ceiling: Decimal) -> None:
        self.spent = spent
        self.ceiling = ceiling
        super().__init__(f"daily LLM ceiling reached: ${spent} of ${ceiling}")


def spent_today() -> Decimal:
    """Total recorded spend in the last 24 hours.

    A rolling window, not a calendar day: a midnight boundary would let the same
    burst run twice in an hour either side of it, and there is no timezone in
    which "today" is unambiguous for a globally reachable service.
    """
    since = timezone.now() - timedelta(days=1)
    total = LLMCall.objects.filter(created_at__gte=since).aggregate(total=Sum("cost_usd"))["total"]
    return Decimal(total or 0)


def ceiling() -> Decimal:
    return Decimal(str(settings.LLM_DAILY_COST_CEILING_USD))


def check() -> None:
    """Raise if the next call would be over budget.

    Deliberately a pre-flight check against *recorded* spend rather than an
    estimate of the pending call: an estimate needs a second network round-trip
    on the exact path that is optimised for time-to-first-token, and
    `count_tokens` does not accept the full request surface anyway — it would be
    counting a request different from the one about to be sent.

    The consequence is that the ceiling can be exceeded by at most one call.
    That is the intended trade, and it is bounded by `max_tokens`.
    """
    limit = ceiling()
    spent = spent_today()
    if spent >= limit:
        raise BudgetExhaustedError(spent, limit)


def remaining() -> Decimal:
    return max(Decimal("0"), ceiling() - spent_today())
