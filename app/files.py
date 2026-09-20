"""Checking an uploaded file before it is stored.

Three rules, in this order:

1. **It must be a PDF, by its contents.** A file named `curriculum.pdf` that
   is really an HTML page would be served back to a volunteer's browser as
   whatever it actually is. Checking the first bytes is what stops that;
   checking the name only stops honest mistakes.
2. **It must be small enough.** Per file, and per church in total, because
   these live in the database.
3. **The name is rewritten, not trusted.** A filename arrives from someone
   else's computer and ends up in a download header.
"""

from __future__ import annotations

import re

MAX_FILE_BYTES = 15 * 1024 * 1024        # one file
CHURCH_QUOTA_BYTES = 250 * 1024 * 1024   # everything one church has stored
PDF_MAGIC = b"%PDF-"

_SAFE = re.compile(r"[^A-Za-z0-9._ -]+")


class RefusedFile(Exception):
    """Raised with a key into the copy dictionary, never a raw message."""

    def __init__(self, reason: str, **fields):
        super().__init__(reason)
        self.reason = reason
        self.fields = fields


def safe_filename(raw: str | None, fallback: str = "file.pdf") -> str:
    name = _SAFE.sub("", (raw or "").strip().replace("/", " ").replace("\\", " "))
    name = re.sub(r"\s+", " ", name).strip(" .") or fallback
    if not name.lower().endswith(".pdf"):
        name = f"{name}.pdf"
    return name[-120:]


def check_pdf(data: bytes, filename: str | None, used_bytes: int = 0) -> None:
    """Raise RefusedFile if this upload cannot be stored."""
    if not data:
        raise RefusedFile("file_empty")
    if not data.startswith(PDF_MAGIC):
        raise RefusedFile("file_not_pdf")
    if not (filename or "").lower().endswith(".pdf"):
        raise RefusedFile("file_not_pdf")
    if len(data) > MAX_FILE_BYTES:
        raise RefusedFile("file_too_big", limit=MAX_FILE_BYTES // (1024 * 1024))
    if used_bytes + len(data) > CHURCH_QUOTA_BYTES:
        raise RefusedFile("file_quota", limit=CHURCH_QUOTA_BYTES // (1024 * 1024))
