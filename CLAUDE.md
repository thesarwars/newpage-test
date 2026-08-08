# House rules

These are the standing rules an AI assistant works under in this repo. They are
committed because README §(g) claims a repeatable AI-assisted method, and a claim
like that should point at an artifact rather than a testimonial.

`docs/PLAN.md` is the spec. If a change contradicts the plan, the plan is updated
in the same commit with the reason — the plan is never silently diverged from.

## Non-negotiable

- **Type hints on every function signature.** `mypy --strict` passes on
  `apps/rag`, `apps/documents`, `apps/analysis`, `apps/chat`, `llm/`.
- **No bare `except:`, no `except Exception: pass`.** Catch the specific error or
  let it propagate. An error that reaches a user must carry an `error_code`.
- **Protocol at every swap seam.** Embedder, requirement extractor, LLM client,
  task runner. A seam exists so the alternative is a class, not a refactor.
- **One Anthropic call site** — `llm/gateway.py`. Every call writes an `LLMCall`
  row in a `finally` block, including failures and refusals.
- **Never log document text.** Chunk ids and content hashes only. `log_safe()` is
  the only sanctioned way to log a document reference, and it has a test.
- **Tests alongside the code they constrain**, in the same commit. Invariant tests
  (chunk offsets, citation mapping, tenancy) are written *before* the
  implementation, so generated code has something to fail against.
- **No new dependency without an ADR** in `docs/adr/`. "It's convenient" is not a
  reason; "here is what I would otherwise write and maintain" is.
- **No version numbers pinned by hand.** Floors in `pyproject.toml` /
  `package.json`; exact versions come from `uv.lock` / `pnpm-lock.yaml`. A
  hallucinated pin fails the build on hour one.

## API facts that go stale fastest

Model APIs change faster than training data. Verify against current docs, never
from memory. Known-current for this repo (Claude Opus 5):

- `temperature`, `top_p`, `top_k`, `budget_tokens` → **400**. Do not send them.
- Assistant-turn prefill → **400**. Use `output_config.format` instead.
- `thinking={"type": "adaptive"}`. Thinking is on by default and **shares
  `max_tokens` with visible text** — size the cap for both.
- `citations` and `output_config.format` are **mutually exclusive** (400 together).
  Chat uses citations; structured extraction uses format. Never both.
- `stop_reason` can be `"refusal"`; `stop_details` can be `None` even then.

## Don'ts, learned the hard way

- **Don't write the eval golden set.** A model grading its own retrieval is not
  evidence. Golden questions are authored by hand, before retrieval is tuned.
- **Don't reach for a framework.** LangChain and a dedicated vector DB were both
  proposed more than once and declined both times; see ADR-0001 and ADR-0002.
- **Don't add scope because it would be impressive.** Every addition needs a rubric
  line or a named failure mode it removes. ~10h was cut from the plan before
  implementation for failing this test; §13 lists it.
- **Don't write the reasoning being evaluated.** README sections (c) through (h)
  are hand-written. Generated prose there defeats the point of the exercise.

## Workflow

- One milestone per commit (`docs/PLAN.md` §13), conventional-commit messages, so
  a bad generation is one `git revert`.
- **No `Co-authored-by` trailer.**
- Run `make lint test` before committing. CI runs the same commands.
