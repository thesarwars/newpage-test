"use client";

/**
 * The passages retrieval selected, shown before any answer text exists.
 *
 * This is the single most load-bearing bit of sequencing in the product: the
 * chips arrive at ~50ms, the first token at ~50ms too but the text takes
 * seconds to accumulate. Seeing *what was read* before reading the answer is
 * what makes the answer legible as derived rather than generated.
 *
 * The 40ms stagger is not decoration either — it makes the arrival readable as
 * a sequence rather than a flash, which is the thing being communicated. It
 * collapses to nothing under `prefers-reduced-motion`, handled globally.
 */

import type { SourceChunk } from "@/lib/chat";

export function SourceChips({ chunks }: { chunks: SourceChunk[] }) {
  return (
    <ul className="flex flex-wrap gap-1.5" aria-label={`${chunks.length} passages retrieved`}>
      {chunks.map((chunk, index) => (
        <li
          key={chunk.id}
          className="animate-[fade-in_220ms_ease-out_both] rounded-pill border border-hairline bg-card px-2 py-0.5 text-micro text-ink-2"
          style={{ animationDelay: `${index * 40}ms` }}
          title={chunk.preview}
        >
          <span className="text-ink">{chunk.doc_label}</span>
          {chunk.section ? ` · ${chunk.section}` : ""}
        </li>
      ))}
    </ul>
  );
}
