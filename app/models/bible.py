"""The World English Bible, stored locally.

**Global, not tenant scoped.** Per spec v3 section E.4, this and the book table
are the only two in the system without a `church_id`. Scripture is not a
church's data and copying all 31,000 verses per tenant would be absurd.

**Public domain only.** The WEB is out of copyright and safe to store, ship,
and cache. Licensed translations are not, which is why nothing in this file has
a `translation` column that could quietly accumulate NIV text. See
`app/bible/providers.py`: licensed text is fetched and rendered, never
persisted.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.extensions import db
from app.models.base import TimestampMixin

# The only translation this table is allowed to hold.
WEB_CODE = "web"
WEB_NAME = "World English Bible"


class BibleVerse(TimestampMixin, db.Model):
    """One verse of the WEB. No church_id: see the module docstring."""

    __tablename__ = "bible_verse"
    __table_args__ = (
        UniqueConstraint("book", "chapter", "verse", name="uq_bible_verse_ref"),
        Index("ix_bible_book_chapter", "book", "chapter", "verse"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    book: Mapped[str] = mapped_column(String(40), nullable=False)
    chapter: Mapped[int] = mapped_column(Integer, nullable=False)
    verse: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)

    def __repr__(self) -> str:
        return f"<BibleVerse {self.book} {self.chapter}:{self.verse}>"

    @classmethod
    def passage(cls, reference) -> list["BibleVerse"]:
        """Every verse a reference covers, in order."""
        query = db.select(cls).where(
            cls.book == reference.book, cls.chapter == reference.chapter
        )
        if reference.start_verse is not None:
            end = reference.end_verse or reference.start_verse
            query = query.where(
                cls.verse >= reference.start_verse, cls.verse <= end
            )
        return list(db.session.scalars(query.order_by(cls.verse)))

    @classmethod
    def has_chapter(cls, book: str, chapter: int) -> bool:
        return db.session.scalar(
            db.select(cls.id).where(cls.book == book, cls.chapter == chapter).limit(1)
        ) is not None

    @classmethod
    def verse_count(cls) -> int:
        return db.session.scalar(db.select(func.count(cls.id))) or 0

    @classmethod
    def loaded_books(cls) -> list[str]:
        return list(
            db.session.scalars(db.select(cls.book).distinct().order_by(cls.book))
        )
