"""The chat endpoint, end to end, with no API key — which is CI's only option.

The acceptance criterion for this milestone is that `POST /api/v1/chat/` streams
the full event sequence with `LLM_BACKEND=fake` *and* with no key set. These
tests are that criterion, plus the three failures that are cheap to get wrong
and expensive to discover: spending tokens when there is nothing to answer from,
keeping a partial answer after a refusal, and losing the ledger row when the
call fails.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from rest_framework.test import APIClient

from apps.chat.models import Citation, Message, MessageStatus, RetrievalTrace
from apps.chat.throttles import ChatBurstThrottle, ChatSustainedThrottle
from apps.core.models import Session
from apps.documents.ingest import ingest_text
from apps.documents.models import Document, DocumentKind
from apps.observability.models import LLMCall
from llm import backends, budget
from llm.fake import FakeAnthropic
from llm.gateway import UpstreamError
from tests.conftest import drain

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "anthropic"

RESUME = """PROFESSIONAL SUMMARY
Backend engineer with eight years building payment and logistics systems.

EXPERIENCE
Senior Backend Engineer — Meridian Logistics (2022 to Present)
Reduced p99 latency from 1.4s to 380ms across the dispatch API.
Owned the migration from a monolith to six Go services on AWS ECS.

Backend Engineer — Northwind Payments (2019 to 2022)
Built idempotent settlement workflows in Python over PostgreSQL and Kafka.

SKILLS
Go, Python, PostgreSQL, Kafka, Terraform, AWS, Docker

EDUCATION
BSc Computer Science, University of Manchester
"""

JOB = """Senior Platform Engineer
Helios Freight

ABOUT THE ROLE
We run a logistics platform handling two million shipments a month.

REQUIREMENTS
- 5+ years of backend engineering in Go or Python
- Production experience operating Kubernetes at scale
- Strong PostgreSQL skills, including query tuning
- Experience with event-driven systems such as Kafka

NICE TO HAVE
- Terraform and infrastructure as code
- Exposure to freight or logistics domains
"""


# ── helpers ──────────────────────────────────────────────────────────────────


def parse_sse(response: Any) -> list[tuple[str, dict[str, Any]]]:
    """Collect `(event, data)` pairs. Also asserts the framing is well-formed."""
    body = drain(response)
    events: list[tuple[str, dict[str, Any]]] = []
    for frame in body.split("\n\n"):
        frame = frame.strip()
        if not frame or frame.startswith(":"):
            continue
        lines = frame.split("\n")
        assert lines[0].startswith("event: "), f"malformed SSE frame: {frame[:80]!r}"
        assert lines[1].startswith("data: "), f"frame without data: {frame[:80]!r}"
        events.append((lines[0][7:], json.loads(lines[1][6:])))
    return events


def names(events: list[tuple[str, dict[str, Any]]]) -> list[str]:
    return [name for name, _ in events]


def first(events: list[tuple[str, dict[str, Any]]], name: str) -> dict[str, Any]:
    for event_name, data in events:
        if event_name == name:
            return data
    raise AssertionError(f"no `{name}` event in {names(events)}")


@pytest.fixture
def corpus(session: Session) -> tuple[Document, Document]:
    resume = ingest_text(
        session=session, kind=DocumentKind.RESUME, text=RESUME, label="Résumé"
    ).document
    job = ingest_text(
        session=session, kind=DocumentKind.JOB, text=JOB, label="Senior Platform Engineer"
    ).document
    return resume, job


def ask(client: APIClient, message: str, **extra: Any) -> list[tuple[str, dict[str, Any]]]:
    response = client.post("/api/v1/chat/", {"message": message, **extra}, format="json")
    assert response.status_code == 200, getattr(response, "data", response)
    assert response["Content-Type"] == "text/event-stream"
    # nginx buffers proxied responses by default, which would deliver the whole
    # stream at once — the one failure that never reproduces locally.
    assert response["X-Accel-Buffering"] == "no"
    return parse_sse(response)


@pytest.mark.django_db
def test_the_sse_endpoint_accepts_the_sse_accept_header(
    session_client: APIClient, corpus: tuple[Document, Document]
) -> None:
    """`Accept: text/event-stream` was the only value that failed.

    DRF negotiates a renderer in `initial()`, before the view body runs. With no
    renderer able to produce `text/event-stream` it returned **406 Not
    Acceptable** — to the one header a client would naturally send to an SSE
    endpoint. `*/*` and `application/json` both worked, which is why curl and
    fetch never noticed.
    """
    for accept in ("text/event-stream", "text/event-stream, */*", "*/*", "application/json"):
        response = session_client.post(
            "/api/v1/chat/",
            {"message": "What am I missing?"},
            format="json",
            HTTP_ACCEPT=accept,
        )
        assert response.status_code == 200, f"Accept: {accept} returned {response.status_code}"
        drain(response)


def test_sse_headers_carry_no_hop_by_hop_header() -> None:
    """`Connection: keep-alive` is in every SSE tutorial and is a 500 under WSGI.

    wsgiref asserts on hop-by-hop headers, and Django's test client never
    reaches `start_response` — so the endpoint returned 200 across the whole
    suite and 500 against the actual server. This test stands in for what the
    test client structurally cannot see.
    """
    from apps.chat.streaming import HOP_BY_HOP, SSE_HEADERS

    assert not {h.lower() for h in SSE_HEADERS} & HOP_BY_HOP


# ── the happy path ───────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestStream:
    def test_full_event_sequence_with_no_api_key(
        self, session_client: APIClient, corpus: tuple[Document, Document]
    ) -> None:
        events = ask(session_client, "What am I missing for this role?")
        sequence = names(events)

        assert sequence[0] == "status"
        # Sources land before any text exists. That ordering is the product
        # decision that makes the retrieval legible rather than implied.
        assert sequence.index("sources") < sequence.index("delta")
        assert sequence.index("scope") < sequence.index("sources")
        assert sequence[-1] == "done"

    def test_scope_reports_demo_mode_when_there_is_no_key(
        self, session_client: APIClient, corpus: tuple[Document, Document]
    ) -> None:
        """The banner's data source. A stub must never be mistakable for output."""
        scope = first(ask(session_client, "Am I a good fit?"), "scope")

        assert scope["demo_mode"] is True
        assert scope["intent"] == "fit"

    def test_source_chips_carry_previews_not_whole_chunks(
        self, session_client: APIClient, corpus: tuple[Document, Document]
    ) -> None:
        sources = first(ask(session_client, "What are the requirements?"), "sources")

        assert sources["chunks"]
        for chunk in sources["chunks"]:
            assert len(chunk["preview"]) <= 180
            assert {"id", "doc_id", "doc_label", "kind", "char_start", "char_end"} <= set(chunk)

    def test_stub_citations_point_at_real_document_spans(
        self, session_client: APIClient, corpus: tuple[Document, Document]
    ) -> None:
        """The claim the keyless path makes: clicking `[1]` demonstrably works.

        A stub that emitted plausible-looking offsets would look identical in
        screenshots and be worthless. This resolves every citation against the
        stored document text.
        """
        events = ask(session_client, "What am I missing for this role?")
        citations = [data for name, data in events if name == "citation"]
        assert citations, "the stub is supposed to cite the passages it quotes"

        documents = {str(d.id): d for d in Document.objects.all()}
        for citation in citations:
            document = documents[citation["doc_id"]]
            span = document.normalized_text[citation["char_start"] : citation["char_end"]]
            assert span == citation["cited_text"]

    def test_answer_and_citations_are_persisted_for_reload(
        self, session_client: APIClient, corpus: tuple[Document, Document]
    ) -> None:
        events = ask(session_client, "What am I missing for this role?")
        message_id = first(events, "done")["message_id"]

        message = Message.objects.get(pk=message_id)
        streamed = "".join(data["text"] for name, data in events if name == "delta")

        assert message.status == MessageStatus.COMPLETE
        assert message.content == streamed, "reload must show exactly what streamed"
        assert Citation.objects.filter(message=message).count() == len(
            [1 for name, _ in events if name == "citation"]
        )

    def test_answer_char_points_just_past_the_text_it_cites(
        self, session_client: APIClient, corpus: tuple[Document, Document]
    ) -> None:
        """The citation marks what came *before* it, not what comes after.

        `answer_char` is the length of the answer emitted when the citation
        arrived, so the character immediately before it is the last character of
        the cited passage. That makes the mark render as `“…passage” [1]`.

        Pinned because the semantics are invisible to every other assertion: the
        existing `0 <= answer_char <= len(answer)` check passes whether the
        offset is captured before or after the quote, and moving it silently
        relocates every mark in the UI. It also decides the client's interval
        convention — a half-open reading of this offset snaps each mark forward
        onto the *next* list item's title, so `[1]` would label passage 2 with
        no error anywhere.
        """
        events = ask(session_client, "What am I missing for this role?")
        answer = "".join(data["text"] for name, data in events if name == "delta")
        citations = [data for name, data in events if name == "citation"]
        assert citations

        for citation in citations:
            at = citation["answer_char"]
            assert at > 0
            assert answer[at - 1] == "”", (
                f"answer_char {at} should sit just past a closing quote, "
                f"found {answer[at - 1]!r}"
            )

    def test_answer_char_offsets_land_inside_the_answer(
        self, session_client: APIClient, corpus: tuple[Document, Document]
    ) -> None:
        """Where the `[n]` mark gets spliced. Off the end means a lost mark."""
        events = ask(session_client, "What am I missing for this role?")
        answer = "".join(data["text"] for name, data in events if name == "delta")

        for name, data in events:
            if name == "citation":
                assert 0 <= data["answer_char"] <= len(answer)

    def test_trace_is_persisted_and_retrievable(
        self, session_client: APIClient, corpus: tuple[Document, Document]
    ) -> None:
        message_id = first(ask(session_client, "What am I missing?"), "done")["message_id"]

        response = session_client.get(f"/api/v1/traces/{message_id}/")

        assert response.status_code == 200
        trace = response.data["retrieval"]
        assert trace["query"] == "What am I missing?"
        assert trace["selected_chunk_ids"]
        assert "dense" in trace["timings_ms"]
        assert response.data["llm_calls"], "the ledger row belongs to the message"

    def test_trace_never_carries_document_text(
        self, session_client: APIClient, corpus: tuple[Document, Document]
    ) -> None:
        """This row gets screenshotted into bug reports. A résumé line must not."""
        message_id = first(ask(session_client, "What am I missing?"), "done")["message_id"]
        row = RetrievalTrace.objects.get(message_id=message_id)

        blob = json.dumps([row.dense_hits, row.lexical_hits, row.fused, row.selected_chunk_ids])
        assert "Meridian" not in blob
        assert "p99" not in blob


# ── the paths that must not spend tokens ─────────────────────────────────────


@pytest.mark.django_db
class TestFreeRefusals:
    def test_out_of_scope_costs_nothing(
        self, session_client: APIClient, corpus: tuple[Document, Document]
    ) -> None:
        events = ask(session_client, "What's the weather in Berlin?")

        assert first(events, "refusal")["reason"] == "out_of_scope"
        assert "delta" not in names(events)
        assert LLMCall.objects.count() == 0, "a deterministic refusal must not call the model"

    def test_fabrication_request_is_refused_with_a_redirect(
        self, session_client: APIClient, corpus: tuple[Document, Document]
    ) -> None:
        """Refusing without offering an alternative is merely useless."""
        refusal = first(ask(session_client, "Add 3 years of Kubernetes to my résumé"), "refusal")

        assert refusal["reason"] == "fabrication_request"
        assert refusal["suggestion"], "the card needs something to offer"
        assert LLMCall.objects.count() == 0

    def test_no_documents_means_no_context_and_no_call(self, session_client: APIClient) -> None:
        events = ask(session_client, "How do I match this role?")

        assert "no_context" in names(events)
        assert first(events, "no_context")["suggestions"]
        assert LLMCall.objects.count() == 0

    def test_refused_messages_are_stored_without_content(
        self, session_client: APIClient, corpus: tuple[Document, Document]
    ) -> None:
        message_id = first(ask(session_client, "Make my résumé say I led a team"), "done")[
            "message_id"
        ]

        message = Message.objects.get(pk=message_id)
        assert message.status == MessageStatus.REFUSED
        assert message.content == ""


# ── failure modes ────────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestFailures:
    def test_mid_stream_refusal_discards_the_partial_answer(
        self,
        session_client: APIClient,
        corpus: tuple[Document, Document],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Text is already on the wire when the refusal lands. Discard it.

        The plan does not claim `stop_reason` can be checked before `content` on
        the streaming path — it cannot. It claims the partial text is thrown
        away, and that is what this asserts.
        """
        monkeypatch.setattr(
            backends,
            "get_backend",
            lambda: FakeAnthropic(
                mode="replay", fixture=FIXTURES / "refusal_stream.json", delay_s=0.0
            ),
        )

        events = ask(session_client, "What am I missing for this role?")
        message_id = first(events, "done")["message_id"]

        assert first(events, "refusal")["reason"] == "model_refusal"
        assert "delta" in names(events), "the fixture streams text before refusing"
        message = Message.objects.get(pk=message_id)
        assert message.status == MessageStatus.REFUSED
        assert message.content == "", "a reload must not resurrect the half-answer"

    def test_upstream_failure_surfaces_as_an_error_event(
        self,
        session_client: APIClient,
        corpus: tuple[Document, Document],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The client gets a typed failure, not a truncated stream.

        That the *ledger row* survives the failure is tested against the real
        gateway in tests/unit/test_gateway.py — asserting it here would only
        prove that the stand-in below does what the stand-in was written to do.
        """

        class Exploding:
            backend_name = "anthropic"

            def stream(self, request: Any, **kwargs: Any) -> Any:
                raise UpstreamError("upstream is down", error_type="APITimeoutError")
                yield  # pragma: no cover — unreachable; makes this a generator

        monkeypatch.setattr(backends, "get_backend", Exploding)

        events = ask(session_client, "What am I missing for this role?")

        assert first(events, "error")["code"] == "upstream_error"
        message = Message.objects.filter(role="assistant").get()
        assert message.status == MessageStatus.ERROR

    def test_budget_ceiling_stops_generation_with_a_usable_error(
        self,
        session_client: APIClient,
        corpus: tuple[Document, Document],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        class Broke:
            backend_name = "anthropic"

            def stream(self, request: Any, **kwargs: Any) -> Any:
                raise budget.BudgetExhaustedError(Decimal("10.00"), Decimal("10.00"))
                yield  # pragma: no cover

        monkeypatch.setattr(backends, "get_backend", Broke)

        error = first(ask(session_client, "What am I missing for this role?"), "error")

        assert error["code"] == "budget_exhausted"
        # The hint has to say what still works, or the app looks dead.
        assert "still work" in error["hint"]


# ── history and tenancy ──────────────────────────────────────────────────────


@pytest.mark.django_db
class TestHistory:
    def test_messages_endpoint_returns_the_conversation_with_citations(
        self, session_client: APIClient, corpus: tuple[Document, Document]
    ) -> None:
        ask(session_client, "What am I missing for this role?")

        response = session_client.get("/api/v1/chat/messages/")

        assert response.status_code == 200
        roles = [m["role"] for m in response.data["messages"]]
        assert roles == ["user", "assistant"]
        assert response.data["messages"][1]["citations"]
        assert response.data["demo_mode"] is True

    def test_history_is_scoped_to_the_session(
        self,
        client: APIClient,
        session_client: APIClient,
        other_session: Session,
        corpus: tuple[Document, Document],
    ) -> None:
        from tests.conftest import authenticate

        ask(session_client, "What am I missing for this role?")

        intruder = authenticate(client, other_session)
        response = intruder.get("/api/v1/chat/messages/")

        assert response.data["messages"] == []

    def test_trace_of_another_session_is_not_found(
        self,
        client: APIClient,
        session_client: APIClient,
        other_session: Session,
        corpus: tuple[Document, Document],
    ) -> None:
        from tests.conftest import authenticate

        message_id = first(ask(session_client, "What am I missing for this role?"), "done")[
            "message_id"
        ]

        intruder = authenticate(client, other_session)
        response = intruder.get(f"/api/v1/traces/{message_id}/")

        assert response.status_code == 404
        assert response.data["error_code"] == "not_found"


@pytest.mark.django_db
class TestValidation:
    def test_empty_message_is_rejected(self, session_client: APIClient) -> None:
        response = session_client.post("/api/v1/chat/", {"message": "   "}, format="json")

        assert response.status_code == 400
        assert response.data["error_code"] == "empty_message"

    def test_overlong_message_is_rejected(self, session_client: APIClient) -> None:
        response = session_client.post("/api/v1/chat/", {"message": "x" * 2001}, format="json")

        assert response.status_code == 400
        assert response.data["error_code"] == "message_too_long"

    def test_chat_without_a_session_is_401(self, client: APIClient) -> None:
        response = client.post("/api/v1/chat/", {"message": "hello"}, format="json")

        assert response.status_code == 401
        assert response.data["error_code"] == "session_required"


@pytest.mark.django_db
def test_usage_reports_the_ledger_and_the_ceiling(
    session_client: APIClient, corpus: tuple[Document, Document]
) -> None:
    ask(session_client, "What am I missing for this role?")

    response = session_client.get("/api/v1/usage/")

    assert response.status_code == 200
    assert response.data["backend"] == "fake"
    assert response.data["calls"], "the stub writes a ledger row too"
    assert float(response.data["daily"]["ceiling_usd"]) > 0


@pytest.mark.django_db
def test_session_totals_track_the_ledger(session: Session) -> None:
    """The usage meter reads `Session.cost_usd`, not a sum over the ledger.

    Two writers for one fact drift, and the symptom is a meter stuck at zero
    next to a ledger showing spend — which reads as a broken meter rather than
    as a missing write. One writer, so it cannot happen.
    """
    from apps.observability import ledger

    ledger.record(
        session_id=session.pk,
        message_id=None,
        purpose="chat",
        model="claude-opus-5",
        backend="anthropic",
        input_tokens=1000,
        output_tokens=500,
        cost_usd=Decimal("0.017500"),
    )

    session.refresh_from_db()
    assert session.tokens_used == 1500
    assert session.cost_usd == Decimal("0.017500")


@pytest.mark.django_db
class TestThrottling:
    """The throttle is wired to the view, and keyed by session rather than IP.

    Rates are set on the throttle class, not through `settings.REST_FRAMEWORK`.
    DRF binds `SimpleRateThrottle.THROTTLE_RATES` at class-definition time, so a
    settings override does not reach it — an earlier version of these tests
    "passed" while the configured rate was never in effect, which is the failure
    mode a throttle test exists to rule out. `__init__` short-circuits `get_rate`
    when a `rate` attribute is present, so this is the honest lever.
    """

    @pytest.fixture(autouse=True)
    def _clean_cache(self) -> Any:
        from django.core.cache import cache

        cache.clear()
        yield
        cache.clear()

    def _limit(self, monkeypatch: pytest.MonkeyPatch, rate: str) -> None:
        for throttle in (ChatBurstThrottle, ChatSustainedThrottle):
            monkeypatch.setattr(throttle, "rate", rate, raising=False)

    def test_the_limit_is_actually_enforced(
        self,
        session_client: APIClient,
        corpus: tuple[Document, Document],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        self._limit(monkeypatch, "2/min")

        codes = [
            session_client.post(
                "/api/v1/chat/", {"message": "Am I a good fit?"}, format="json"
            ).status_code
            for _ in range(3)
        ]

        assert codes == [200, 200, 429]

    def test_a_generous_limit_does_not_throttle(
        self,
        session_client: APIClient,
        corpus: tuple[Document, Document],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The control. Without it the test above passes if everything 429s."""
        self._limit(monkeypatch, "100/min")

        codes = [
            session_client.post(
                "/api/v1/chat/", {"message": "Am I a good fit?"}, format="json"
            ).status_code
            for _ in range(3)
        ]

        assert codes == [200, 200, 200]

    def test_the_rejection_uses_the_project_error_envelope(
        self,
        session_client: APIClient,
        corpus: tuple[Document, Document],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A client should only ever have to parse one error shape."""
        self._limit(monkeypatch, "1/min")
        session_client.post("/api/v1/chat/", {"message": "Am I a good fit?"}, format="json")

        blocked = session_client.post(
            "/api/v1/chat/", {"message": "Am I a good fit?"}, format="json"
        )

        assert blocked.status_code == 429
        assert blocked.data["error_code"] == "rate_limited"
        assert blocked.data["retry_after"] > 0

    def test_the_key_is_the_session_not_the_ip(
        self,
        client: APIClient,
        session_client: APIClient,
        other_session: Session,
        corpus: tuple[Document, Document],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Two tenants share one IP in every office. They must not share a bucket."""
        from tests.conftest import authenticate

        self._limit(monkeypatch, "1/min")

        session_client.post("/api/v1/chat/", {"message": "Am I a good fit?"}, format="json")
        assert (
            session_client.post(
                "/api/v1/chat/", {"message": "Am I a good fit?"}, format="json"
            ).status_code
            == 429
        )

        other = authenticate(client, other_session)
        assert (
            other.post("/api/v1/chat/", {"message": "Am I a good fit?"}, format="json").status_code
            == 200
        )
