"""A chart the band plays from, attached to its song.

A SongSelect PDF (chord chart, lead sheet, vocal sheet) the church downloaded
under its own licence. Kept as the file, never as extracted text: see
app/songselect.py for why the text formats are read for details and dropped,
and this one is kept.

**Who can open one.** Staff and leaders, from the staff side. A volunteer only
while they are scheduled, and not declined, on a published service whose plan
uses the song. The licence covers the church's own musicians playing it, not
the whole congregation browsing a chart library, so the member side offers
charts through the plan and nowhere else.

Stored in Postgres, same reasons and same limits as TeamFile. The per-church
quota is shared between the two, see `stored_bytes`.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Optional

from sqlalchemy import ForeignKey, Index, Integer, LargeBinary, String, func
from sqlalchemy.orm import Mapped, deferred, mapped_column, relationship

from app.extensions import db
from app.models.base import TenantScoped, TimestampMixin, utcnow

# A plan stays open to its volunteers through the day of the service, so the
# chart does not vanish from the drummer's phone at 10:31.
GRACE_AFTER_START = timedelta(hours=12)


class SongChart(TenantScoped, TimestampMixin, db.Model):
    __tablename__ = "song_chart"
    __table_args__ = (
        Index("ix_song_chart_church_song", "church_id", "song_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    song_id: Mapped[int] = mapped_column(
        ForeignKey("song.id", ondelete="CASCADE"), nullable=False, index=True
    )
    song: Mapped["Song"] = relationship(back_populates="charts")  # noqa: F821

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Deferred: listing charts must not load every PDF.
    data: Mapped[bytes] = deferred(mapped_column(LargeBinary, nullable=False))

    uploaded_by_user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL")
    )
    uploaded_by_name: Mapped[Optional[str]] = mapped_column(String(120))

    def __repr__(self) -> str:
        return f"<SongChart {self.title!r} song={self.song_id}>"

    @property
    def size_label(self) -> str:
        if self.size_bytes >= 1_000_000:
            return f"{self.size_bytes / 1_000_000:.1f} MB"
        return f"{max(1, round(self.size_bytes / 1000))} KB"

    @classmethod
    def get_for_church(cls, church_id: int, chart_id: int) -> "SongChart | None":
        return db.session.scalar(
            db.select(cls).where(cls.id == chart_id, cls.church_id == church_id)
        )

    @classmethod
    def duplicate_of(cls, church_id: int, song_id: int, filename: str, size: int):
        """The same download dropped twice is one chart, not two."""
        return db.session.scalar(
            db.select(cls).where(
                cls.church_id == church_id,
                cls.song_id == song_id,
                cls.filename == filename,
                cls.size_bytes == size,
            )
        )

    @classmethod
    def bytes_used(cls, church_id: int) -> int:
        return int(db.session.scalar(
            db.select(func.coalesce(func.sum(cls.size_bytes), 0)).where(
                cls.church_id == church_id
            )
        ) or 0)

    @classmethod
    def person_may_open(cls, church_id: int, person_id: int, chart: "SongChart") -> bool:
        """Scheduled, not declined, on a published service that uses the song."""
        from app.models.service import (
            DECLINED, STATUS_PUBLISHED, Service, ServiceAssignment, ServiceItem,
        )

        found = db.session.scalar(
            db.select(ServiceAssignment.id)
            .join(Service, Service.id == ServiceAssignment.service_id)
            .join(ServiceItem, ServiceItem.service_id == Service.id)
            .where(
                ServiceAssignment.church_id == church_id,
                ServiceAssignment.person_id == person_id,
                ServiceAssignment.status != DECLINED,
                Service.status == STATUS_PUBLISHED,
                Service.starts_at >= utcnow() - GRACE_AFTER_START,
                ServiceItem.song_id == chart.song_id,
            )
            .limit(1)
        )
        return found is not None


def stored_bytes(church_id: int) -> int:
    """Everything a church keeps in the database as files. One quota."""
    from app.models.teamfile import TeamFile

    return TeamFile.bytes_used(church_id) + SongChart.bytes_used(church_id)
