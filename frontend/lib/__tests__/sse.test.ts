import { describe, expect, it } from "vitest";

import { SseParser, payloadOf } from "@/lib/sse";

const STREAM = [
  'event: status\ndata: {"phase":"resolving","detail":"Working out which documents to read…"}\n\n',
  'event: scope\ndata: {"job_ids":["a"],"intent":"gap","resolved_from":"Job #2","demo_mode":true}\n\n',
  ": ping\n\n",
  'event: sources\ndata: {"chunks":[{"id":"c1","preview":"REQUIREMENTS — 5+ years"}]}\n\n',
  'event: delta\ndata: {"text":"You\'re short on production "}\n\n',
  'event: citation\ndata: {"index":1,"answer_char":26,"char_start":104,"char_end":157,"cited_text":"— Kubernetes at scale"}\n\n',
  'event: delta\ndata: {"text":"Kubernetes."}\n\n',
  'event: done\ndata: {"message_id":"m1","grounding":{"citations":1,"max_score":0.57,"low_evidence":false}}\n\n',
].join("");

const TRUE_FRAMES = 7; // the ping is a comment, not a frame

function feed(chunkSize: number): ReturnType<SseParser["push"]> {
  const bytes = new TextEncoder().encode(STREAM);
  const parser = new SseParser();
  const frames = [];
  for (let i = 0; i < bytes.length; i += chunkSize) {
    frames.push(...parser.push(bytes.slice(i, i + chunkSize)));
  }
  frames.push(...parser.end());
  return frames;
}

describe("chunk boundaries", () => {
  // A network chunk has nothing to do with a frame. Measured against a real
  // capture, a naive per-chunk split recovers zero frames of 143.
  it.each([1, 2, 3, 7, 13, 64, 1000, 100_000])(
    "recovers every frame at %i-byte chunks",
    (size) => {
      const frames = feed(size);

      expect(frames).toHaveLength(TRUE_FRAMES);
      expect(frames.map((f) => f.event)).toEqual([
        "status",
        "scope",
        "sources",
        "delta",
        "citation",
        "delta",
        "done",
      ]);
    },
  );

  it("survives a multi-byte character split across a chunk boundary", () => {
    // The payloads carry en-dashes and curly quotes lifted from document text.
    // Without TextDecoder({stream:true}) the halves decode to U+FFFD and the
    // cited_text is silently corrupted — the citation still renders, pointing
    // at a span whose text no longer matches the document.
    const oneByte = feed(1);
    const citation = payloadOf<{ cited_text: string }>(
      oneByte.find((f) => f.event === "citation")!,
    );

    expect(citation?.cited_text).toBe("— Kubernetes at scale");
    expect(JSON.stringify(oneByte)).not.toContain("�");
  });

  it("reassembles a frame separator split down the middle", () => {
    const parser = new SseParser();

    expect(parser.pushText('event: delta\ndata: {"text":"hi"}\n')).toHaveLength(0);
    const frames = parser.pushText("\nevent: done\ndata: {}\n\n");

    expect(frames.map((f) => f.event)).toEqual(["delta", "done"]);
  });
});

describe("framing rules", () => {
  it("drops heartbeat comments rather than parsing them", () => {
    // `: ping` every 15s keeps proxies from reaping an idle connection during
    // adaptive thinking. Treating one as data hands JSON.parse the word "ping".
    const parser = new SseParser();

    expect(parser.pushText(": ping\n\n")).toHaveLength(0);
  });

  it("strips exactly one leading space, not all whitespace", () => {
    // `.trim()` here would eat meaningful whitespace inside a delta, and a
    // delta is frequently nothing but a space between two words.
    const parser = new SseParser();
    const [frame] = parser.pushText('event: delta\ndata: {"text":"  indented"}\n\n');

    expect(payloadOf<{ text: string }>(frame)?.text).toBe("  indented");
  });

  it("joins multiple data lines with a newline, per the spec", () => {
    const parser = new SseParser();
    const [frame] = parser.pushText("event: x\ndata: one\ndata: two\n\n");

    expect(frame.data).toBe("one\ntwo");
  });

  it("accepts CRLF, which a proxy is entitled to rewrite", () => {
    const parser = new SseParser();
    const frames = parser.pushText("event: delta\r\ndata: {}\r\n\r\n");

    expect(frames.map((f) => f.event)).toEqual(["delta"]);
  });

  it("discards a truncated trailing frame rather than parsing half of it", () => {
    // The connection dropped mid-answer. Half a JSON payload is not a smaller
    // event, it is a different one.
    const parser = new SseParser();
    parser.pushText('event: delta\ndata: {"text":"partial');

    expect(parser.end()).toHaveLength(0);
  });

  it("ignores an event with no data field", () => {
    const parser = new SseParser();

    expect(parser.pushText("event: lonely\n\n")).toHaveLength(0);
  });
});

describe("payload parsing", () => {
  it("returns null rather than throwing on malformed JSON", () => {
    // One bad frame must not take down a stream that is otherwise fine.
    expect(payloadOf({ event: "delta", data: "{oops" })).toBeNull();
  });
});

describe("CRLF split across a chunk boundary", () => {
  // The failure this guards is invisible locally: this server sends no CR at
  // all, so byte-chunk replay of a real capture is always clean. Behind a proxy
  // that rewrites line endings, a CRLF straddling a chunk boundary used to be
  // normalised into TWO line breaks — a spurious frame separator that split
  // `event: delta` from its `data:` line. The event name was then lost and
  // every delta arrived as `message`, so the client's switch ignored the whole
  // answer while reporting no error.
  const CRLF = "event: delta\r\ndata: {\"text\":\"a\"}\r\n\r\nevent: done\r\ndata: {}\r\n\r\n";

  it("keeps the event names when fed whole", () => {
    const parser = new SseParser();

    expect(parser.pushText(CRLF).map((f) => f.event)).toEqual(["delta", "done"]);
  });

  it("keeps them when every CR and LF is split apart", () => {
    const parser = new SseParser();
    const frames = [];
    for (const char of CRLF) frames.push(...parser.pushText(char));
    frames.push(...parser.end());

    expect(frames.map((f) => f.event)).toEqual(["delta", "done"]);
  });

  it("keeps them when the cut lands exactly between CR and LF", () => {
    const parser = new SseParser();
    const at = CRLF.indexOf("\r\n") + 1;
    const frames = [
      ...parser.pushText(CRLF.slice(0, at)),
      ...parser.pushText(CRLF.slice(at)),
      ...parser.end(),
    ];

    expect(frames.map((f) => f.event)).toEqual(["delta", "done"]);
    expect(frames.every((f) => f.event !== "message")).toBe(true);
  });

  it("does not invent a frame from a lone trailing CR", () => {
    const parser = new SseParser();

    expect(parser.pushText("event: delta\r")).toHaveLength(0);
    expect(parser.pushText("\ndata: {}\r\n\r\n").map((f) => f.event)).toEqual(["delta"]);
  });
});
