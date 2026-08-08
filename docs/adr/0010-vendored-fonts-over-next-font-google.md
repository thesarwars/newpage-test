# ADR-0010: Vendor the Geist woff2 files rather than use `next/font/google`

**Status:** accepted · **Date:** 2026-08-08 · **Milestone:** M6

## Context

`docs/PLAN.md` §7 specifies "Geist Sans / Geist Mono, self-hosted via
`next/font/local` (no CDN — see PII)". The `create-next-app` scaffold instead
uses `next/font/google`. Those look like the same thing and are not.

## What was actually measured

`next/font/google` **does** self-host: it downloads the woff2 at build time and
rewrites `@font-face` to `/_next/static/media/*.woff2`. Grepping a production
build for `fonts.gstatic.com` returns nothing. **So the plan's stated reason —
"no CDN, see PII" — does not distinguish the two options.** There is no runtime
request either way, and the privacy argument the plan gives is not the argument
that matters.

The argument that matters is build hermeticity, and it was verified rather than
assumed: a production build run behind a dead proxy **fails**:

```
Error: next/font: error: Failed to fetch Geist from Google Fonts
```

There is no on-disk cache — the loader's caches are per-process Maps that only
deduplicate the server and client compile passes. It retries three times, falls
back to Arial metrics in `dev` with a warning, and re-throws in a production
build.

This project runs `pnpm build` in **two** places that must not depend on
fonts.googleapis.com being reachable: the `docker` CI job (`docker compose
build`) and the `frontend` CI job. A third-party outage would turn a green build
red for a reason unrelated to the change under test, and `docker build
--network=none` would fail outright.

## Decision

Vendor `Geist-Variable.woff2` and `GeistMono-Variable.woff2` into
`frontend/app/fonts/` and load them with `next/font/local`, with `OFL.txt`
alongside. This is what §7 already specified; only the *reason* recorded in the
plan changes.

## Alternatives

**`pnpm add geist`** (Vercel's own package) produces byte-identical output and
is genuinely offline — it is a thin wrapper over `next/font/local` around the
same files. Declined because it is a dependency whose entire content is two font
files I can commit directly, it hard-codes `display`, `preload` and the CSS
variable names, and under ADR-0009's test "what I would otherwise write and
maintain" is: nothing. Two binaries in git, no package.

**Stay on `next/font/google`.** Declined: it trades 138 KB of committed binaries
for a network dependency in two CI jobs, and the thing traded away is the
property that the build is reproducible offline.

## Consequences

138 KB of binaries in the repository and a manual font-upgrade path — a real
cost, paid once. The build no longer reaches the network for fonts, so
`docker compose build` and CI are hermetic with respect to typography.
