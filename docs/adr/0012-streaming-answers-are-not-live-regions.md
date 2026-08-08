# ADR-0012: A streaming answer is not a live region

**Status:** accepted · **Date:** 2026-08-08 · **Milestone:** M7
**Supersedes:** `aria-live="polite"` on the streaming answer, `docs/PLAN.md` §7.

## Context

§7 specifies the streaming answer as `aria-live="polite"` with citation marks
splicing in at their recorded `answer_char`. That is the obvious reading of
"announce new text as it arrives", and it is wrong in two independent ways —
both measured against the running app rather than reasoned about.

## What the measurements say

**One answer produces roughly two thousand announcements.** The keyless stub
emits a delta about every 12 ms; a single 13k-character answer was measured at
2,146 deltas. `aria-live="polite"` with the default `aria-atomic="false"`
announces the *diff*, not the whole region ([MDN, ARIA live
regions](https://developer.mozilla.org/en-US/docs/Web/Accessibility/ARIA/Guides/Live_regions)),
so that is ~2,146 queued utterances of roughly one word each. The region is
never silent long enough for a screen-reader user to do anything else.

`aria-atomic="true"` is worse, not better: it re-announces the entire content on
every update, so delta 2,146 would re-read all 13k characters.

**Citations splice behind the write head.** Measured on a live stream, each
citation mark is inserted **51–82 characters behind** text already in the DOM.
That rules out `role="log"` for the streaming answer specifically, because the
role's contract is that "new information is added only to the end of the log,
**not at arbitrary points**" (MDN). Marks are, by construction, arbitrary points.

**`aria-busy` is not a way out.** It is the tidy answer — mark the region busy
while streaming, announce once at the end — and support does not exist:
[a11ysupport.io](https://a11ysupport.io/tech/aria/aria-busy_attribute) records
`aria-busy="true"` as supported by JAWS and effectively ignored by NVDA and
VoiceOver, which would get the flood anyway.

## Decision

Three regions, three jobs.

1. **The completed transcript is `role="log"`** with a redundant
   `aria-live="polite"`. Its contract genuinely holds here: finished messages are
   only ever appended. `role="log"` also has the best support of the options
   ([a11ysupport.io: 62/66](https://a11ysupport.io/tech/aria/log_role)),
   including VoiceOver on macOS and iOS.

2. **The streaming answer is `aria-live="off"`.** It is visible, it animates, and
   sighted users get the whole perceived-latency design §7 asks for. It is simply
   not a live region while it streams.

3. **A visually-hidden `role="status"` carries phase, not prose** — "Working out
   which documents to read", "Reading the passages", "Answer complete". Five
   utterances per turn instead of two thousand, and they are the part that is
   actually useful to hear.

Citation marks are `<button>`s whose accessible name carries the evidence
(`Citation 1: 5+ years running Kubernetes…`), not the bare number, because "1" is
not something a person can act on.

## Consequences

- A screen-reader user does not hear the answer as it arrives. They hear that an
  answer is being produced, and read it when it is there. Given the alternative
  is an uninterruptible two-thousand-item queue, that is the better experience,
  not merely the cheaper one.
- §7 is corrected in the same commit, per `CLAUDE.md`.
- **Unverified:** none of this was tested with an actual screen reader. The
  measurements (delta counts, splice distances) are from the running app; the
  behavioural claims are from vendor support data and specification text. §11's
  axe-core gate does not cover announcement behaviour and would pass either way.
