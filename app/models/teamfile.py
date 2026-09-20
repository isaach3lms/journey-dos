"""Files a team keeps for itself: kids curriculum, a chord chart, a rota.

Stored in the database rather than on disk. Render wipes a service's disk on
every deploy, so a file written there disappears the next time we ship, which
is the worst kind of data loss: silent, and discovered by a volunteer on a
Sunday. Postgres keeps them through deploys and is included in the backups the
church already has.

That choice has a ceiling. Files are capped per file and per church (see
app/files.py) so the database stays a database. When a church outgrows it, the
files move to object storage and only this model changes.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import ForeignKey, Index, Integer, LargeBinary, String
from sqlalchemy.orm import Mapped, deferred, mapped_column, relationship
from sqlalchemy import func

from app.extensions import db
from app.models.base import TenantScoped, TimestampMixin


class TeamFile(TenantScoped, TimestampMixin, db.Model):
    __tablename__ = "team_file"
    __table_args__ = (
        Index("ix_team_file_church_team", "church_id", "team_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    team_id: Mapped[int] = mapped_column(
        ForeignKey("team.id", ondelete="CASCADE"), nullable=False, index=True
    )
    team: Mapped["Team"] = relationship(back_populates="files")  # noqa: F821

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False, default="application/pdf")
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Deferred: every list of files would otherwise drag every PDF into
    # memory to print its name.
    data: Mapped[bytes] = deferred(mapped_column(LargeBinary, nullable=False))

    uploaded_by_user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL")
    )
    uploaded_by_name: Mapped[Optional[str]] = mapped_column(String(120))

    def __repr__(self) -> str:
        return f"<TeamFile {self.title!r} team={self.team_id}>"

    @property
    def size_label(self) -> str:
        if self.size_bytes >= 1_000_000:
            return f"{self.size_bytes / 1_000_000:.1f} MB"
        return f"{max(1, round(self.size_bytes / 1000))} KB"

    @classmethod
    def for_team(cls, church_id: int, team_id: int):
        return (
            db.select(cls)
            .where(cls.church_id == church_id, cls.team_id == team_id)
            .order_by(cls.created_at.desc())
        )

    @classmethod
    def get_for_church(cls, church_id: int, file_id: int) -> "TeamFile | None":
        return db.session.scalar(
            db.select(cls).where(cls.id == file_id, cls.church_id == church_id)
        )

    @classmethod
    def for_person(cls, church_id: int, person_id: int):
        """Every file belonging to a team this person serves on."""
        from app.models.service import Team, TeamMembership

        return (
            db.select(cls)
            .join(Team, Team.id == cls.team_id)
            .join(TeamMembership, TeamMembership.team_id == Team.id)
            .where(
                cls.church_id == church_id,
                TeamMembership.person_id == person_id,
                Team.is_active.is_(True),
            )
            .order_by(Team.name, cls.created_at.desc())
        )

    @classmethod
    def bytes_used(cls, church_id: int) -> int:
        return int(db.session.scalar(
            db.select(func.coalesce(func.sum(cls.size_bytes), 0)).where(
                cls.church_id == church_id
            )
        ) or 0)
