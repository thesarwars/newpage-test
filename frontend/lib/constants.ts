/**
 * Limits that exist on both sides of the wire.
 *
 * Duplicated from the backend on purpose, and the duplication is the point of
 * this file: keeping them in one named module means a drift between client and
 * server is a one-line diff here rather than a mystery rejection in the UI.
 * The server's copy remains authoritative — these only decide what to refuse
 * before spending a network round trip on it.
 */

/** apps/documents/validators.py: MAX_FILE_BYTES */
export const MAX_FILE_BYTES = 10 * 1024 * 1024;

/** apps/documents/validators.py: ALLOWED_EXTENSIONS */
export const ALLOWED_EXTENSIONS = new Set(["pdf", "docx", "txt", "md"]);

/** apps/documents/validators.py: MAX_PAGES — shown as guidance, checked server-side. */
export const MAX_PAGES = 30;

/** apps/documents/validators.py: MIN_PASTED_CHARS / MAX_PASTED_CHARS */
export const MIN_PASTED_CHARS = 50;
export const MAX_PASTED_CHARS = 120_000;

/** apps/documents/ingest.py: the session quota. */
export const MAX_JOBS = 10;

/**
 * How long to wait before admitting an upload is slow.
 *
 * Ingest is synchronous and not observable: parse, chunk and embed happen
 * inside one request, so there is no progress to report between "sent" and
 * "done". Measured on the demo corpus, a two-page PDF takes about a second; a
 * long posting can take considerably longer. After this the UI stops implying
 * imminence and says so.
 */
export const SLOW_UPLOAD_MS = 10_000;
