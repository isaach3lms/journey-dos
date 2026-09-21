"""Reading the files SongSelect hands a church.

SongSelect has an API, but CCLI only opens it to approved software partners
(Planning Center, ProPresenter, and a short list of others). A church cannot
switch it on with its account number. What every SongSelect subscriber *can*
do is download a song, and these are the three formats that download comes in:

- **USR** ("SongShow Plus" / "SongSelect Import File"). An INI-style file with
  `[S A1234567]` carrying the CCLI song number, then `Title=`, `Author=`,
  `Copyright=`, `Keys=`.
- **TXT** (lyrics as text). The title on the first line, then lyrics, then a
  footer: `CCLI Song # 1234567`, an author line, a `©` line.
- **ChordPro** (`.cho`, `.chopro`, `.chordpro`, `.pro`). Directives in braces:
  `{title: ...}`, `{key: G}`, `{tempo: 72}`, and a CCLI line in the footer.

**Metadata only.** Every format carries the lyrics, and this module throws them
away on purpose: see the module docstring in app/models/service.py. The church's
licence covers the church reproducing the words, it does not make this
platform a lyric store. What a plan needs is the title, the authors, the CCLI
number, and a key. The words stay in SongSelect and ProPresenter.

Pure functions. No Flask, no database.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.music import UnknownKey, normalize_key

MAX_FILE_BYTES = 512 * 1024
MAX_FILES = 50

EXTENSIONS = {
    "usr": "usr",
    "txt": "txt",
    "cho": "chordpro",
    "chopro": "chordpro",
    "chordpro": "chordpro",
    "pro": "chordpro",
    "crd": "chordpro",
}

# What the upload box accepts. Kept here so the template and the check agree.
ACCEPT = ",".join(f".{ext}" for ext in EXTENSIONS)

_CCLI_RE = re.compile(r"CCLI\s*(?:Song)?\s*(?:#|No\.?|Number)?\s*:?\s*(\d{3,9})", re.I)
_DIRECTIVE_RE = re.compile(r"^\{\s*([A-Za-z_]+)\s*:?\s*(.*?)\s*\}\s*$")


class NotASong(ValueError):
    """The file could not be read as a SongSelect download.

    `reason` is a key into SERVICES (`import_why_<reason>`), so the message a
    staff member sees lives with the rest of the copy.
    """

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class SongMeta:
    title: str
    author: str | None = None
    ccli_number: str | None = None
    default_key: str | None = None
    tempo_bpm: int | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _decode(data: bytes) -> str:
    """SongSelect has shipped UTF-8, UTF-8 with a BOM, UTF-16 and Windows-1252
    over the years. Try them in the order that cannot misread the others."""
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def _authors(raw: str | None) -> str | None:
    """`A | B | C` or `A; B` becomes `A, B, C`. Order kept, repeats dropped."""
    if not raw:
        return None
    parts = re.split(r"\s*[|;]\s*|\s*/t\s*", raw.strip())
    seen: list[str] = []
    for part in parts:
        part = part.strip().strip(",")
        if part and part not in seen:
            seen.append(part)
    return ", ".join(seen)[:200] or None


def _key(raw: str | None) -> str | None:
    """First key only. USR lists every published key; the first is the one the
    song is charted in. Anything we cannot spell is dropped, not guessed."""
    if not raw:
        return None
    first = re.split(r"[\s,|/]+|/t", raw.strip())[0]
    try:
        return normalize_key(first)
    except UnknownKey:
        return None


def _tempo(raw: str | None) -> int | None:
    match = re.search(r"\d{2,3}", raw or "")
    if not match:
        return None
    bpm = int(match.group())
    return bpm if 40 <= bpm <= 220 else None


def _ccli(raw: str | None) -> str | None:
    digits = re.sub(r"\D", "", raw or "")
    return digits if 3 <= len(digits) <= 9 else None


def _finish(title, author=None, ccli=None, key=None, tempo=None) -> SongMeta:
    title = re.sub(r"\s+", " ", (title or "")).strip()
    if not title:
        raise NotASong("no_title")
    return SongMeta(
        title=title[:200],
        author=_authors(author),
        ccli_number=_ccli(ccli),
        default_key=_key(key),
        tempo_bpm=_tempo(tempo),
    )


# ---------------------------------------------------------------------------
# Formats
# ---------------------------------------------------------------------------

def parse_usr(text: str) -> SongMeta:
    fields: dict[str, str] = {}
    ccli = None
    for line in text.splitlines():
        line = line.strip()
        header = re.match(r"^\[S\s+A?(\d+)\]$", line, re.I)
        if header:
            ccli = header.group(1)
            continue
        if "=" in line and not line.startswith("["):
            name, _, value = line.partition("=")
            fields.setdefault(name.strip().lower(), value.strip())
    if "title" not in fields:
        raise NotASong("unreadable")
    return _finish(
        fields.get("title"),
        author=fields.get("author"),
        ccli=ccli,
        key=fields.get("keys") or fields.get("key"),
        tempo=fields.get("tempo"),
    )


def parse_chordpro(text: str) -> SongMeta:
    directives: dict[str, str] = {}
    ccli = None
    for line in text.splitlines():
        line = line.strip()
        match = _DIRECTIVE_RE.match(line)
        if match:
            name, value = match.group(1).lower(), match.group(2)
            directives.setdefault(name, value)
            found = _CCLI_RE.search(line)
            if found and not ccli:
                ccli = found.group(1)
            continue
        found = _CCLI_RE.search(line)
        if found and not ccli and "licen" not in line.lower():
            ccli = found.group(1)

    title = directives.get("title") or directives.get("t")
    if not title:
        raise NotASong("unreadable")
    author = (
        directives.get("author") or directives.get("artist")
        or directives.get("composer") or directives.get("lyricist")
        or directives.get("subtitle") or directives.get("st")
    )
    ccli = directives.get("ccli") or ccli
    return _finish(
        title,
        author=author,
        ccli=ccli,
        key=directives.get("key"),
        tempo=directives.get("tempo"),
    )


def parse_txt(text: str) -> SongMeta:
    lines = [line.rstrip() for line in text.splitlines()]
    body = [line for line in lines if line.strip()]
    if not body:
        raise NotASong("empty")

    title = body[0].strip()
    ccli = author = None
    for index, line in enumerate(lines):
        stripped = line.strip()
        # "CCLI License # 12345" is the church's licence, not the song.
        if "licen" in stripped.lower():
            continue
        found = _CCLI_RE.search(stripped)
        if found:
            ccli = found.group(1)
            # The line after the song number names the writers.
            for following in lines[index + 1:]:
                following = following.strip()
                if not following:
                    continue
                if not following.startswith(("©", "(c)", "Copyright", "For use")):
                    author = following
                break
            break

    # A plain text file with no CCLI footer is somebody's notes, not a
    # SongSelect download. Importing its first line as a song title would put
    # "Things to bring Sunday" in the song library.
    if ccli is None:
        raise NotASong("no_ccli")
    return _finish(title, author=author, ccli=ccli)


def parse(filename: str, data: bytes) -> SongMeta:
    """Read one download. Raises NotASong with a reason key."""
    if not data or not data.strip():
        raise NotASong("empty")
    if len(data) > MAX_FILE_BYTES:
        raise NotASong("too_big")
    if data.startswith(b"%PDF-"):
        raise NotASong("pdf")

    ext = (filename or "").rsplit(".", 1)[-1].lower() if "." in (filename or "") else ""
    kind = EXTENSIONS.get(ext)
    if kind is None:
        raise NotASong("wrong_type")

    text = _decode(data)
    if kind == "usr":
        return parse_usr(text)
    if kind == "chordpro":
        return parse_chordpro(text)
    return parse_txt(text)
