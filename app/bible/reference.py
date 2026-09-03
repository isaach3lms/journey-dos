"""Parsing a scripture reference.

Pure functions, no database. Worth its own module because a worship leader or a
pastor types a reference the way they say it, and the shapes are irregular:
"Psalm 139:1-6", "John 1", "1 Cor 13:4-7", "Ps 23", "1 John 4:7-8".

Two ambiguities matter and both are handled explicitly rather than guessed at:

**Leading numbers are part of the book.** "1 John 4" is the first epistle of
John, chapter 4. "1 Corinthians" is not Corinthians chapter 1. Parsing left to
right without knowing that produces confident nonsense.

**Psalm and Psalms are the same book.** So are Song of Songs and Song of
Solomon, and Revelation and Revelations, which is wrong but universal.

An unparseable reference returns None rather than raising. A typo in a reading
plan should show "we could not find that passage" on one card, not take down
the page a member is reading.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Canonical name, then every spelling a person actually types. Order matters
# inside a book's aliases only for display; matching is exact after
# normalization.
BOOKS: tuple[tuple[str, tuple[str, ...], int], ...] = (
    ("Genesis", ("genesis", "gen", "ge", "gn"), 50),
    ("Exodus", ("exodus", "exod", "exo", "ex"), 40),
    ("Leviticus", ("leviticus", "lev", "le", "lv"), 27),
    ("Numbers", ("numbers", "num", "nu", "nm"), 36),
    ("Deuteronomy", ("deuteronomy", "deut", "deu", "dt"), 34),
    ("Joshua", ("joshua", "josh", "jos"), 24),
    ("Judges", ("judges", "judg", "jdg"), 21),
    ("Ruth", ("ruth", "rth", "ru"), 4),
    ("1 Samuel", ("1 samuel", "1 sam", "1sa", "i samuel"), 31),
    ("2 Samuel", ("2 samuel", "2 sam", "2sa", "ii samuel"), 24),
    ("1 Kings", ("1 kings", "1 kgs", "1ki", "i kings"), 22),
    ("2 Kings", ("2 kings", "2 kgs", "2ki", "ii kings"), 25),
    ("1 Chronicles", ("1 chronicles", "1 chron", "1 chr", "1ch"), 29),
    ("2 Chronicles", ("2 chronicles", "2 chron", "2 chr", "2ch"), 36),
    ("Ezra", ("ezra", "ezr"), 10),
    ("Nehemiah", ("nehemiah", "neh", "ne"), 13),
    ("Esther", ("esther", "esth", "est"), 10),
    ("Job", ("job", "jb"), 42),
    # Psalm and Psalms are the same book and people type both.
    ("Psalms", ("psalms", "psalm", "psa", " psalm", "ps", "pss"), 150),
    ("Proverbs", ("proverbs", "prov", "pro", "prv", "pr"), 31),
    ("Ecclesiastes", ("ecclesiastes", "eccles", "eccl", "ecc", "qoh"), 12),
    ("Song of Songs", ("song of songs", "song of solomon", "song", "sos", "canticles"), 8),
    ("Isaiah", ("isaiah", "isa", "is"), 66),
    ("Jeremiah", ("jeremiah", "jer", "je"), 52),
    ("Lamentations", ("lamentations", "lam", "la"), 5),
    ("Ezekiel", ("ezekiel", "ezek", "eze", "ezk"), 48),
    ("Daniel", ("daniel", "dan", "da", "dn"), 12),
    ("Hosea", ("hosea", "hos", "ho"), 14),
    ("Joel", ("joel", "joe", "jl"), 3),
    ("Amos", ("amos", "amo", "am"), 9),
    ("Obadiah", ("obadiah", "obad", "oba", "ob"), 1),
    ("Jonah", ("jonah", "jon", "jnh"), 4),
    ("Micah", ("micah", "mic", "mi"), 7),
    ("Nahum", ("nahum", "nah", "na"), 3),
    ("Habakkuk", ("habakkuk", "hab", "hb"), 3),
    ("Zephaniah", ("zephaniah", "zeph", "zep", "zp"), 3),
    ("Haggai", ("haggai", "hag", "hg"), 2),
    ("Zechariah", ("zechariah", "zech", "zec", "zc"), 14),
    ("Malachi", ("malachi", "mal", "ml"), 4),
    ("Matthew", ("matthew", "matt", "mat", "mt"), 28),
    ("Mark", ("mark", "mrk", "mk", "mr"), 16),
    ("Luke", ("luke", "luk", "lk"), 24),
    ("John", ("john", "jhn", "jn"), 21),
    ("Acts", ("acts", "act", "ac"), 28),
    ("Romans", ("romans", "rom", "ro", "rm"), 16),
    ("1 Corinthians", ("1 corinthians", "1 cor", "1co", "i corinthians"), 16),
    ("2 Corinthians", ("2 corinthians", "2 cor", "2co", "ii corinthians"), 13),
    ("Galatians", ("galatians", "gal", "ga"), 6),
    ("Ephesians", ("ephesians", "eph", "ep"), 6),
    ("Philippians", ("philippians", "phil", "php"), 4),
    ("Colossians", ("colossians", "col", "co"), 4),
    ("1 Thessalonians", ("1 thessalonians", "1 thess", "1 th", "1th"), 5),
    ("2 Thessalonians", ("2 thessalonians", "2 thess", "2 th", "2th"), 3),
    ("1 Timothy", ("1 timothy", "1 tim", "1ti"), 6),
    ("2 Timothy", ("2 timothy", "2 tim", "2ti"), 4),
    ("Titus", ("titus", "tit", "ti"), 3),
    ("Philemon", ("philemon", "philem", "phm"), 1),
    ("Hebrews", ("hebrews", "heb", "hb"), 13),
    ("James", ("james", "jas", "jm"), 5),
    ("1 Peter", ("1 peter", "1 pet", "1pe"), 5),
    ("2 Peter", ("2 peter", "2 pet", "2pe"), 3),
    ("1 John", ("1 john", "1 jn", "1jn", "i john"), 5),
    ("2 John", ("2 john", "2 jn", "2jn"), 1),
    ("3 John", ("3 john", "3 jn", "3jn"), 1),
    ("Jude", ("jude", "jud", "jde"), 1),
    # People say Revelations. They are wrong and they are consistent about it.
    ("Revelation", ("revelation", "revelations", "rev", "re", "apocalypse"), 22),
)

BOOK_NAMES: tuple[str, ...] = tuple(name for name, _, _ in BOOKS)
CHAPTER_COUNTS: dict[str, int] = {name: chapters for name, _, chapters in BOOKS}

_ALIAS_TO_BOOK: dict[str, str] = {}
for _name, _aliases, _chapters in BOOKS:
    _ALIAS_TO_BOOK[_name.lower()] = _name
    for _alias in _aliases:
        _ALIAS_TO_BOOK[_alias.strip()] = _name

# Ordinal prefixes people write in front of a book name.
_ORDINALS = {"i": "1", "ii": "2", "iii": "3", "first": "1", "second": "2", "third": "3"}

_REFERENCE = re.compile(
    r"^\s*(?P<book>.+?)\s*"
    r"(?P<chapter>\d+)"
    r"(?:\s*[:.]\s*(?P<start>\d+)(?:\s*[-–—]\s*(?P<end>\d+))?)?\s*$"
)


@dataclass(frozen=True)
class Reference:
    book: str
    chapter: int
    start_verse: int | None = None
    end_verse: int | None = None

    def __str__(self) -> str:
        base = f"{self.book} {self.chapter}"
        if self.start_verse is None:
            return base
        if self.end_verse is None or self.end_verse == self.start_verse:
            return f"{base}:{self.start_verse}"
        return f"{base}:{self.start_verse}-{self.end_verse}"

    @property
    def is_whole_chapter(self) -> bool:
        return self.start_verse is None

    def covers(self, verse: int) -> bool:
        if self.start_verse is None:
            return True
        end = self.end_verse or self.start_verse
        return self.start_verse <= verse <= end


def normalize_book(raw: str | None) -> str | None:
    """Resolve any spelling a person types to a canonical book name."""
    if not raw:
        return None

    text = " ".join(raw.strip().lower().replace(".", " ").split())
    if not text:
        return None

    # "I John" and "First John" become "1 John" before anything else looks at
    # them. A leading number is part of the book, never a chapter.
    parts = text.split()
    if parts and parts[0] in _ORDINALS:
        parts[0] = _ORDINALS[parts[0]]
        text = " ".join(parts)

    if text in _ALIAS_TO_BOOK:
        return _ALIAS_TO_BOOK[text]

    # "1john" written without a space.
    squashed = re.sub(r"^(\d)\s*", r"\1 ", text)
    if squashed in _ALIAS_TO_BOOK:
        return _ALIAS_TO_BOOK[squashed]

    return None


def parse(raw: str | None) -> Reference | None:
    """Turn what somebody typed into a reference, or None.

    None rather than an exception: a typo in a reading plan should show one
    apologetic card, not take down the page a member is reading.
    """
    if not raw or not raw.strip():
        return None

    match = _REFERENCE.match(raw.replace("\u00a0", " "))
    if not match:
        # A bare book name with no chapter, like "Jude" or "Philemon".
        book = normalize_book(raw)
        if book and CHAPTER_COUNTS.get(book) == 1:
            return Reference(book, 1)
        return None

    book = normalize_book(match.group("book"))
    if book is None:
        return None

    chapter = int(match.group("chapter"))
    if chapter < 1 or chapter > CHAPTER_COUNTS.get(book, 150):
        return None

    start = match.group("start")
    end = match.group("end")
    start_verse = int(start) if start else None
    end_verse = int(end) if end else None

    if start_verse is not None and start_verse < 1:
        return None
    # "John 3:16-12" is a typo, not a backwards range. Reading it as one verse
    # is the least surprising thing to do with it.
    if end_verse is not None and start_verse is not None and end_verse < start_verse:
        end_verse = start_verse

    return Reference(book, chapter, start_verse, end_verse)


def is_valid(raw: str | None) -> bool:
    return parse(raw) is not None
