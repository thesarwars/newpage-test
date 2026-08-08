import { describe, expect, it } from "vitest";

import { inline, parse, splitAt } from "@/lib/markdown";

/** Every span's text must sit at its claimed offset in the source. */
function offsetsAreTruthful(source: string, blocks: ReturnType<typeof parse>): void {
  const spans = blocks.flatMap((block) =>
    block.type === "list" ? block.items.flat() : block.type === "paragraph" ? block.spans : [],
  );
  for (const span of spans) {
    // Two spans deliberately do not equal their source slice: a code span (the
    // backticks are stripped) and the synthetic space a soft line break renders
    // as. Both still carry a truthful offset, which is what citations need.
    if (span.code || span.synthetic) continue;
    expect(source.slice(span.start, span.start + span.text.length)).toBe(span.text);
  }
}

describe("offset fidelity", () => {
  // This is the property the whole citation feature rests on. If a span claims
  // the wrong offset, the [n] mark lands in the wrong sentence — and it looks
  // deliberate, because there is nothing visibly broken.
  it.each([
    "Plain prose with no markup at all.",
    "You're short on **production Kubernetes**, which the posting calls a must-have.",
    "- Kubernetes\n- Terraform\n- Observability tooling",
    "1. **Résumé** — “Reduced p99 latency”\n2. **Job #2** — “5+ years”",
    "Some prose.\n\n- then a list\n- with two items\n\nAnd a closing line.",
    "A paragraph that wraps\nacross two source lines.",
    "Mixed `code` and **bold** and *italic* together.",
  ])("holds for %#", (source) => {
    offsetsAreTruthful(source, parse(source));
  });
});

describe("structure", () => {
  it("separates paragraphs on a blank line", () => {
    const blocks = parse("First.\n\nSecond.");

    expect(blocks).toHaveLength(2);
    expect(blocks.every((b) => b.type === "paragraph")).toBe(true);
  });

  it("groups consecutive bullets into one list", () => {
    const blocks = parse("- one\n- two\n- three");

    expect(blocks).toHaveLength(1);
    expect(blocks[0].type === "list" && blocks[0].items).toHaveLength(3);
  });

  it("distinguishes ordered from unordered", () => {
    const [unordered] = parse("- a");
    const [ordered] = parse("1. a");

    expect(unordered.type === "list" && unordered.ordered).toBe(false);
    expect(ordered.type === "list" && ordered.ordered).toBe(true);
  });

  it("starts a new list when the marker style changes", () => {
    const blocks = parse("- bullet\n1. numbered");

    expect(blocks).toHaveLength(2);
  });

  it("excludes the list marker from the first span's offset", () => {
    const source = "- Kubernetes";
    const [block] = parse(source);

    const first = block.type === "list" ? block.items[0][0] : null;
    expect(first?.start).toBe(2);
    expect(source.slice(first!.start)).toBe("Kubernetes");
  });
});

describe("inline emphasis", () => {
  it("marks bold text without keeping the asterisks", () => {
    const spans = inline("a **b** c", 0);

    expect(spans.find((s) => s.bold)?.text).toBe("b");
    expect(spans.map((s) => s.text).join("")).toBe("a b c");
  });

  it("renders an unclosed marker literally rather than swallowing the line", () => {
    // This is the *normal* state of a streaming answer: "**Demo" is on screen
    // until the closing asterisks arrive. Treating it as a parse error would
    // make the text flicker between two renderings on every delta.
    const spans = inline("**half finished", 0);

    expect(spans.map((s) => s.text).join("")).toContain("half finished");
  });

  it("renders an unclosed backtick literally", () => {
    const spans = inline("use `npm to install", 0);

    expect(spans.map((s) => s.text).join("")).toBe("use `npm to install");
  });

  it("never emits markup, because there is no code path that could", () => {
    // docs/PLAN.md §8.7 requires HTML disabled. Here that is not a plugin to
    // configure, it is a capability the renderer does not have.
    const spans = inline("<script>alert(1)</script>", 0);

    expect(spans).toHaveLength(1);
    expect(spans[0].text).toBe("<script>alert(1)</script>");
    expect(spans[0].bold).toBeUndefined();
  });
});

describe("citation splitting", () => {
  const span = { text: "You are short on Kubernetes.", start: 0 };

  it("splits at an offset inside the span", () => {
    const pieces = splitAt(span, [{ index: 1, at: 17 }]);

    expect(pieces[0]).toEqual({ text: "You are short on ", citation: 1 });
    expect(pieces[1]).toEqual({ text: "Kubernetes.", citation: null });
  });

  it("ignores an offset outside the span", () => {
    expect(splitAt(span, [{ index: 1, at: 900 }])).toEqual([
      { text: span.text, citation: null },
    ]);
  });

  it("attaches an offset at the very start to the previous span, not this one", () => {
    // Otherwise a citation at index 0 of a span emits a zero-length piece
    // before any text, and the mark renders before the sentence it belongs to.
    expect(splitAt(span, [{ index: 1, at: 0 }])).toEqual([
      { text: span.text, citation: null },
    ]);
  });

  it("keeps both marks when two citations share an offset", () => {
    // Dropping one would silently lose evidence the model actually supplied.
    const pieces = splitAt(span, [
      { index: 1, at: 17 },
      { index: 2, at: 17 },
    ]);

    expect(pieces.map((p) => p.citation)).toEqual([1, 2, null]);
    expect(pieces.map((p) => p.text).join("")).toBe(span.text);
  });

  it("handles an offset at the exact end of the span", () => {
    const pieces = splitAt(span, [{ index: 1, at: span.text.length }]);

    expect(pieces).toEqual([{ text: span.text, citation: 1 }]);
  });

  it("never loses or duplicates a character", () => {
    const pieces = splitAt(span, [
      { index: 1, at: 4 },
      { index: 2, at: 17 },
      { index: 3, at: 20 },
    ]);

    expect(pieces.map((p) => p.text).join("")).toBe(span.text);
  });

  it("orders marks by position even when they arrive out of order", () => {
    const pieces = splitAt(span, [
      { index: 2, at: 17 },
      { index: 1, at: 4 },
    ]);

    expect(pieces.map((p) => p.citation)).toEqual([1, 2, null]);
  });
});

describe("the keyless stub's actual output", () => {
  // What a reviewer without an API key sees, so it has to render correctly.
  const STUB =
    "**Demo answer.** No `ANTHROPIC_API_KEY` is configured, so this text is " +
    "assembled from the passages retrieval selected.\n\n" +
    "1. **Résumé** — “Reduced p99 latency from 1.4s to 380ms”\n\n" +
    "2. **Job #2 — REQUIREMENTS** — “Production Kubernetes at scale”\n\n";

  it("parses into the blocks it looks like", () => {
    const blocks = parse(STUB);

    expect(blocks[0].type).toBe("paragraph");
    expect(blocks.some((b) => b.type === "list" && b.ordered)).toBe(true);
  });

  it("keeps every offset truthful", () => {
    offsetsAreTruthful(STUB, parse(STUB));
  });

  it("renders the em-dashes and curly quotes it actually emits", () => {
    const text = parse(STUB)
      .flatMap((b) => (b.type === "list" ? b.items.flat() : b.type === "paragraph" ? b.spans : []))
      .map((s) => s.text)
      .join("");

    expect(text).toContain("—");
    expect(text).toContain("“Reduced p99 latency from 1.4s to 380ms”");
  });
});
