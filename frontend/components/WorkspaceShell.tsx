"use client";

/**
 * The three-column workspace: documents · work · evidence.
 *
 * `280px | 1fr | 380px`, with the centre column at `min-width: 0`. That last
 * part is not cosmetic — a CSS grid track defaults to `min-content`, so a wide
 * child (a code block, a long chunk preview, a trace table) pushes the whole
 * grid wider and the *page* scrolls sideways instead of the child. Every
 * horizontal overflow in this app is meant to be contained by the thing
 * overflowing.
 *
 * The right column is a seam for M7's evidence panel. It renders as a column
 * now so the layout it lands into is the layout that shipped, rather than a
 * grid change arriving at the same time as the citation offsets.
 */

import type { ReactNode } from "react";

interface Props {
  rail: ReactNode;
  children: ReactNode;
  banner?: ReactNode;
  evidence?: ReactNode;
}

export function WorkspaceShell({ rail, children, banner, evidence }: Props) {
  return (
    <div className="flex h-dvh flex-col">
      {/* First focusable thing on the page. A three-pane app puts a long rail
          between the top of the document and the main content, and that is a
          lot of Tab presses for someone who does not use a mouse. */}
      <a
        href="#work"
        className="sr-only rounded-control bg-accent-fill px-4 py-2 text-ink-inv focus:not-sr-only focus:absolute focus:left-3 focus:top-3 focus:z-50"
      >
        Skip to content
      </a>

      {banner}

      <div className="grid min-h-0 flex-1 grid-cols-1 lg:grid-cols-[280px_minmax(0,1fr)] xl:grid-cols-[280px_minmax(0,1fr)_380px]">
        {/* One rail, repositioned — not two copies behind breakpoint classes.
            Two copies means two of every dialog, two file inputs, and two of
            each control's accessible name in the document, which is a real
            problem for anything walking the DOM rather than looking at it.
            Below `lg` the grid is a single column and the rail simply sits
            after the main content. */}
        <aside
          aria-label="Documents"
          className="order-2 min-h-0 border-t border-hairline lg:order-1 lg:border-r lg:border-t-0"
        >
          {rail}
        </aside>

        {/* min-w-0 again on the child: the track allows shrinking, the child
            must also be told it may. */}
        <main id="work" className="order-1 min-h-0 min-w-0 overflow-y-auto lg:order-2">
          {children}
        </main>

        {evidence ? (
          <aside
            aria-label="Evidence"
            className="order-3 hidden min-h-0 border-l border-hairline xl:block"
          >
            {evidence}
          </aside>
        ) : null}
      </div>

    </div>
  );
}
