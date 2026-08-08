"""Model pricing, with effective dates.

Prices change and introductory rates expire. A hard-coded number that was right
in August is quietly wrong in September, and cost reporting that is quietly wrong
is worse than none — it gets quoted in a README.

So the table carries `intro_until`, and the Sonnet 5 introductory rate
self-corrects the day it lapses rather than drifting.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

# Cache reads are billed at 0.1x input; 5-minute cache writes at 1.25x.
CACHE_READ_MULTIPLIER = Decimal("0.1")
CACHE_WRITE_MULTIPLIER = Decimal("1.25")

_MILLION = Decimal("1000000")


@dataclass(frozen=True)
class ModelPrice:
    input_per_mtok: Decimal
    output_per_mtok: Decimal
    intro_input_per_mtok: Decimal | None = None
    intro_output_per_mtok: Decimal | None = None
    intro_until: date | None = None

    def rates(self, on: date) -> tuple[Decimal, Decimal]:
        if self.intro_until and on <= self.intro_until:
            return (
                self.intro_input_per_mtok or self.input_per_mtok,
                self.intro_output_per_mtok or self.output_per_mtok,
            )
        return self.input_per_mtok, self.output_per_mtok


PRICES: dict[str, ModelPrice] = {
    "claude-opus-5": ModelPrice(Decimal("5.00"), Decimal("25.00")),
    "claude-opus-4-8": ModelPrice(Decimal("5.00"), Decimal("25.00")),
    "claude-sonnet-5": ModelPrice(
        Decimal("3.00"),
        Decimal("15.00"),
        intro_input_per_mtok=Decimal("2.00"),
        intro_output_per_mtok=Decimal("10.00"),
        intro_until=date(2026, 8, 31),
    ),
    "claude-haiku-4-5": ModelPrice(Decimal("1.00"), Decimal("5.00")),
}


def cost_usd(
    *,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
    on: date | None = None,
) -> Decimal:
    """Cost of one call, in USD.

    Returns 0 for an unknown model rather than raising: a new model id should
    degrade cost *reporting*, not break the request that produced it. The unknown
    id lands on the ledger row, so it is visible rather than silent.
    """
    price = PRICES.get(model)
    if price is None:
        return Decimal("0")

    input_rate, output_rate = price.rates(on or date.today())

    total = (
        Decimal(input_tokens) * input_rate
        + Decimal(cache_read_tokens) * input_rate * CACHE_READ_MULTIPLIER
        + Decimal(cache_creation_tokens) * input_rate * CACHE_WRITE_MULTIPLIER
        + Decimal(output_tokens) * output_rate
    ) / _MILLION

    return total.quantize(Decimal("0.000001"))
