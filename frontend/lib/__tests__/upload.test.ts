import { describe, expect, it } from "vitest";

import { MAX_FILE_BYTES } from "@/lib/constants";
import { dragLooksAcceptable, extensionOf, rejectionFor } from "@/lib/upload";

function file(name: string, size: number, type = "application/pdf"): File {
  // `size` has to be stubbed on the File itself. Setting it on the source Blob
  // does nothing: the File constructor recomputes size from the content it is
  // given, so every fixture arrived as 0 bytes and looked like an empty file.
  const handle = new File([], name, { type });
  Object.defineProperty(handle, "size", { value: size });
  return handle;
}

describe("client-side rejection", () => {
  it("refuses an oversized file before it is transferred", () => {
    // The whole point: an 11 MB file should not cross the network in order to
    // be told it is 11 MB.
    const rejection = rejectionFor(file("cv.pdf", MAX_FILE_BYTES + 1));

    expect(rejection?.code).toBe("too_large");
    expect(rejection?.offerPaste).toBe(true);
  });

  it("checks size before type, matching the server's order", () => {
    // Otherwise fixing the type merely reveals the next complaint, and the user
    // discovers their file was too large only on the second attempt.
    expect(rejectionFor(file("virus.exe", MAX_FILE_BYTES + 1))?.code).toBe("too_large");
  });

  it("refuses an unsupported extension", () => {
    expect(rejectionFor(file("notes.pages", 1000))?.code).toBe("unsupported_type");
  });

  it("refuses an empty file", () => {
    expect(rejectionFor(file("empty.pdf", 0))?.code).toBe("parse_failed");
  });

  it("accepts every extension the server accepts", () => {
    for (const name of ["cv.pdf", "cv.docx", "cv.txt", "cv.md"]) {
      expect(rejectionFor(file(name, 5000))).toBeNull();
    }
  });

  it("is case-insensitive about the extension", () => {
    expect(rejectionFor(file("CV.PDF", 5000))).toBeNull();
    expect(extensionOf("Report.DOCX")).toBe("docx");
  });

  it("treats a file with no extension as unsupported", () => {
    expect(rejectionFor(file("resume", 5000))?.code).toBe("unsupported_type");
  });
});

describe("drag acceptance", () => {
  const items = (types: string[]) =>
    types.map((type) => ({ kind: "file", type })) as unknown as DataTransferItemList;

  it("accepts a typeless item rather than refusing a file that was fine", () => {
    // During a drag the browser withholds the filename and .md often arrives
    // with an empty type. A false "supported" resolves in a moment; a false
    // rejection blocks a valid upload with no explanation.
    expect(dragLooksAcceptable(items([""]))).toBe(true);
  });

  it("rejects a clearly unsupported type", () => {
    expect(dragLooksAcceptable(items(["image/png"]))).toBe(false);
  });

  it("rejects a mixed drag containing anything unsupported", () => {
    expect(dragLooksAcceptable(items(["application/pdf", "image/png"]))).toBe(false);
  });

  it("accepts an empty drag rather than flashing a rejection", () => {
    expect(dragLooksAcceptable(null)).toBe(true);
  });
});
