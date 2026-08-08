# ADR-0011: Theme the fit-tier palette, against the plan's own instruction

**Status:** accepted · **Date:** 2026-08-08 · **Milestone:** M6
**Supersedes:** the "reserved status palette, **never the accent, never
themed**" rule in `docs/PLAN.md` §7.

## Context

§7 fixes four hexes for the fit tiers and states they are never themed. It also
acknowledges one problem and proposes a mitigation:

> Amber and Weak sit under 3:1 on the light surface **by design** — mitigated
> because every instance ships icon + text label, so status never rides on hue
> alone.

Both the numbers and the mitigation were checked before writing any component
code. Neither survived.

## What the maths says

Computed contrast (WCAG 2.1 relative luminance) against the light page
`#fcfcfb` and the raised dark card `#232321` — the worst-case surface in each
mode, which is the page in light and the *card* in dark:

| Tier | Hex | Light | Dark card |
|---|---|---|---|
| Strong | `#0ca30c` | 3.27 | 4.69 |
| Partial | `#fab219` | **1.79** | 8.58 |
| Weak | `#ec835a` | **2.57** | 5.97 |
| Gap | `#d03b3b` | 4.68 | **3.28** |

The plan says amber is "under 3:1". It is under **2:1**. And the plan's caveat
names only light mode, while red is the dark-mode casualty at 3.28 and falls
below 3:1 on any card lighter than `#232321`.

**The stated mitigation is circular.** "Every instance ships icon + text label"
rescues *hue-blindness* (SC 1.4.1) but not *legibility*: an icon is itself a
graphical object under SC 1.4.11 and needs 3:1 in its own right. A
`CircleDashed` stroked in `#fab219` on a white card is 1.83:1 — the icon is
exactly as invisible as the colour it was supposed to rescue. The mitigation
only works if the icon and the label are drawn in ink and the hue is confined to
fills, which is a constraint on component code that the plan never states.

## Why "never themed" is the actual cause

Three un-themed palettes were computed and measured. Each fixed one axis and
broke the other:

| Palette | Worst contrast | Closest CVD pair | Grayscale ΔY |
|---|---|---|---|
| Plan's spec hexes | **1.79:1** | 44.7 | 21.7 |
| Luminance-matched | 3.90:1 | **5.1** (identical) | **0.0** |
| Spread inside the shared window | 3.10:1 | **3.3** (identical) | 10.0 |

The pattern is the finding. An un-themed colour must clear 3:1 against a
near-white page *and* a near-black card, which confines it to relative luminance
**Y ∈ [0.150, 0.291]** — a 1.9× range. Four hues compressed into a 1.9× luminance
range cannot also be far apart in luminance, and luminance is the one channel a
colour-blind or grayscale reader still has. Legibility and colour-blind
separation are in direct competition, and inside that window neither can win.

Stronger, and worth stating because it settles the question: **no un-themed
colour can ever be AA-normal text on both surfaces.** Clearing 4.5:1 on the light
page requires Y ≤ 0.177; clearing it on the dark card requires Y ≥ 0.250. The
window is empty at every hue.

## Decision

Theme the status palette. Each mode then has the whole luminance range on one
side of its threshold, and both goals are satisfiable at once:

| Tier | Light | vs page | Dark | vs card |
|---|---|---|---|---|
| Strong | `#039e05` | 3.47 | `#008d00` | 3.61 |
| Partial | `#a06f00` | 4.29 | `#c88d00` | 5.45 |
| Weak | `#aa471c` | 5.64 | `#fe956c` | 7.29 |
| Gap | `#a00016` | 8.16 | `#ffb2ab` | 9.15 |

Measured: worst contrast **3.47** light / **3.61** dark (against 1.79 before),
with colour-blind separation *equal or better* than the spec palette in light
(deuteranopia 33.7, protanopia 45.4, tritanopia 44.2) and far better in dark
(51.8 / 23.3 / 56.9, grayscale 31.3). Luminance ordering encodes severity, so
the tiers remain ranked in grayscale and in print.

Two rules travel with the palette, because the maths above only holds if they do:

1. **Status hue is never text.** The tier label and the hero score are ink. Hue
   lives in the meter fill, the icon and the matrix cell.
2. **Icons are filled shapes, not hairline strokes**, so the 3:1 they clear is
   the contrast actually perceived.

## Consequences

- §7's "never themed" is wrong and is corrected in the same commit, with this
  ADR as the reason — per `CLAUDE.md`, the plan is never silently diverged from.
- Eight hexes to maintain instead of four.
- Amber becomes a darker gold in light mode. That is the price of being legible
  on white; a yellow that reads as yellow on `#fcfcfb` cannot also be seen on it.
- **Unverified:** this is WCAG 2.1 relative-luminance maths, computed and
  reproducible, not a browser measurement. APCA would rate these colours
  differently. §11's axe-core gate on the loaded workspace is the real check and
  has not run yet.
