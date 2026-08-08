# Anthropic fixtures

## `raw_stream_events.json` — ⚠ UNVERIFIED

`docs/PLAN.md` §4.7 mandates a 30-minute spike against the real API **before**
any streaming code, to confirm the `citations_delta` / `char_location` field
names and record the response here. **That spike has not run** — no
`ANTHROPIC_API_KEY` was available at any point during this build.

So these events are written from the documented response shape, not observed
from a real one. They pin what `llm/gateway.py::_parse_event` currently
*believes*, which means:

- the parser has a test, and the citation → offset arithmetic downstream of it
  is genuinely verified;
- but if the live shape differs in a field name, this fixture is wrong in
  exactly the same way the parser is, and the test passes anyway.

That is the one thing in this repository whose contract is asserted rather than
measured, and it is called out in the README for the same reason it is called
out here.

**To resolve it:** set a key and run `make smoke-live`. It performs the spike —
one real two-block document request — and rewrites this file from the actual
response. If any field name differs, `tests/unit/test_gateway.py` starts failing
and the fix is a two-line change in `_parse_event`.

The documented fallback, if `char_location` turns out to be unusable: server-numbered
`[S1]` markers plus a regex mapper (~20 lines, visibly worse UX).

## `citations_stream.json`, `refusal_stream.json`

Normalised event streams for `FakeAnthropic(mode="replay")`. These are *our*
event vocabulary (`llm/types.py`), not Anthropic's wire format, so they are not
affected by the uncertainty above — they exist to drive the SSE layer, the
citation resolver and the refusal path deterministically in tests.
