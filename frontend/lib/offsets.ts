/**
 * Python offsets → JavaScript offsets.
 *
 * Every character offset in this system is produced by Python, which indexes
 * strings by **Unicode code point**. JavaScript indexes by **UTF-16 code unit**.
 * The two agree for every character in the Basic Multilingual Plane and disagree
 * by one for every character outside it — emoji, rare CJK, mathematical
 * alphanumerics — because those are stored as a surrogate pair.
 *
 * This is not hypothetical here. The keyless stub interpolates the user's own
 * question into the answer, so a user who types "🚀 am I a fit?" produces an
 * answer whose offsets are measurably wrong. Reproduced against the live API:
 * an 803-code-point answer is 806 UTF-16 units, and all three citations landed
 * 3 units early.
 *
 * The failure has no error surface. A drifted offset does not throw and does not
 * fall outside the text; it lands a few characters off, and combined with the
 * renderer's snap-forward rule it resolves to a *different valid* span. The mark
 * renders, looks fine, and cites the wrong passage.
 *
 * Converting client-side rather than changing the server keeps the server's
 * offsets meaning "index into the Python string" — which is what it verifies
 * `cited_text` against before persisting a citation. Changing that would make
 * the server's own invariant unexpressible in Python.
 */

/**
 * Convert a code-point index into the equivalent UTF-16 index in `text`.
 *
 * `Array.from` iterates by code point, so the length of the first `index` code
 * points, measured in UTF-16 units, is exactly the answer. Costs one pass over
 * the prefix, and is called a handful of times per answer.
 */
export function toUtf16(text: string, codePointIndex: number): number {
  if (codePointIndex <= 0) return 0;

  let unit = 0;
  let seen = 0;

  for (const character of text) {
    if (seen >= codePointIndex) break;
    unit += character.length;
    seen += 1;
  }

  // An index past the end of the string clamps rather than overshooting — the
  // server can legitimately report an offset equal to the length.
  return Math.min(unit, text.length);
}

/** True when `text` is entirely BMP, so the two index spaces coincide. */
export function isBmpOnly(text: string): boolean {
  // A surrogate pair is the only case where a code point spans two units.
  return !/[\uD800-\uDBFF][\uDC00-\uDFFF]/.test(text);
}
