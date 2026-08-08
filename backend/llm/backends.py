"""Backend selection.

`LLM_BACKEND` is `auto` by default, which means: use the real API if a key is
configured, otherwise fall back to the stub and keep working. That default is
the reason the app runs for someone who has just cloned it — the alternative,
failing at startup on a missing key, turns a five-minute evaluation into a
support thread.

`anthropic` and `fake` are the explicit overrides. `anthropic` refuses to start
without a key rather than silently degrading, because a deployment that meant
to use the real model and quietly served stubs is a worse failure than a crash.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, Protocol

import structlog
from django.conf import settings

from llm.fake import FakeAnthropic
from llm.types import ChatRequest, StreamEvent

log = structlog.get_logger(__name__)


class LLMBackend(Protocol):
    backend_name: str

    def stream(
        self,
        request: ChatRequest,
        *,
        session_id: Any = ...,
        message_id: Any = ...,
        purpose: str = ...,
        # `None` is the heartbeat sentinel: the backend is the only layer
        # that can tell the upstream has gone quiet.
    ) -> Iterator[StreamEvent | None]: ...


def get_backend() -> LLMBackend:
    choice = getattr(settings, "LLM_BACKEND", "auto")
    key = getattr(settings, "ANTHROPIC_API_KEY", "")

    if choice == "fake":
        return FakeAnthropic(mode="stub", delay_s=_delay())

    if choice == "anthropic":
        if not key:
            raise RuntimeError(
                "LLM_BACKEND=anthropic but ANTHROPIC_API_KEY is empty. "
                "Set the key, or use LLM_BACKEND=auto to fall back to the stub."
            )
        return _anthropic(key)

    if choice != "auto":
        raise RuntimeError(f"LLM_BACKEND must be auto, anthropic or fake — got {choice!r}.")

    if key:
        return _anthropic(key)

    log.info("llm_backend_stub", reason="no_api_key")
    return FakeAnthropic(mode="stub", delay_s=_delay())


def has_live_backend() -> bool:
    """Whether real generation is available. Drives the UI's demo banner.

    Reported by `/readyz` and the session payload — *reported, never required*.
    A missing key is a reduced-capability state, not an unhealthy one.
    """
    return get_backend().backend_name == "anthropic"


def _delay() -> float:
    return float(getattr(settings, "LLM_FAKE_DELAY_S", 0.0))


def _anthropic(key: str) -> LLMBackend:
    # Imported lazily so the keyless install never needs the SDK importable.
    from llm.gateway import AnthropicGateway

    return AnthropicGateway(key)
