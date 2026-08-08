/**
 * A Markdown renderer that knows where every character came from.
 *
 * This exists because of one hard constraint. Each citation carries
 * `answer_char`: an offset into the **raw** answer text. To render a clickable
 * `[n]` at that position, something has to map raw offsets onto rendered output
 * — and the obvious approaches don't survive contact:
 *
 * - *Splice a sentinel into the source, then render.* The sentinel can land
 *   between the two asterisks of `**bold**`, breaking the emphasis it was
 *   inserted into.
 * - *Render, then walk the DOM inserting marks.* The rendered text no longer
 *   contains the markdown syntax, so raw offsets no longer address it. Every
 *   `**` shifts everything after it by four.
 *
 * So the parser carries source offsets through to the leaf nodes, and marks are
 * placed while emitting text — the only point where both the raw offset and the
 * rendered position are known at once.
 *
 * It is hand-written rather than `react-markdown` + `rehype-sanitize` for two
 * reasons. The offset tracking above is not something either library offers, so
 * the integration work is comparable to writing this. And docs/PLAN.md §8.7
 * requires HTML disabled in rendered markdown — here that is not a plugin to
 * configure correctly, it is a thing the code cannot do, because there is no
 * code path that turns a string into markup.
 *
 * The supported subset is what `apps/chat/prompts/system.md` actually asks for:
 * paragraphs, unordered and ordered lists, `**bold**`, `*italic*` and
 * `` `code` ``. Anything else renders as its own literal text.
 */

export interface Span {
  text: string;
  bold?: boolean;
  italic?: boolean;
  code?: boolean;
  /** Offset of this span's first character in the raw source. */
  start: number;
  /**
   * This span's text is not a copy of the source at `start`.
   *
   * True for exactly one thing: the space a soft line break renders as, which
   * stands in for a newline. Its offset is still the newline's own, so a
   * citation landing there attaches correctly — but `source.slice(start, …)`
   * will not equal `text`, and the invariant test needs to know that is
   * intended rather than a drift.
   */
  synthetic?: boolean;
}

export type Block =
  | { type: "paragraph"; spans: Span[] }
  | { type: "list"; ordered: boolean; items: Span[][] }
  | { type: "code"; text: string; start: number };

const BULLET = /^[ \t]*[-*+][ \t]+/;
const ORDERED = /^[ \t]*\d+[.)][ \t]+/;

/**
 * Parse markdown into blocks whose spans carry source offsets.
 *
 * Offsets are threaded through by construction rather than recovered by
 * searching for the text afterwards: a search would find the wrong occurrence
 * the moment a word repeats, which in an answer about job requirements it
 * always does.
 */
export function parse(source: string): Block[] {
  const blocks: Block[] = [];
  let offset = 0;
  let paragraph: { line: string; start: number }[] = [];
  let list: { items: Span[][]; ordered: boolean } | null = null;

  const flushParagraph = () => {
    if (paragraph.length === 0) return;
    const spans = paragraph.flatMap((line, index) => {
      const parsed = inline(line.line, line.start);
      // A soft line break inside a paragraph renders as a space, and that space
      // has to occupy the newline's own offset or every subsequent citation in
      // the paragraph shifts by one.
      return index < paragraph.length - 1
        ? [
            ...parsed,
            { text: " ", start: line.start + line.line.length, synthetic: true },
          ]
        : parsed;
    });
    blocks.push({ type: "paragraph", spans });
    paragraph = [];
  };

  const flushList = () => {
    if (!list) return;
    blocks.push({ type: "list", ordered: list.ordered, items: list.items });
    list = null;
  };

  for (const line of source.split("\n")) {
    const lineStart = offset;
    offset += line.length + 1; // +1 for the newline that split() removed

    if (line.trim() === "") {
      flushParagraph();
      flushList();
      continue;
    }

    const bullet = BULLET.exec(line);
    const ordered = ORDERED.exec(line);
    const marker = bullet ?? ordered;

    if (marker) {
      flushParagraph();
      const isOrdered = Boolean(ordered);
      if (!list || list.ordered !== isOrdered) {
        flushList();
        list = { items: [], ordered: isOrdered };
      }
      // The offset starts after the marker: the "- " is presentation and the
      // model never cites it, but its length still has to be accounted for.
      list.items.push(inline(line.slice(marker[0].length), lineStart + marker[0].length));
      continue;
    }

    flushList();
    paragraph.push({ line, start: lineStart });
  }

  flushParagraph();
  flushList();
  return blocks;
}

const TOKEN = /(\*\*|__|\*|_|`)/;

/**
 * Split a line into styled spans, carrying offsets.
 *
 * Deliberately forgiving: an unmatched `**` renders as literal asterisks rather
 * than swallowing the rest of the line. A half-finished emphasis marker is the
 * *normal* state of a streaming answer — it is on screen for as long as it takes
 * the next token to arrive — so treating it as a parse error would make the text
 * flicker between two renderings on every delta.
 */
export function inline(line: string, start: number): Span[] {
  const spans: Span[] = [];
  let index = 0;
  let bold = false;
  let italic = false;

  const push = (text: string, at: number, code = false) => {
    if (!text) return;
    spans.push({ text, start: at, ...(bold && { bold }), ...(italic && { italic }), ...(code && { code }) });
  };

  while (index < line.length) {
    const rest = line.slice(index);
    const match = TOKEN.exec(rest);

    if (!match || match.index === undefined) {
      push(rest, start + index);
      break;
    }

    push(rest.slice(0, match.index), start + index);
    const tokenAt = index + match.index;
    const token = match[1];

    if (token === "`") {
      const close = line.indexOf("`", tokenAt + 1);
      if (close === -1) {
        push("`", start + tokenAt);
        index = tokenAt + 1;
        continue;
      }
      push(line.slice(tokenAt + 1, close), start + tokenAt + 1, true);
      index = close + 1;
      continue;
    }

    if (token === "**" || token === "__") bold = !bold;
    else italic = !italic;
    index = tokenAt + token.length;
  }

  return spans;
}

/** A resolved mark: which span it belongs to, and where inside that span. */
export interface Placement {
  span: number;
  /** Offset within the span's own text. */
  at: number;
  index: number;
}

/**
 * Resolve every citation offset onto the flattened span list.
 *
 * The subtle part is **snap-forward**. Markdown delimiters are consumed by the
 * parser and appear in no span, so an offset landing on one belongs nowhere: a
 * citation at offset 1 (between the asterisks of `**bold**`), at a list marker,
 * or at offset 0 of the answer resolves to no span at all and the mark is
 * silently dropped — a citation the model supplied that the reader never sees.
 *
 * So an offset that lands in no span moves forward to the start of the next
 * span in source order, and an offset past the last span moves to its end. That
 * is also the correct reading rather than merely a salvage: a citation sitting
 * on the `**` that introduces a word belongs on the word, and one at the end of
 * the answer belongs at the end of the rendered answer.
 */
export function place(spans: Span[], marks: { index: number; at: number }[]): Placement[] {
  const placements: Placement[] = [];

  for (const mark of marks) {
    let resolved: Placement | null = null;

    for (let i = 0; i < spans.length; i += 1) {
      const span = spans[i];
      const end = span.start + span.text.length;

      if (mark.at > span.start && mark.at <= end) {
        resolved = { span: i, at: mark.at - span.start, index: mark.index };
        break;
      }
      // Snap forward: the offset sits before this span and inside no earlier
      // one, so it fell on syntax the parser consumed.
      if (mark.at <= span.start) {
        resolved = { span: i, at: 0, index: mark.index };
        break;
      }
    }

    if (!resolved && spans.length > 0) {
      const last = spans.length - 1;
      resolved = { span: last, at: spans[last].text.length, index: mark.index };
    }
    if (resolved) placements.push(resolved);
  }

  // Ascending by position, then by index. Two citations at one offset both
  // render, adjacent and in order — dropping either loses evidence the model
  // actually supplied.
  return placements.sort((a, b) => a.span - b.span || a.at - b.at || a.index - b.index);
}

/** Every span in the document, in source order. */
export function flatten(blocks: Block[]): Span[] {
  return blocks.flatMap((block) =>
    block.type === "paragraph" ? block.spans : block.type === "list" ? block.items.flat() : [],
  );
}

/**
 * Split a span at every citation offset that falls strictly inside it.
 *
 * Returns pieces in order, each tagged with the citation index that should
 * follow it. Offsets outside the span are ignored, an offset at the span's start
 * attaches to the *previous* span, and two citations at the same offset both
 * emit — dropping one would silently lose evidence.
 */
export function splitAt(
  span: Span,
  offsets: { index: number; at: number }[],
): { text: string; citation: number | null }[] {
  const end = span.start + span.text.length;
  const inside = offsets
    .filter((o) => o.at > span.start && o.at <= end)
    .sort((a, b) => a.at - b.at || a.index - b.index);

  if (inside.length === 0) return [{ text: span.text, citation: null }];

  const pieces: { text: string; citation: number | null }[] = [];
  let cursor = span.start;

  for (const offset of inside) {
    const slice = span.text.slice(cursor - span.start, offset.at - span.start);
    // Two citations at the same offset produce a zero-length slice for the
    // second. Emitting it anyway keeps both marks, adjacent, which is what the
    // model meant.
    pieces.push({ text: slice, citation: offset.index });
    cursor = offset.at;
  }

  const tail = span.text.slice(cursor - span.start);
  if (tail) pieces.push({ text: tail, citation: null });

  return pieces;
}
