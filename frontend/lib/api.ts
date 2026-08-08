/**
 * The one place this app talks to Django.
 *
 * Two things it exists to get right.
 *
 * **`credentials: "include"` on every request.** The session is an httpOnly
 * cookie set by a different origin (:8000 vs :3000). Omit this on one call and
 * that call silently arrives anonymous — 401, with nothing in the console to
 * suggest a missing option. Centralising it means it cannot be forgotten once.
 *
 * **Errors arrive as data, not exceptions to stringify.** The backend writes
 * `{error_code, message, hint}` deliberately, so that error copy is authored
 * once, server-side, instead of in a `catch` block. `ApiError` carries that
 * envelope through intact and the UI renders `message` and `hint` verbatim.
 */

import type { ApiErrorBody, Document, Workspace } from "./types";

const BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";

export class ApiError extends Error {
  readonly code: string;
  readonly hint?: string;
  readonly status: number;
  readonly retryAfter?: number;

  constructor(status: number, body: ApiErrorBody) {
    super(body.message);
    this.name = "ApiError";
    this.status = status;
    this.code = body.error_code;
    this.hint = body.hint;
    this.retryAfter = body.retry_after;
  }
}

/**
 * The network itself failing, which has no status code and no envelope.
 *
 * Worth its own type because it is the only failure where nothing reached the
 * server: "nothing has been lost" is true here and false for every ApiError,
 * and that distinction is the whole difference in what the UI should say.
 */
export class OfflineError extends Error {
  constructor(cause: unknown) {
    super("Could not reach the API.");
    this.name = "OfflineError";
    this.cause = cause;
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${BASE}${path}`, {
      ...init,
      credentials: "include",
      headers: {
        Accept: "application/json",
        ...init.headers,
      },
    });
  } catch (cause) {
    // A cross-origin network failure is opaque by design — the browser reports
    // TypeError("Failed to fetch") whether the server is down, DNS failed, or
    // CORS rejected the response. Distinguishing them client-side is not
    // possible, so the UI says the one true thing: nothing was sent.
    throw new OfflineError(cause);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  const body = await response.json().catch(() => null);

  if (!response.ok) {
    throw new ApiError(
      response.status,
      (body as ApiErrorBody | null) ?? {
        error_code: "unknown_error",
        message: "Something went wrong.",
        hint: "Try again in a moment.",
      },
    );
  }

  return body as T;
}

function json(method: string, payload?: unknown): RequestInit {
  return {
    method,
    ...(payload === undefined
      ? {}
      : {
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        }),
  };
}

export const api = {
  /**
   * Load the workspace, creating one if there isn't a live session.
   *
   * `GET` first rather than `POST` first: a returning visitor is the common
   * case and costs one round trip, while a first visit costs two. Reversing it
   * would make every load pay for the rare case.
   *
   * `POST /sessions/` is idempotent server-side — it returns the existing
   * session when the cookie resolves — which is what makes this safe under
   * React Strict Mode's double effect in development.
   */
  async bootstrap(): Promise<Workspace> {
    try {
      return await request<Workspace>("/sessions/current/");
    } catch (error) {
      if (error instanceof ApiError && error.code === "session_required") {
        await request("/sessions/", json("POST"));
        return request<Workspace>("/sessions/current/");
      }
      throw error;
    }
  },

  workspace(): Promise<Workspace> {
    return request<Workspace>("/sessions/current/");
  },

  seedDemo(): Promise<{ document_ids: string[]; documents: Document[] }> {
    return request("/sessions/demo/", json("POST"));
  },

  /**
   * Deletes everything, then immediately mints a replacement workspace.
   *
   * The re-mint is not optional. `DELETE` clears the cookie, so the very next
   * request is a 401 — a successful deletion would drop the user into the
   * "session expired" state, which reads as a failure of the thing that just
   * worked.
   */
  async deleteEverything(): Promise<Workspace> {
    await request<void>("/sessions/current/", json("DELETE"));
    await request("/sessions/", json("POST"));
    return request<Workspace>("/sessions/current/");
  },

  upload(file: File, kind: string, label = ""): Promise<Document> {
    const form = new FormData();
    form.append("file", file);
    form.append("kind", kind);
    if (label) form.append("label", label);
    // No Content-Type header: the browser must set it, because only the browser
    // knows the multipart boundary it generated.
    return request<Document>("/documents/", { method: "POST", body: form });
  },

  paste(text: string, kind: string, label = ""): Promise<Document> {
    return request<Document>("/documents/paste/", json("POST", { text, kind, label }));
  },

  remove(id: string): Promise<void> {
    return request<void>(`/documents/${id}/`, json("DELETE"));
  },
};
