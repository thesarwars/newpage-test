"use client";

/**
 * Ephemeral UI state that several distant components need.
 *
 * ADR-0009 declined Zustand for M6 and expected it here, and this is the reason:
 * a delta lands roughly every 12ms, and a Context-based store re-renders every
 * consumer on every value change. The document rail, the composer's scope pill
 * and the trace drawer would each re-render per token, for state they do not
 * read. Zustand subscribes per selector, so only the answer body re-renders.
 *
 * Server state deliberately does not live here — documents and messages stay in
 * `useWorkspace`. Two stores for one concept is how they drift; this one holds
 * only things that have no server representation at all.
 */

import { create } from "zustand";

export interface SelectedCitation {
  index: number;
  docId: string;
  charStart: number;
  charEnd: number;
  citedText: string;
  /** The message it came from, so the panel can offer a way back to it. */
  messageId: string | null;
}

interface UiState {
  selected: SelectedCitation | null;
  panelOpen: boolean;
  /** Empty means "every job" — resolved server-side, which is the point. */
  scopeJobIds: string[];
  mode: "analysis" | "interview";

  selectCitation: (citation: SelectedCitation) => void;
  closePanel: () => void;
  setScope: (ids: string[]) => void;
  toggleScope: (id: string) => void;
  setMode: (mode: "analysis" | "interview") => void;
}

export const useUi = create<UiState>((set) => ({
  selected: null,
  panelOpen: false,
  scopeJobIds: [],
  mode: "analysis",

  selectCitation: (citation) => set({ selected: citation, panelOpen: true }),
  closePanel: () => set({ panelOpen: false }),
  setScope: (scopeJobIds) => set({ scopeJobIds }),
  toggleScope: (id) =>
    set((state) => ({
      scopeJobIds: state.scopeJobIds.includes(id)
        ? state.scopeJobIds.filter((existing) => existing !== id)
        : [...state.scopeJobIds, id],
    })),
  setMode: (mode) => set({ mode }),
}));
