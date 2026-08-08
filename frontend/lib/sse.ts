/**
 * An incremental Server-Sent Events parser.
 *
 * `EventSource` cannot be used here: it is GET-only and this endpoint needs a
 * POST with a JSON body. So the stream is read from `fetch` with a
 * `ReadableStream`, and the framing has to be reassembled by hand.
 *
 * Two bugs are worth naming, because both are invisible until they aren't:
 *
 * **Frames split across chunk boundaries.** A network chunk has nothing to do
 * with a frame — the separator `\n\n` routinely lands mid-chunk. Splitting each
 * chunk independently discards every frame that straddles one; measured against
 * a real captured stream, a naive per-chunk split recovers *zero* of 143 frames.
 * Hence the carry-over buffer.
 *
 * **Multi-byte characters split across chunk boundaries.** The payloads contain
 * en-dashes and curly quotes from document text, and UTF-8 encodes those over
 * two or three bytes which a chunk can cut in half. `TextDecoder` handles it
 * only when told the input is streaming; without `{stream: true}` the halves
 * decode to U+FFFD and the JSON either breaks or silently corrupts a citation's
 * `cited_text`.
 *
 * Pure and DOM-free, so it is tested in node against recorded byte streams.
 */

export interface SseFrame {
  event: string;
  data: string;
}

export class SseParser {
  private buffer = "";
  private readonly decoder = new TextDecoder();

  /** Feed raw bytes; get back whatever complete frames they completed. */
  push(chunk: Uint8Array): SseFrame[] {
    // `stream: true` keeps a partial multi-byte sequence in the decoder's own
    // buffer until the rest of it arrives.
    this.buffer += this.decoder.decode(chunk, { stream: true });
    return this.drain();
  }

  /** Feed already-decoded text. Used by tests and by the replay fixtures. */
  pushText(text: string): SseFrame[] {
    this.buffer += text;
    return this.drain();
  }

  /**
   * Flush at end-of-stream.
   *
   * A well-formed stream ends with a separator and this returns nothing. A
   * truncated one — the connection dropped mid-answer — leaves a partial frame
   * that is deliberately *discarded* rather than parsed: half a JSON payload is
   * not a smaller event, it is a different one.
   */
  end(): SseFrame[] {
    this.buffer += this.decoder.decode();
    const frames = this.drain();
    this.buffer = "";
    return frames;
  }

  private drain(): SseFrame[] {
    // Normalise line endings. This backend emits LF only — verified on a live
    // capture — but the spec permits CRLF and a proxy may rewrite them, at which
    // point every frame boundary would be missed.
    //
    // A *trailing* CR is held back rather than converted. Normalising it
    // immediately turns one CRLF that happened to straddle a chunk boundary
    // into two line breaks — a spurious frame separator that splits `event:
    // delta` from its `data:` line. The event name is then lost and every delta
    // arrives under the default name `message`, so a client switch statement
    // silently ignores the entire answer. Byte-chunk replay never shows it,
    // because this server sends no CR at all; a proxy that rewrites them does.
    const held = this.buffer.endsWith("\r");
    if (held) this.buffer = this.buffer.slice(0, -1);
    this.buffer = this.buffer.replace(/\r\n/g, "\n").replace(/\r/g, "\n");

    const frames: SseFrame[] = [];
    let separator = this.buffer.indexOf("\n\n");

    while (separator !== -1) {
      const raw = this.buffer.slice(0, separator);
      this.buffer = this.buffer.slice(separator + 2);

      const frame = parseFrame(raw);
      if (frame) frames.push(frame);

      separator = this.buffer.indexOf("\n\n");
    }

    // Put the held CR back so the next chunk can complete its CRLF.
    if (held) this.buffer += "\r";

    return frames;
  }
}

function parseFrame(raw: string): SseFrame | null {
  let event = "message";
  const data: string[] = [];

  for (const line of raw.split("\n")) {
    // A line beginning with a colon is a comment. The server sends `: ping`
    // every 15 seconds to stop proxies reaping an idle connection during
    // adaptive thinking; treating one as data would hand `JSON.parse` a `ping`.
    if (line.startsWith(":")) continue;

    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    // Exactly one optional leading space is stripped, per the spec — not
    // `.trim()`, which would eat meaningful whitespace inside a payload.
    let value = colon === -1 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);

    if (field === "event") event = value;
    else if (field === "data") data.push(value);
  }

  if (data.length === 0) return null;
  // Multiple data lines are joined with newlines. This backend always emits one
  // (its payloads are `json.dumps`, which escapes newlines), but the join is
  // what the spec says and costs nothing.
  return { event, data: data.join("\n") };
}

/** Parse a frame's payload, or null if it is not the JSON we expected. */
export function payloadOf<T>(frame: SseFrame): T | null {
  try {
    return JSON.parse(frame.data) as T;
  } catch {
    return null;
  }
}
