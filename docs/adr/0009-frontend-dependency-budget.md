# ADR-0009: The frontend dependency budget

**Status:** accepted · **Date:** 2026-08-08 · **Milestone:** M6

## Context

`CLAUDE.md` forbids adding a dependency without an ADR, and specifies the test:
"it's convenient" is not a reason; **"here is what I would otherwise write and
maintain"** is. The frontend starts from `create-next-app` with three runtime
dependencies (next, react, react-dom) and needs to become a workspace shell.

This ADR applies that test to every candidate, including the ones declined —
because the declines are the part that keeps the budget meaningful.

## Accepted

### `next-themes` (^0.4.6)

**What I would otherwise write:** a theme store, a `localStorage` read, a
`matchMedia` listener for the `system` state, and — the part that is genuinely
fiddly — a **synchronous inline script that runs before first paint**, because
any theme resolution that waits for React produces a flash of the wrong theme on
every cold load. That script has to be injected as raw HTML, carry a CSP nonce,
and coexist with hydration. Roughly 80 lines, of which 20 are subtle.

**Cost:** one dependency, no transitive tree, ships its own `"use client"`.

**Trap found while verifying it, recorded here because it is invisible
otherwise:** the default is `attribute="data-theme"`, **not** `class="dark"` —
and Tailwind 4's `dark:` variant defaults to `@media (prefers-color-scheme:
dark)`, which ignores both. Without an explicit `@custom-variant`, a theme
toggle silently does nothing while `prefers-color-scheme` keeps winning. The
`next-themes` README's Tailwind section is v3-era and does not apply.

### `lucide-react` (^1.30.0)

**What I would otherwise write:** hand-authored SVG for every icon. Not hard,
but §7's accessibility argument *requires* icons — the fit tiers are legible
under colour-vision deficiency only because status ships as icon + text label,
never hue alone (see ADR-0011). Icons are load-bearing here, not decoration, and
a hand-rolled set is a maintenance surface with no upside. Tree-shakes per icon.

### `vitest` (^4) + `@testing-library/react` (^16) + `jsdom`

**What I would otherwise write:** nothing — and that is the problem. The
frontend currently has no test framework at all, so `make test-web` is lint,
typecheck and build: three checks that cannot tell you a component renders the
wrong thing. `CLAUDE.md` requires tests alongside the code they constrain, and
M7's SSE reducer is a pure function whose correctness is not observable any
other way. RTL 16 is the first line declaring React 19 support.

## Declined

### `@tanstack/react-query` — declined

Recommended during research for server state (hydration, document list, message
history). It is a good library and it is not needed here: there are four
endpoints, one of which is a stream that Query does not model well anyway. What
I would otherwise write is a `useWorkspace` hook of about sixty lines around
`fetch`. Sixty lines I understand beats a caching layer whose invalidation
semantics I would then have to reason about at the same time as the citation
offsets. **Revisit if** the endpoint count doubles or optimistic updates arrive.

### `zustand` — declined *for M6*, expected in M7

§7 names it, and it is the right answer for the streaming state: a delta lands
roughly every 12 ms, and a Context-based store re-renders every consumer on
every value change — the document rail and the trace drawer would re-render per
token. But **M6 has no streaming state.** The document rail's state is local to
the rail. Adding a store now would be adding scope because it will be needed
later, which is the failure mode §13 exists to prevent. It lands in M7 with the
component that needs it, and the reason above is the ADR it lands with.

### A component library (shadcn/ui, Radix, MUI) — declined

The shell needs a dialog, a dropzone and a list. The dialog is the only piece
with real accessibility depth (focus trap, `aria-modal`, Escape, restore focus),
and the native `<dialog>` element with `showModal()` provides all four in the
browser. Vendoring shadcn also collides with the type scale: its components ship
`text-sm`/`text-xs`, which would have to coexist with the plan's closed
12/13/14/16/20/26/34 scale or force a sweep.

## Consequences

Four new runtime/dev dependencies against three existing ones. Every one is
justified by code I would otherwise own, and two of the most plausible additions
are declined in writing so that adding them later is a decision rather than a
drift.
