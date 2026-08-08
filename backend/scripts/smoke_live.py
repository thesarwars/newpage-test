"""The citations spike, as a script — `make smoke-live`.

docs/PLAN.md §4.7 requires this to run *before* any streaming code, to confirm
the `char_location` response shape against the real API and record it as a
fixture. No `ANTHROPIC_API_KEY` was available during the build, so it did not
run, and `llm/gateway.py::_parse_event` currently encodes the documented shape
rather than an observed one.

This script closes that gap in one command. It sends a deliberately minimal
two-block document request — a fake résumé and a fake job posting, no real
person's data leaves the machine — asks a question that forces a citation, and:

1. prints every raw event so the shape is visible, not inferred;
2. rewrites `tests/fixtures/anthropic/raw_stream_events.json` from the real
   response;
3. verifies the offset contract end to end: for each citation, it checks that
   `block_text[start_char_index:end_char_index] == cited_text`. That is the
   single assumption the entire evidence panel rests on, and it is the thing a
   printout of field names would not actually prove.

Exit code 1 if the contract fails, so this is usable as a gate rather than as
something to read and nod at.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "tests"
    / "fixtures"
    / "anthropic"
    / "raw_stream_events.json"
)

RESUME = """PROFESSIONAL SUMMARY
Backend engineer with eight years building payment and logistics systems.

EXPERIENCE
Senior Backend Engineer, Meridian Logistics (2022 to Present)
Reduced p99 latency from 1.4s to 380ms across the dispatch API.

SKILLS
Go, Python, PostgreSQL, Kafka, Terraform, AWS
"""

JOB = """REQUIREMENTS
- 5+ years running Kubernetes in production
- Strong PostgreSQL skills, including query tuning
"""

QUESTION = (
    "Name one requirement in the job posting that the résumé does not evidence. "
    "Quote the requirement."
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=os.environ.get("CIA_CHAT_MODEL", "claude-opus-5"))
    parser.add_argument(
        "--write-fixture",
        action="store_true",
        help="overwrite the recorded fixture with this response",
    )
    args = parser.parse_args()

    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        print("ANTHROPIC_API_KEY is not set — this is the one target that needs a real key.")
        print("Everything else (make test, make eval, the whole app) runs without one.")
        return 2

    import anthropic

    client = anthropic.Anthropic(api_key=key)
    blocks = [
        ("Résumé", RESUME),
        ("Job posting", JOB),
    ]

    raw_events: list[dict[str, Any]] = []
    print(f"→ {args.model}, two document blocks, citations enabled\n")

    # Assembled as a plain dict and splatted, mirroring llm/gateway.py — the
    # request body is data here, not a call signature to satisfy.
    payload: dict[str, Any] = {
        "model": args.model,
        "max_tokens": 1024,
        "system": [{"type": "text", "text": "Answer only from the supplied documents. Be brief."}],
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "document",
                        "title": title,
                        "source": {"type": "text", "media_type": "text/plain", "data": text},
                        "citations": {"enabled": True},
                    }
                    for title, text in blocks
                ]
                + [{"type": "text", "text": QUESTION}],
            }
        ],
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": "low"},
    }

    with client.beta.messages.stream(**payload) as stream:
        for event in stream:
            raw_events.append(_dictify(event))
        final = stream.get_final_message()

    for recorded in raw_events:
        print(json.dumps(recorded, ensure_ascii=False)[:300])

    citations = _citations(raw_events)
    print(f"\n{len(citations)} citation(s) returned.\n")

    ok = _verify(citations, blocks)

    if args.write_fixture:
        FIXTURE.write_text(
            json.dumps(
                {
                    "_warning": None,
                    "_recorded_at": final.id if hasattr(final, "id") else None,
                    "_model": args.model,
                    "events": raw_events,
                    "final_message": _dictify(final),
                },
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"wrote {FIXTURE}")
        print("Now run `make test` — tests/unit/test_gateway.py will fail if the")
        print("documented shape and the real one disagree, which is the point.")

    return 0 if ok else 1


def _dictify(value: Any) -> Any:
    """SDK object → plain JSON, without assuming a particular serialiser exists."""
    if hasattr(value, "model_dump"):
        return json.loads(value.model_dump_json())
    if isinstance(value, list):
        return [_dictify(v) for v in value]
    if isinstance(value, dict):
        return {k: _dictify(v) for k, v in value.items()}
    return value


def _citations(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    found = []
    for event in events:
        delta = event.get("delta") or {}
        if delta.get("type") == "citations_delta":
            found.append(delta.get("citation") or {})
    return found


def _verify(citations: list[dict[str, Any]], blocks: list[tuple[str, str]]) -> bool:
    """The offset contract. Field names are cheap to eyeball; this is not."""
    if not citations:
        print("✗ no citations came back — the fallback in docs/PLAN.md §4.7 applies.")
        return False

    ok = True
    for citation in citations:
        if citation.get("type") != "char_location":
            print(f"✗ unexpected citation type: {citation.get('type')!r}")
            ok = False
            continue

        index = citation.get("document_index")
        start = citation.get("start_char_index")
        end = citation.get("end_char_index")
        cited = citation.get("cited_text", "")

        if index is None or not isinstance(index, int) or not 0 <= index < len(blocks):
            print(f"✗ document_index {index!r} is outside the blocks we sent")
            ok = False
            continue

        slice_ = blocks[index][1][start:end]
        if slice_ == cited:
            print(f"✓ block {index} [{start}:{end}] slices exactly to the cited text")
        else:
            print(f"✗ block {index} [{start}:{end}] sliced {slice_!r}, cited {cited!r}")
            ok = False

    return ok


if __name__ == "__main__":
    sys.exit(main())
