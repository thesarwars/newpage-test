"""File intake validation.

Everything here runs *before* a byte is parsed. The uploaded file is untrusted
input from the internet, and the parsers below it are C-backed libraries reading
attacker-shaped structures — so the cheap structural checks come first.

Each failure raises with a distinct `error_code`, because the UI's job is to say
what went wrong and what to do instead: "this PDF has no text layer" points at
the paste-text fallback, and "encrypted" does not.
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass
from typing import BinaryIO

from rest_framework import status

from apps.core.errors import ApiError

MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_PAGES = 30
# For *parsed files*: below this, the file almost certainly has no text layer.
MIN_EXTRACTED_CHARS = 200
# For *pasted text*: the user typed it deliberately, so the only question is
# whether there is enough to analyse. A terse but real job blurb is ~150 chars,
# and telling its author "this looks like a scan" would be nonsense.
MIN_PASTED_CHARS = 50
# And an upper bound, which the paste path did not have. Ingest is synchronous,
# so an unbounded paste is unbounded request time: 200 KB measured at 20 s and
# 500 KB at 51 s, and behind gunicorn's 30 s default those are 502s rather than
# slow successes. 30 pages of dense prose is roughly 120k characters, so this is
# the same ceiling the file path enforces via MAX_PAGES, expressed in the unit
# the paste path actually has.
MAX_PASTED_CHARS = 120_000
# A DOCX is a zip. A 200x compression ratio is not a résumé, it is a zip bomb
# aimed at whatever unpacks it.
MAX_ZIP_RATIO = 200

ALLOWED_EXTENSIONS = frozenset({"pdf", "docx", "txt", "md"})

# Leading bytes, checked against the file itself rather than the client's
# Content-Type header — which is attacker-controlled and means nothing.
_MAGIC = {
    "pdf": [b"%PDF-"],
    "docx": [b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"],
}


class UploadError(ApiError):
    # Annotated `int` rather than left to inference: without it mypy narrows this
    # to Literal[422] and every subclass that needs a different status (413) is
    # an assignment error.
    status_code: int = status.HTTP_422_UNPROCESSABLE_ENTITY


class FileTooLargeError(UploadError):
    status_code = status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
    error_code = "too_large"
    message = "That file is larger than 10 MB."
    hint = "Export a smaller PDF, or paste the text instead."


class UnsupportedTypeError(UploadError):
    error_code = "unsupported_type"
    message = "That file type isn't supported."
    hint = "Upload a PDF, DOCX, TXT or Markdown file, or paste the text."


class ContentMismatchError(UploadError):
    error_code = "unsupported_type"
    message = "That file's contents don't match its extension."
    hint = "Re-export the document, or paste the text instead."


class EncryptedPdfError(UploadError):
    error_code = "encrypted_pdf"
    message = "That PDF is password-protected, so its text can't be read."
    hint = "Remove the password and re-upload, or paste the text instead."


class NoTextLayerError(UploadError):
    error_code = "no_text_layer"
    message = "No selectable text was found — this looks like a scan or an image."
    hint = "Paste the text instead. There's no OCR in this build."


class TooManyPagesError(UploadError):
    error_code = "too_many_pages"
    message = f"That document is longer than {MAX_PAGES} pages."
    hint = "Upload the relevant section, or paste the text instead."


class ParseFailedError(UploadError):
    error_code = "parse_failed"
    message = "That file couldn't be read."
    hint = "Re-export it, or paste the text instead."


class TextTooShortError(UploadError):
    error_code = "text_too_short"
    message = "There isn't enough text there to analyse."
    hint = "Paste the whole posting, including the requirements section."


class TextTooLongError(UploadError):
    error_code = "text_too_long"
    message = "That's more text than this build will analyse in one document."
    hint = "Paste the role and its requirements, or split it across two documents."


class ZipBombError(UploadError):
    error_code = "parse_failed"
    message = "That file's internal structure looks malformed."
    hint = "Re-export the document, or paste the text instead."


@dataclass(frozen=True)
class ValidatedUpload:
    extension: str
    size_bytes: int


def extension_of(filename: str) -> str:
    _, _, suffix = filename.rpartition(".")
    return suffix.lower()


def validate_upload(*, filename: str, size_bytes: int, handle: BinaryIO) -> ValidatedUpload:
    """Structural checks on an uploaded file. Raises `UploadError` on rejection.

    Leaves `handle` rewound so the caller can parse it.
    """
    if size_bytes > MAX_FILE_BYTES:
        raise FileTooLargeError()
    if size_bytes == 0:
        raise ParseFailedError(
            "That file is empty.", hint="Choose a different file, or paste the text."
        )

    extension = extension_of(filename)
    if extension not in ALLOWED_EXTENSIONS:
        raise UnsupportedTypeError()

    handle.seek(0)
    header = handle.read(8)
    handle.seek(0)

    expected = _MAGIC.get(extension)
    if expected and not any(header.startswith(prefix) for prefix in expected):
        # The extension says one thing and the bytes say another. Rejected
        # rather than sniffed-and-accepted: guessing is how you end up handing a
        # renamed archive to a PDF parser.
        raise ContentMismatchError()

    if extension == "docx":
        _reject_zip_bomb(handle)

    return ValidatedUpload(extension=extension, size_bytes=size_bytes)


def _reject_zip_bomb(handle: BinaryIO) -> None:
    handle.seek(0)
    try:
        with zipfile.ZipFile(handle) as archive:
            compressed = sum(info.compress_size for info in archive.infolist())
            uncompressed = sum(info.file_size for info in archive.infolist())
    except zipfile.BadZipFile as exc:
        raise ParseFailedError() from exc
    finally:
        handle.seek(0)

    if compressed and uncompressed / compressed > MAX_ZIP_RATIO:
        raise ZipBombError()


def validate_extracted_text(text: str, *, page_count: int) -> None:
    """Post-parse checks on a *file* — what came out, rather than what went in."""
    if page_count > MAX_PAGES:
        raise TooManyPagesError()
    if len(text.strip()) < MIN_EXTRACTED_CHARS:
        # Almost always a scanned PDF: the file is valid, the parse succeeded,
        # and there is simply no text layer. Points at the paste fallback.
        raise NoTextLayerError()


def validate_pasted_text(text: str) -> None:
    """Checks on text the user typed or pasted.

    Deliberately not `validate_extracted_text`. Reusing that here meant a user
    pasting a short-but-real job description was told "No selectable text was
    found — this looks like a scan", and pointed at the paste box they were
    already using. Same shape of failure, completely different cause.
    """
    if len(text.strip()) < MIN_PASTED_CHARS:
        raise TextTooShortError()
    if len(text) > MAX_PASTED_CHARS:
        raise TextTooLongError()


def safe_stored_name(session_id: str, document_id: str, extension: str) -> str:
    """Storage path built from ids we generated, never from the client's filename.

    The original filename is kept on the row for display and escaped at render
    time; it never touches the filesystem.
    """
    return f"{session_id}/{document_id}.{extension}"
