# Career Intelligence Assistant

A conversational RAG assistant over a résumé and multiple job descriptions. Upload
your CV and the postings you're considering, then ask about fit, skill gaps,
experience alignment and interview preparation — with every answer backed by
clickable, character-exact citations into the source document.

**Stack:** Django 5.2 + DRF · PostgreSQL 17 + pgvector · local ONNX embeddings ·
Next.js 16 · Claude Opus 5.

> ### 🚧 Build status — in progress
>
> This README documents **what actually works today**, not the finished product.
> Seven of fourteen planned milestones are complete: ingest, indexing,
> retrieval, grounded streaming chat, and the web shell — documents, upload,
> demo seeding and deletion. The conversation UI is next
> — [What's built](#whats-built-today) is exact about the line.
>
> One thing to know before reading further: the plan required a 30-minute spike
> against the real API to confirm the citation response shape before writing any
> streaming code. **No API key was available, so that spike never ran** — see
> [The one unverified assumption](#the-one-unverified-assumption).
>
> The full design — including every decision below and the ones not yet
> implemented — is in **[docs/PLAN.md](docs/PLAN.md)**.

---

## Quick setup

Requires Docker and Docker Compose. Nothing else — no Python, Node or Postgres
on your machine.

```bash
git clone https://github.com/thesarwars/newpage-test.git
cd newpage-test
make up
```

That builds the images, generates a local `.env` with fresh random secrets,
starts three containers and applies migrations. First build takes a few minutes
(it bakes a ~130 MB embedding model into the image); afterwards a cold start is
about 30 seconds.

```
web  →  http://localhost:3000     (Next.js scaffold — no UI yet, see build status)
api  →  http://localhost:8000/readyz
```

### An API key is optional

`ANTHROPIC_API_KEY` is the **only** key this project ever asks for, and it is not
required. Everything currently built runs without it — including chat: parsing,
normalization, chunking, embedding, retrieval, requirement extraction, gap
analysis, the SSE stream, source chips, the retrieval trace and **working
citations** are all local.

With no key, free-text generation is served by a stub assembled from the passages
retrieval actually selected, with **real character offsets into your real
documents** — so clicking a citation demonstrably works rather than looking like
it might. Every response carries `demo_mode: true` on its first frame, so a stub
can't be mistaken for model output.

What genuinely needs a key: generated prose, `make smoke-live`, and the
server-side `fallbacks` refusal path. Set it in `.env` and restart.

### Useful targets

| Command | What it does |
|---|---|
| `make up` | Build, start, migrate, print URLs |
| `make test` | Backend suite — no network, no API key |
| `make eval` | Retrieval evaluation against the golden set |
| `make smoke-sse` | Stream one chat answer through `curl` — no key needed |
| `make smoke-live` | One real API round-trip; verifies the citation offset contract (**needs a key**) |
| `make lint` | ruff, ruff format, mypy |
| `make down` / `make clean` | Stop / stop and drop the database volume |
| `make logs` | Tail structured logs |

**Everything runs in the container.** `make` is the only supported entry point —
host Python is likely 3.14, where `onnxruntime` has no wheels. Running `pytest`
directly on the host will fail with a confusing dependency error.

---

## What's built today

Complete and tested:

| Milestone | What landed |
|---|---|
| **M0** Scaffold | Compose (db/api/web), multi-stage Dockerfiles, Makefile, CI across five jobs |
| **M1** Ops spine | Anonymous session tenancy, structured logging with PII redaction, error envelope, `/healthz` `/readyz` `/version` |
| **M2** Ingest | Upload validation, PDF/DOCX/text parsers, text normalization, section detection, prompt-injection scanning |
| **M3** Chunking & embeddings | Structure-aware chunking on the model's real tokenizer, structural breadcrumbs, local ONNX embeddings, HNSW + GIN indexes |
| **M4** Retrieval & evaluation | Hybrid dense + lexical retrieval with RRF, per-job quotas, section anchors, an evidence floor, deterministic scope resolution and intent routing, keyless requirement extraction, and a golden-set eval gating CI |
| **M5** LLM & streaming chat | Single-call-site Anthropic gateway with a `finally` cost ledger, frozen SHA-pinned system prompt, context assembly, native-citation offset mapping, SSE streaming, per-session throttles, a daily spend ceiling, and a keyless stub backend that still cites real spans |
| **M6** Web shell | Workspace layout, document rail, drag-and-drop upload with client-side rejection, paste fallback, one-click demo seeding, delete-everything, three-state theming, and a design system whose contrast is computed rather than asserted |

Not built yet: the conversation UI and the evidence panel (M7), the Fit Board
(M8) and the Gap Matrix (M9). The chat API works today — `make smoke-sse`
streams a grounded, cited answer through `curl`.

### Retrieval quality, measured

`make eval` runs 32 golden questions against the demo corpus. **No API key
required** — every metric is deterministic.

| arm | hit-rate@12 | MRR@12 |
|---|---|---|
| dense only | 1.000 | 0.337 |
| lexical only | 0.833 | 0.640 |
| **RRF fused** | **1.000** | **0.497** |

That table is the justification for hybrid retrieval, and it is why the lexical
arm keeps its GIN index. Dense finds the right chunk every time but ranks it
poorly; lexical misses more often but ranks precisely when it hits. Fusion keeps
dense's recall and most of lexical's precision.

Scope resolution ("Job #2" → the right document) and out-of-scope refusal are
both **1.000**. Gap analysis scores **F1 0.875** against hand labels versus
**0.482** for a naive top-k baseline — the delta is the evidence for the claim
that *vector search cannot retrieve absence*: the chunks most similar to "what
am I missing?" are the ones describing what the candidate has.

CI fails the build if fused hit-rate drops below 0.95 or routing below 1.000.
The committed numbers live in `backend/evals/baseline.json`.

### The one unverified assumption

`docs/PLAN.md` sequences a 30-minute spike against the real API **before** any
streaming code: send a two-block document request, confirm the `char_location`
citation shape, record it as a fixture. **No `ANTHROPIC_API_KEY` was available at
any point during this build, so it never ran.**

That leaves exactly one thing in this repository asserted rather than measured:
the field names in `llm/gateway.py::_parse_event`, which come from the documented
response shape. Everything downstream of that function is verified against real
documents in CI — the offset arithmetic, the rejection of citations that don't
match the source text, the numbering, and the full SSE path.

Rather than paper over it:

- The parser reads every field through `getattr`, so a name mismatch costs the
  clickable marks, never the answer or the request.
- `backend/tests/fixtures/anthropic/raw_stream_events.json` pins the assumption
  and carries a `_warning` field saying the test passes *because* the fixture and
  the parser would share any error.
- **`make smoke-live` is the spike, as one command.** It sends the request, prints
  every raw event, and verifies `block_text[start:end] == cited_text` for each
  citation — exiting non-zero if it fails, so it is a gate rather than something
  to read and nod at. `make smoke-live ARGS=--write-fixture` re-records the
  fixture; `make test` then fails if the real shape and the documented one
  disagree, which is the point.

If it turns out `char_location` is unusable, the documented fallback is
server-numbered `[S1]` markers with a regex mapper — about twenty lines, and
visibly worse UX.

### Try what exists

```bash
# Create a session, upload a résumé and a job description
curl -sS -c /tmp/jar -X POST http://localhost:8000/api/v1/sessions/
curl -sS -b /tmp/jar -F "file=@backend/fixtures/demo/resume.pdf" -F "kind=resume" \
     http://localhost:8000/api/v1/documents/
curl -sS -b /tmp/jar -F "file=@backend/fixtures/demo/job_2_vertex.pdf" -F "kind=job" \
     http://localhost:8000/api/v1/documents/
```

The response carries detected sections with character offsets, boilerplate flags,
and injection findings. `backend/fixtures/adversarial_job.pdf` contains a real
prompt-injection payload rendered in white-on-white text — upload it to see the
scanner catch it.

Then ask it something. `make smoke-sse Q="What am I missing for this role?"` does
this for you, or:

```bash
curl -N -sS -b /tmp/jar -X POST http://localhost:8000/api/v1/chat/ \
     -H "Content-Type: application/json" \
     -d '{"message": "What am I missing for this role?"}'
```

```
event: status    data: {"phase":"resolving", ...}
event: scope     data: {"job_ids":[...],"intent":"gap","resolved_from":"Job #2","demo_mode":true}
event: sources   data: {"chunks":[{"id":...,"section":"REQUIREMENTS","preview":"..."}]}
event: delta     data: {"text":"You're short on production "}
event: citation  data: {"index":1,"answer_char":267,"char_start":104,"char_end":157,"cited_text":"..."}
event: done      data: {"usage":{...},"ttft_ms":14,"grounding":{"citations":3,"max_score":0.543}}
```

`sources` lands **before any text exists** — grounding visibly happens first, so
the answer reads as derived rather than generated. Every `citation` carries
offsets into `Document.normalized_text`, verified server-side against the stored
text before it is sent; one that doesn't match is dropped rather than shown,
because a mark on the wrong span is worse than a missing one.

Two things cost zero tokens by design: an out-of-scope question
(`"What's the weather?"`) and a question retrieval can't support are both refused
before any call is made. `GET /api/v1/traces/{message_id}/` shows exactly why
each passage was chosen and what the message cost.

### Current API

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/v1/sessions/` | Create (or return) an anonymous workspace |
| `GET` `DELETE` | `/api/v1/sessions/current/` | Hydrate / hard-delete everything |
| `GET` `POST` | `/api/v1/documents/` | List / upload |
| `POST` | `/api/v1/documents/paste/` | Paste text — the fallback every parse error points at |
| `GET` `PATCH` `DELETE` | `/api/v1/documents/{id}/` | Detail (incl. `normalized_text`) / rename / delete |
| `POST` | `/api/v1/chat/` | **SSE.** Ask a question; streams status, scope, sources, deltas, citations, done |
| `GET` | `/api/v1/chat/messages/` | Conversation history with citations, for a page reload |
| `GET` | `/api/v1/traces/{message_id}/` | Full retrieval trace + the LLM calls that message made |
| `GET` | `/api/v1/usage/` | Session totals, daily spend against the ceiling, last 20 calls |
| `GET` | `/healthz` `/readyz` `/version` | Liveness / readiness / build identity |

---

## Architecture

```
┌──────────────────────── browser ────────────────────────┐
│  Next.js 16  ·  workspace  ·  gap matrix          (M6+) │
└───────┬─────────────────────────────────┬───────────────┘
        │ fetch(credentials:'include')    │ SSE            (M5)
        ▼                                 ▼
┌────────────────── api (gunicorn, gthread) ──────────────────┐
│ core/       session cookie · request-id · structlog · errors │
│ documents/  validate → parse → normalize → scan → section    │
│             → chunk → embed                                  │
│ rag/        embeddings (local ONNX)                          │
│             dense · lexical · RRF · quota · anchors    (M4)  │
│ llm/        AnthropicGateway — the ONLY call site      (M5)  │
└───────┬──────────────────────────────────────┬──────────────┘
        │ SQL (rows + vectors + tsvector)      │ HTTPS  (M5)
        ▼                                      ▼
┌──────────────────────┐            ┌─────────────────────┐
│ Postgres 17 +pgvector│            │  api.anthropic.com  │
│ HNSW + GIN indexes   │            │  (optional)         │
└──────────────────────┘            └─────────────────────┘
```

Three services, and there is deliberately no fourth. No message broker: ingest is
bounded by hard intake caps (10 MB, 30 pages) to a few seconds, so a queue would
add two stateful services and a "stuck in parsing forever" failure class in
exchange for latency nobody notices. No separate vector database: pgvector lives
in the same Postgres, so a retrieval query is one query plan and one transaction.

### The ingest pipeline

```
validate → parse → normalize → scan → sections → chunk → embed → index
```

Everything downstream of `normalize` indexes into a single canonical string,
`Document.normalized_text`. Sections, chunks and (in M5) the model's own citation
offsets are all character positions in that one string. The invariant

```
normalized_text[chunk.char_start:chunk.char_end] == chunk.text
```

is property-tested with Hypothesis and verified in SQL against stored rows. The
entire evidence-panel feature is that one assertion — if it breaks, citations
highlight the wrong span and nothing raises an error.

---

## Engineering standards

**Followed.** Typed protocols at every swap seam (embedder, requirement
extractor, task runner). `mypy --strict` on the packages that carry logic.
Structured JSON logs with PII redaction on by default. One canonical text
representation. Containerised, one command to run. CI on every push: ruff, mypy,
tests against a real pgvector service, frontend lint/typecheck/build, a full
compose build, and secret scanning over complete git history.

**Deliberately skipped**, each with the trigger that would change my mind:

| Skipped | Trigger to add |
|---|---|
| Authentication | Any real user data. `session_id` is already the tenant column, so it's a middleware swap plus Postgres RLS. |
| Celery / Redis | Ingest exceeding ~20s, batch upload, or OCR. |
| Dedicated vector DB | >1M chunks, or measured ANN recall/latency problems. |
| Orchestration framework | Never at this scope — see below. |
| OCR | Scanned PDFs are rejected with a specific error pointing at the paste fallback. |
| `/metrics`, OpenTelemetry | Multi-replica deployment. |

### Testing

**218 tests**, no network and no API key required.

| Area | Tests |
|---|---|
| Injection scanning | 28 |
| Document API (incl. cross-tenant probes) | 27 |
| Retrieval: fusion, quotas, anchors, floor, routing | 40 |
| Chunking, offsets, tokenizer | 24 |
| Section detection | 21 |
| Requirement extraction & matching | 15 |
| Parsers & upload validation | 15 |
| Sessions | 11 |
| Normalization (property-based) | 10 |
| Logging, redaction, tenancy, health | 20 |

Roughly 3,600 lines of application code against 1,700 lines of tests. Three
properties are treated as non-negotiable: the chunk-offset invariant, the tenancy
guard (session A must not reach session B's data, and a foreign UUID must 404
identically to a missing one), and "CI cannot spend money" — the test settings
pin the LLM backend to a fake, and that assertion caught a real misconfiguration
on its first run.

The embedder is **not** mocked. fastembed on CPU is deterministic, so tests
exercise the true encoding path; mocking it would mean the retrieval tests assert
nothing.

---

## Key technical decisions

> **Note to the reviewer:** the sections below record decisions made during the
> build. They are being expanded into fuller reasoning as the remaining
> milestones land — see [docs/PLAN.md](docs/PLAN.md) §2 and §4 for the complete
> argument behind each, including the alternatives considered.

**Local embeddings, not a hosted embedder.** Anthropic has no embeddings
endpoint, so any hosted embedder means a *second* vendor key. A reviewer who
doesn't have one gets an app that doesn't run. `bge-small-en-v1.5` runs on CPU
via ONNX with the weights baked into the image, which also makes CI hermetic and
retrieval deterministic. It sits behind an `Embedder` protocol, so swapping to
Voyage or OpenAI is one class plus a documented reindex.

**Postgres + pgvector, not a dedicated vector database.** The dominant retrieval
operation here is a metadata filter (`document_id IN (…)`, `session_id = …`) —
that's SQL, not a bolt-on filter DSL. And the lexical arm of hybrid retrieval has
to live in the same query plan for rank fusion to work. One datastore, one
transaction, one backup story.

**No orchestration framework.** Prompt caching is a byte-exact prefix match and
tenancy is a SQL predicate — those are the two things this application most needs
to own precisely, and a framework abstracts both.

**Real tokenizer, not a character proxy.** A 4-chars-per-token estimate reads
`"Production Kubernetes experience, not just running kubectl."` as 14 tokens; the
encoder sees 17. Scaled to a 512-token budget that's roughly 100 tokens of every
chunk's tail silently dropped by the encoder — a retrieval bug with no symptom.
Chunking asks the model's own tokenizer and asserts against its limit.

**Structural breadcrumbs, not generated ones.** A bullet reading *"Reduced p99
latency from 1.4s to 380ms"* carries no signal about which employer. Anthropic's
contextual-retrieval recipe fixes this with one LLM call per chunk; the structure
was already in the section headings, so deriving `[Résumé — Experience — Senior
Backend Engineer, Meridian Logistics]` costs zero tokens. I'm deliberately *not*
claiming the published 35–49% improvement figure — that's for the generated
variant, and M4's evaluation will measure what this actually buys.

**Prompt injection is treated as a real threat.** The adversary is the job
posting, not the user. Defence is structural first: document content only ever
enters as `document` content blocks in a user turn, never concatenated into an
instruction position. On top of that, an ingest-time scanner catches
imperative-override patterns, invisible characters and — using per-character font
and colour data from `pdfplumber` — text rendered white-on-white or at 0pt.
Flagged spans are excluded from retrieval *and shown to the user*, because a
silent filter is a guardrail while an auditable one is a product feature. The
scanner will miss a naturally-phrased injection; that's stated plainly rather
than papered over.

---

## What I'd do differently / next

Immediate: the LLM gateway with a single call site and a cost ledger, then
streaming chat with native citations, then the frontend.

**The golden set is the weakest part of the evaluation** and I would replace it
first. Thirty-two questions drafted alongside the implementation measure
*regression*, not quality — they are not adversarial, and they were written by
someone who knew how the retriever worked. Independently authored questions,
including negations and deliberately ambiguous references, would be worth more
than any amount of further tuning.

The image is currently **2.2 GB** (onnxruntime plus baked weights). That's a real
cost to a reviewer's first `make up` and I haven't attacked it yet.

Page-level citation offsets were dropped: mapping them honestly requires
normalizing per page, and nothing consumes them today. A nullable column nobody
fills is worse than no column.

---

## Secrets and privacy

A résumé is PII by construction, and the design treats it that way rather than
bolting on a policy afterwards.

- **No third-party analytics, no session replay, no CDN fonts.** The only
  external egress at runtime is `api.anthropic.com`, and only when a key is set.
  That claim needed defending rather than asserting: Next.js collects anonymous
  telemetry and posts it to Vercel **by default**, so `NEXT_TELEMETRY_DISABLED`
  is set in the image, in compose and in CI. An app holding somebody's résumé
  does not get to phone home about itself, even anonymously.
- **The uploaded file is never written to disk.** It is validated, parsed and
  normalized in memory, and the original bytes are dropped — nothing downstream
  needs them, because every offset in the system indexes into `normalized_text`
  rather than into the source file. A test enumerates every model in the project
  and asserts none carries a `FileField`, so this stays true by build failure
  rather than by vigilance.
- **Logs carry ids and content hashes, never document text.** A `log_safe()`
  helper is the only sanctioned way to reference a document in a log — it
  physically cannot emit text because it never receives it — and a redaction
  processor scrubs emails, phone numbers and URLs as a second layer.
- **"Delete everything" is a first-class control**, not a buried setting.
  Sessions carry a 7-day TTL with a `purge_expired` command that makes the
  retention claim true rather than aspirational.
- **Nothing password-shaped is committed.** `.env.example` ships blank values;
  `make up` generates them locally into a gitignored `.env`. Compose *requires*
  the database password with no fallback, so a missing value fails loudly rather
  than silently starting Postgres with a password every checkout would know.

> One historical note, since a scanner will find it: commit `71d0c2e` contained
> `POSTGRES_PASSWORD=cia` as a local development default. It was a container
> password bound to a non-default localhost port that never protected anything
> reachable, and there is nothing to rotate. It has been removed going forward.

---

## Repository layout

```
├── docs/
│   ├── PLAN.md          the full design and build plan — start here
│   └── ASSIGNMENT.md    the original brief
├── backend/
│   ├── apps/core/       session tenancy, logging, errors, health
│   ├── apps/documents/  parsers, normalize, sanitize, chunking, ingest
│   ├── apps/rag/        embeddings (retrieval lands in M4)
│   ├── fixtures/        synthetic demo corpus + adversarial fixture
│   ├── scripts/         fixture generation (committed, so the PDFs are auditable)
│   └── tests/           unit + api
├── frontend/            Next.js scaffold (UI lands in M6+)
├── docker-compose.yml
└── Makefile
```

The demo corpus is entirely synthetic — invented people, invented companies —
and generated by a committed script, so the contents of those PDFs (including the
injection payload) are auditable rather than opaque binaries.
