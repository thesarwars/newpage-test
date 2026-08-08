"""Pricing, the budget ceiling, and backend selection."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

# pytest-django renamed SettingsWrapper to Settings; this is the current name.
from pytest_django.fixtures import Settings

from apps.observability.models import LLMCall
from llm import backends, budget
from llm.fake import FakeAnthropic
from llm.gateway import AnthropicGateway
from llm.pricing import cost_usd


class TestPricing:
    def test_opus_5_rates(self) -> None:
        assert cost_usd(model="claude-opus-5", input_tokens=1_000_000, output_tokens=0) == Decimal(
            "5.000000"
        )
        assert cost_usd(model="claude-opus-5", input_tokens=0, output_tokens=1_000_000) == Decimal(
            "25.000000"
        )

    def test_cache_reads_are_a_tenth_of_input(self) -> None:
        assert cost_usd(
            model="claude-opus-5", input_tokens=0, output_tokens=0, cache_read_tokens=1_000_000
        ) == Decimal("0.500000")

    def test_cache_writes_cost_more_than_plain_input(self) -> None:
        """The 1.25x write multiplier. A cold turn costs more, not less."""
        assert cost_usd(
            model="claude-opus-5",
            input_tokens=0,
            output_tokens=0,
            cache_creation_tokens=1_000_000,
        ) == Decimal("6.250000")

    def test_introductory_rate_expires_on_its_own(self) -> None:
        """A price that was right in August is quietly wrong in September.

        The table carries the end date so the correction is automatic rather
        than dependent on somebody remembering.
        """
        during = cost_usd(
            model="claude-sonnet-5",
            input_tokens=1_000_000,
            output_tokens=0,
            on=date(2026, 8, 15),
        )
        after = cost_usd(
            model="claude-sonnet-5",
            input_tokens=1_000_000,
            output_tokens=0,
            on=date(2026, 9, 1),
        )

        assert during == Decimal("2.000000")
        assert after == Decimal("3.000000")

    def test_unknown_model_degrades_reporting_not_the_request(self) -> None:
        """A new model id should cost the answer nothing. The id lands on the row."""
        assert cost_usd(model="claude-6", input_tokens=1000, output_tokens=1000) == Decimal("0")


@pytest.mark.django_db
class TestBudget:
    def test_spend_is_summed_over_a_rolling_day(self, settings: Settings) -> None:
        LLMCall.objects.create(purpose="chat", model="claude-opus-5", cost_usd=Decimal("2.50"))
        stale = LLMCall.objects.create(
            purpose="chat", model="claude-opus-5", cost_usd=Decimal("99.00")
        )
        # A calendar-day window would let the same burst run twice either side
        # of midnight, and there is no timezone in which "today" is unambiguous
        # for a globally reachable service.
        LLMCall.objects.filter(pk=stale.pk).update(created_at=timezone.now() - timedelta(days=2))

        assert budget.spent_today() == Decimal("2.50")

    def test_check_raises_once_the_ceiling_is_reached(self, settings: Settings) -> None:
        settings.LLM_DAILY_COST_CEILING_USD = 1.0
        LLMCall.objects.create(purpose="chat", model="claude-opus-5", cost_usd=Decimal("1.00"))

        with pytest.raises(budget.BudgetExhaustedError):
            budget.check()

    def test_remaining_never_goes_negative(self, settings: Settings) -> None:
        settings.LLM_DAILY_COST_CEILING_USD = 1.0
        LLMCall.objects.create(purpose="chat", model="claude-opus-5", cost_usd=Decimal("5.00"))

        assert budget.remaining() == Decimal("0")


class TestBackendSelection:
    def test_auto_without_a_key_falls_back_to_the_stub(self, settings: Settings) -> None:
        """Someone who just cloned this gets a working app, not a startup crash."""
        settings.LLM_BACKEND = "auto"
        settings.ANTHROPIC_API_KEY = ""

        assert isinstance(backends.get_backend(), FakeAnthropic)
        assert backends.has_live_backend() is False

    def test_auto_with_a_key_uses_the_real_gateway(self, settings: Settings) -> None:
        settings.LLM_BACKEND = "auto"
        settings.ANTHROPIC_API_KEY = "sk-test"

        assert isinstance(backends.get_backend(), AnthropicGateway)
        assert backends.has_live_backend() is True

    def test_explicit_anthropic_without_a_key_refuses_to_start(self, settings: Settings) -> None:
        """A deployment that meant to use the model and quietly served stubs is
        a worse failure than a crash."""
        settings.LLM_BACKEND = "anthropic"
        settings.ANTHROPIC_API_KEY = ""

        with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY is empty"):
            backends.get_backend()

    def test_a_typo_in_the_backend_name_is_not_silently_ignored(self, settings: Settings) -> None:
        settings.LLM_BACKEND = "antropic"

        with pytest.raises(RuntimeError, match="must be auto, anthropic or fake"):
            backends.get_backend()
