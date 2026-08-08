import { describe, expect, it } from "vitest";

import { isBmpOnly, toUtf16 } from "@/lib/offsets";

describe("code point to UTF-16", () => {
  it("is the identity for text inside the BMP", () => {
    // The overwhelmingly common case, including every character in the demo
    // corpus — which is exactly why this bug survives casual testing.
    const text = "You're short on production Kubernetes — 5+ years.";
    for (let i = 0; i <= text.length; i += 1) {
      expect(toUtf16(text, i)).toBe(i);
    }
  });

  it("shifts by one per astral character", () => {
    // "🚀" is one code point and two UTF-16 units. Python says the "a" is at 1;
    // JavaScript says it is at 2.
    const text = "🚀ab";

    expect(toUtf16(text, 1)).toBe(2);
    expect(toUtf16(text, 2)).toBe(3);
  });

  it("reproduces the live drift", () => {
    // Measured against the running API: a question containing two rockets and a
    // globe produced an 803-code-point answer that is 806 UTF-16 units, and
    // every citation landed three units early.
    const prefix = "🚀🚀 Am I a fit for the platform role? 🌍";
    const text = `${prefix} — and the answer continues here.`;

    expect(Array.from(text).length).toBe(text.length - 3);
    expect(toUtf16(text, Array.from(text).length)).toBe(text.length);
  });

  it("slices the intended characters after conversion", () => {
    const text = "🚀 Kubernetes at scale";
    // Python: text[2:12] == "Kubernetes"
    const start = toUtf16(text, 2);
    const end = toUtf16(text, 12);

    expect(text.slice(start, end)).toBe("Kubernetes");
    // And what the uncorrected code would have highlighted instead.
    expect(text.slice(2, 12)).not.toBe("Kubernetes");
  });

  it("clamps an index past the end rather than overshooting", () => {
    expect(toUtf16("abc", 99)).toBe(3);
  });

  it("handles a zero or negative index", () => {
    expect(toUtf16("🚀abc", 0)).toBe(0);
    expect(toUtf16("🚀abc", -1)).toBe(0);
  });

  it("detects whether the two index spaces coincide", () => {
    expect(isBmpOnly("Kubernetes — Terraform")).toBe(true);
    expect(isBmpOnly("ship it 🚀")).toBe(false);
  });
});
