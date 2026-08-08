import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, OfflineError, api } from "@/lib/api";

function respond(status: number, body: unknown, ok = status < 400) {
  return Promise.resolve({
    ok,
    status,
    json: () => Promise.resolve(body),
  } as Response);
}

afterEach(() => vi.unstubAllGlobals());

describe("credentials", () => {
  it("sends the session cookie on every request", async () => {
    // The session is an httpOnly cookie from another origin. Omit this on one
    // call and that call silently arrives anonymous — a 401 with nothing in the
    // console to suggest a missing option.
    const seen: RequestInit[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn((_url: string, init: RequestInit) => {
        seen.push(init);
        return respond(200, { id: "s1", documents: [] });
      }),
    );

    await api.workspace();

    expect(seen[0]).toMatchObject({ credentials: "include" });
  });
});

describe("errors", () => {
  it("carries the server's envelope through instead of stringifying it", async () => {
    vi.stubGlobal("fetch", () =>
      respond(
        422,
        {
          error_code: "no_text_layer",
          message: "No selectable text was found — this looks like a scan.",
          hint: "Paste the text instead.",
        },
        false,
      ),
    );

    // The server authored this copy on purpose; the client renders it verbatim.
    await expect(api.paste("x", "job")).rejects.toMatchObject({
      code: "no_text_layer",
      message: "No selectable text was found — this looks like a scan.",
      hint: "Paste the text instead.",
    });
  });

  it("distinguishes a network failure from an API failure", async () => {
    vi.stubGlobal("fetch", () => Promise.reject(new TypeError("Failed to fetch")));

    // "Nothing has been lost" is true here and false for every ApiError, and
    // that distinction is the whole difference in what the UI should say.
    await expect(api.workspace()).rejects.toBeInstanceOf(OfflineError);
  });

  it("survives an error response that is not JSON", async () => {
    vi.stubGlobal("fetch", () =>
      Promise.resolve({
        ok: false,
        status: 502,
        json: () => Promise.reject(new SyntaxError("not json")),
      } as unknown as Response),
    );

    // A proxy returning an HTML error page must not crash the client on the way
    // to reporting it.
    await expect(api.workspace()).rejects.toBeInstanceOf(ApiError);
  });

  it("does not try to parse a 204 body", async () => {
    // DELETE returns 204 with no body at all; calling .json() on it throws.
    vi.stubGlobal("fetch", () =>
      Promise.resolve({ ok: true, status: 204, json: () => Promise.reject(new Error("no body")) } as unknown as Response),
    );

    await expect(api.remove("doc-1")).resolves.toBeUndefined();
  });
});

describe("bootstrap", () => {
  it("creates a session only when there isn't one", async () => {
    const calls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string) => {
        calls.push(url);
        if (calls.length === 1) {
          return respond(401, { error_code: "session_required", message: "no session" }, false);
        }
        return respond(200, { id: "s1", documents: [] });
      }),
    );

    await api.bootstrap();

    // GET first: a returning visitor is the common case and pays one round
    // trip; only a first visit pays for the POST.
    expect(calls[0]).toContain("/sessions/current/");
    expect(calls[1]).toContain("/sessions/");
    expect(calls).toHaveLength(3);
  });

  it("costs one request for a returning visitor", async () => {
    const fetchMock = vi.fn(() => respond(200, { id: "s1", documents: [] }));
    vi.stubGlobal("fetch", fetchMock);

    await api.bootstrap();

    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

describe("delete everything", () => {
  it("mints a replacement workspace immediately", async () => {
    const calls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string, init: RequestInit) => {
        calls.push(`${init.method ?? "GET"} ${url.split("/api/v1")[1]}`);
        return respond(url.endsWith("current/") && init.method === "DELETE" ? 204 : 200, {
          id: "s2",
          documents: [],
        });
      }),
    );

    await api.deleteEverything();

    // DELETE clears the cookie, so without the re-mint a successful deletion
    // drops the user into the "session expired" state — a failure message for
    // the thing that just worked.
    expect(calls).toEqual([
      "DELETE /sessions/current/",
      "POST /sessions/",
      "GET /sessions/current/",
    ]);
  });
});
