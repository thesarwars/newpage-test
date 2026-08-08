"use client";

/**
 * Renders an answer, with citation marks spliced at raw-source offsets.
 *
 * The offset arithmetic lives in `lib/markdown.ts`; this is only the React
 * half. Note what is absent: there is no `dangerouslySetInnerHTML`, no
 * sanitizer, and no HTML parser. docs/PLAN.md §8.7 requires HTML disabled in
 * rendered markdown, and here that is not a setting that could be wrong — the
 * renderer has no code path that turns a string into markup.
 *
 * That matters more than it looks, because the assistant's answer is not
 * entirely the assistant's: the keyless stub interpolates the user's own
 * question into an italic span, so a user can put markdown syntax inside an
 * "assistant" message. Against this renderer the worst case is a stray asterisk.
 */

import { Fragment } from "react";

import { type Span, flatten, parse, place, splitAt } from "@/lib/markdown";
import { toUtf16 } from "@/lib/offsets";
import type { Citation } from "@/lib/types";

interface Props {
  source: string;
  citations: Citation[];
  onCite?: (citation: Citation) => void;
  activeIndex?: number | null;
}

export function Markdown({ source, citations, onCite, activeIndex }: Props) {
  const blocks = parse(source);
  const spans = flatten(blocks);
  const placements = place(
    spans,
    // `answer_char` is a Python code-point index; this parser works in UTF-16.
    // One emoji in the user's question is enough to make them disagree, and the
    // resulting mark lands on a different passage with no error anywhere.
    citations.map((c) => ({ index: c.index, at: toUtf16(source, c.answer_char) })),
  );

  // Placements address spans by their index in the flat list, but rendering
  // walks the block tree. `flatten` returns the same span *objects*, so identity
  // maps one onto the other — a positional counter threaded through the walk
  // works too, and is one refactor away from being silently off by one.
  const indexOf = new Map<Span, number>(spans.map((span, i) => [span, i]));

  const renderSpan = (span: Span, key: number) => {
    const here = placements.filter((p) => p.span === indexOf.get(span));
    const pieces = splitAt(
      { ...span, start: 0 },
      here.map((p) => ({ index: p.index, at: p.at })),
    );

    return (
      <Fragment key={key}>
        {pieces.map((piece, i) => (
          <Fragment key={i}>
            {styled(span, piece.text)}
            {piece.citation !== null ? (
              <CiteMark
                index={piece.citation}
                citation={citations.find((c) => c.index === piece.citation)}
                onCite={onCite}
                active={activeIndex === piece.citation}
              />
            ) : null}
          </Fragment>
        ))}
      </Fragment>
    );
  };

  const renderSpans = (list: Span[]) => list.map((span, i) => renderSpan(span, i));

  return (
    <div className="space-y-3 text-body">
      {blocks.map((block, index) => {
        if (block.type === "paragraph") {
          return <p key={index}>{renderSpans(block.spans)}</p>;
        }
        if (block.type === "list") {
          const List = block.ordered ? "ol" : "ul";
          return (
            <List
              key={index}
              className={
                block.ordered
                  ? "list-decimal space-y-1.5 pl-5 marker:text-ink-2"
                  : "list-disc space-y-1.5 pl-5 marker:text-ink-2"
              }
            >
              {block.items.map((item, i) => (
                <li key={i}>{renderSpans(item)}</li>
              ))}
            </List>
          );
        }
        return null;
      })}
    </div>
  );
}

function styled(span: Span, text: string) {
  if (!text) return null;
  if (span.code) {
    return (
      <code className="rounded bg-muted px-1 py-0.5 font-mono text-micro">{text}</code>
    );
  }
  if (span.bold && span.italic) return <strong><em>{text}</em></strong>;
  if (span.bold) return <strong className="font-semibold">{text}</strong>;
  if (span.italic) return <em>{text}</em>;
  return text;
}

function CiteMark({
  index,
  citation,
  onCite,
  active,
}: {
  index: number;
  citation?: Citation;
  onCite?: (citation: Citation) => void;
  active: boolean;
}) {
  if (!citation) return null;

  return (
    <button
      type="button"
      data-citation={index}
      onClick={() => onCite?.(citation)}
      // The accessible name carries the evidence, not just the number. "1" tells
      // a screen-reader user nothing about what they are about to open.
      aria-label={`Citation ${index}: ${citation.cited_text.slice(0, 80)}`}
      className={`mx-0.5 inline-flex min-w-4 translate-y-[-1px] items-center justify-center rounded-[4px] px-1 align-middle font-mono text-[10px] leading-4 transition-colors duration-(--duration-hover) ${
        active ? "bg-accent-fill text-white" : "bg-mark text-ink hover:bg-accent-fill hover:text-white"
      }`}
    >
      {index}
    </button>
  );
}
