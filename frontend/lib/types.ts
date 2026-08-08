/**
 * The API contract, mirrored.
 *
 * Every shape here was read off the backend's serializer functions rather than
 * off the plan, because the two disagreed in three places. `chunk_count` was
 * promised and absent (now present); `DocumentStatus` has seven members of
 * which only `ready` and `failed` are ever written; and the plan's `warnings[]`
 * does not exist at all.
 *
 * Hand-written rather than generated. A generator is the right answer at ten
 * endpoints; at seven it is a build step, a schema export and a drift check to
 * maintain in order to avoid writing eighty lines once.
 */

export type DocumentKind = "resume" | "job";

/**
 * Seven values exist on the model; ingest is synchronous and wrapped in one
 * transaction, so a client only ever observes the terminal two. The
 * intermediate states are listed because they are what the column can hold, and
 * narrowing the type to a lie would be worse than a union nothing produces.
 */
export type DocumentStatus =
  | "queued"
  | "parsing"
  | "chunking"
  | "embedding"
  | "analyzing"
  | "ready"
  | "failed";

export interface Section {
  id: string;
  heading: string;
  kind: string;
  char_start: number;
  char_end: number;
  is_boilerplate: boolean;
}

export interface Document {
  id: string;
  kind: DocumentKind;
  /** The "#2" in "how do I match Job #2?". 0 for the résumé, 1-based for jobs. */
  ordinal: number;
  label: string;
  company: string;
  original_filename: string;
  page_count: number;
  size_bytes: number;
  chunk_count: number;
  status: DocumentStatus;
  error_code: string;
  /** A posting containing text aimed at automated screening. Surfaced, not hidden. */
  injection_flag: boolean;
  injection_reasons: string[];
  sections: Section[];
  created_at: string;
  /** Only on the detail endpoint. The evidence panel's data source (M7). */
  normalized_text?: string;
}

export interface Usage {
  tokens_used: number;
  cost_usd: string;
  budget_remaining_usd: string;
}

export interface Citation {
  index: number;
  answer_char: number;
  chunk_id: string | null;
  doc_id: string;
  char_start: number;
  char_end: number;
  cited_text: string;
}

export interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  mode: "analysis" | "interview";
  intent: string;
  scope_job_ids: string[];
  status: "streaming" | "complete" | "refused" | "no_context" | "error";
  refusal_reason: string;
  grounding: { max_score: number; citations: number; low_evidence: boolean };
  created_at: string;
  citations: Citation[];
}

/** `GET /sessions/current/` — the whole workspace in one round trip. */
export interface Workspace {
  id: string;
  expires_at: string;
  usage: Usage;
  demo_seeded: boolean;
  can_seed_demo: boolean;
  /** No API key configured: generation is stubbed. Reported, never required. */
  demo_mode: boolean;
  documents: Document[];
  messages: Message[];
}

/** A suggestion chip. `label` is what the chip says; `message` is what it asks. */
export interface Suggestion {
  label: string;
  message: string;
  intent: string;
}

/**
 * Every failure the API returns has this shape.
 *
 * `message` and `hint` are written server-side and rendered verbatim — the
 * client does not compose error prose. `error_code` is the stable half the UI
 * switches on, and it is deliberately NOT used as a copy key: `unsupported_type`
 * carries two different messages and `parse_failed` carries four, so keying
 * copy off the code would produce confidently wrong sentences.
 */
export interface ApiErrorBody {
  error_code: string;
  message: string;
  hint?: string;
  retry_after?: number;
}
