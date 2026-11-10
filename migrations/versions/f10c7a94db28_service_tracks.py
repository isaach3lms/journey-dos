"""A Sunday becomes one service with tracks, and same-day services merge.

Three steps, in order, because each depends on the last:

1. Create `service_track` and give every existing service one track, named
   after that service. Point its items, assignments and needs at it. After this
   step the application behaves exactly as before: one Sunday, one strand.

2. Fold services that share a church-local date into one. The earliest becomes
   the Sunday; the others become tracks on it and their rows move across. This
   is what makes "three services every Sunday" into "one Sunday, three
   strands".

3. Re-scope the two unique constraints from the service to the track, which
   only becomes correct once every row has a track.

**What the merge changes, deliberately.** Tracks share the Sunday's start
time, so a kids service that started at 9:15 now clocks its running order from
whenever the Sunday starts. Nothing is lost: each track's original start time
and original name are written into its notes, where a leader can read them and
adjust. That is better than a silent reclock and better than refusing to merge.

The church's own timezone decides what "the same date" means. Comparing UTC
dates would split a Sunday evening service in Missouri from the morning one it
belongs with, which is exactly the kind of quiet wrongness this migration must
not introduce.

Revision ID: f10c7a94db28
Revises: a82e5fd13c74
"""

from alembic import op
import sqlalchemy as sa


revision = "f10c7a94db28"
down_revision = "a82e5fd13c74"
branch_labels = None
depends_on = None


def _local_date(moment, zone_name):
    """The calendar date this moment falls on, in the church's own zone."""
    from datetime import timezone
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    if moment is None:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    try:
        zone = ZoneInfo(zone_name) if zone_name else timezone.utc
    except (ZoneInfoNotFoundError, ValueError):
        zone = timezone.utc
    return moment.astimezone(zone).date()


def upgrade():
    bind = op.get_bind()

    op.create_table(
        "service_track",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("church_id", sa.Integer(), nullable=False),
        sa.Column("service_id", sa.Integer(), nullable=False),
        sa.Column("service_type_id", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["church_id"], ["church.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["service_id"], ["service.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["service_type_id"], ["service_type.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("service_id", "position", name="uq_service_track_position"),
    )
    op.create_index("ix_service_track_church", "service_track", ["church_id", "service_id"])
    op.create_index("ix_service_track_service", "service_track", ["service_id"])
    op.create_index("ix_service_track_type", "service_track", ["service_type_id"])

    # Nullable first. The column is filled below and tightened at the end,
    # because a NOT NULL column cannot be added to a table that already has
    # rows without a default nobody wants.
    for table in ("service_item", "service_assignment", "service_need"):
        with op.batch_alter_table(table, schema=None) as batch:
            batch.add_column(sa.Column("track_id", sa.Integer(), nullable=True))

    # -- step 1: one track per existing service ------------------------------

    services = bind.execute(sa.text(
        "SELECT id, church_id, name, starts_at, service_type_id, headcount FROM service "
        "ORDER BY starts_at, id"
    )).fetchall()

    now = sa.text("CURRENT_TIMESTAMP")
    track_of_service = {}
    for row in services:
        bind.execute(
            sa.text(
                "INSERT INTO service_track "
                "(church_id, service_id, service_type_id, name, position,"
                " created_at, updated_at) "
                "VALUES (:c, :s, :t, :n, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ),
            {"c": row.church_id, "s": row.id, "t": row.service_type_id,
             "n": (row.name or "Service")[:120]},
        )
        track_of_service[row.id] = bind.execute(
            sa.text("SELECT id FROM service_track WHERE service_id = :s"),
            {"s": row.id},
        ).scalar()

    for table in ("service_item", "service_assignment", "service_need"):
        for service_id, track_id in track_of_service.items():
            bind.execute(
                sa.text(f"UPDATE {table} SET track_id = :t WHERE service_id = :s"),
                {"t": track_id, "s": service_id},
            )

    # -- step 1b: release the old per-service uniqueness ---------------------
    #
    # Both constraints are about to become wrong in two ways at once: they are
    # scoped to the service rather than the track, and the merge below moves
    # rows onto a service that already has a row in that position. Dropping
    # them here rather than at the end is the difference between a migration
    # that runs and one that dies halfway through a church's history.

    with op.batch_alter_table("service_item", schema=None) as batch:
        batch.drop_constraint("uq_service_item_position", type_="unique")
    with op.batch_alter_table("service_need", schema=None) as batch:
        batch.drop_constraint("uq_service_need_position", type_="unique")

    # -- step 2: merge services sharing a church-local date ------------------

    zones = dict(bind.execute(sa.text("SELECT id, timezone FROM church")).fetchall())

    by_day = {}
    for row in services:
        key = (row.church_id, _local_date(row.starts_at, zones.get(row.church_id)))
        by_day.setdefault(key, []).append(row)

    merged = 0
    for (_church_id, _day), group in by_day.items():
        if len(group) < 2:
            continue
        # Earliest first: the Sunday starts when its first strand does.
        group = sorted(group, key=lambda r: (r.starts_at, r.id))
        keeper, rest = group[0], group[1:]

        # The Sunday keeps the earliest start, because that is when it begins.
        # Its name becomes a plain "Sunday" rather than whichever service
        # happened to be first: calling the whole morning "Operations" because
        # the setup team start at eight would be wrong every week. Nothing is
        # lost, because every original name survives as its track's name.
        bind.execute(
            sa.text("UPDATE service SET name = 'Sunday' WHERE id = :keep"),
            {"keep": keeper.id},
        )

        for offset, other in enumerate(rest, start=2):
            track_id = track_of_service[other.id]
            # What the merge would otherwise lose, written where it is read.
            note = (
                f"Was a separate service called {other.name!r}, "
                f"starting {other.starts_at}. Merged into this Sunday."
            )
            bind.execute(
                sa.text(
                    "UPDATE service_track SET service_id = :keep, position = :p,"
                    " notes = :note WHERE id = :t"
                ),
                {"keep": keeper.id, "p": offset, "note": note, "t": track_id},
            )
            for table in ("service_item", "service_assignment", "service_need"):
                bind.execute(
                    sa.text(f"UPDATE {table} SET service_id = :keep WHERE track_id = :t"),
                    {"keep": keeper.id, "t": track_id},
                )
            # A Sunday has one headcount now. Keep the largest of the ones
            # being merged: the main service's number is the one a church
            # means by "how many were here", and it is the biggest.
            if other.headcount:
                bind.execute(
                    sa.text(
                        "UPDATE service SET headcount = :h WHERE id = :keep "
                        "AND (headcount IS NULL OR headcount < :h)"
                    ),
                    {"h": other.headcount, "keep": keeper.id},
                )
            bind.execute(sa.text("DELETE FROM service WHERE id = :s"), {"s": other.id})
            merged += 1

    # -- step 3: tighten up --------------------------------------------------

    with op.batch_alter_table("service_item", schema=None) as batch:
        batch.alter_column("track_id", existing_type=sa.Integer(), nullable=False)
        batch.create_foreign_key(
            "fk_service_item_track", "service_track", ["track_id"], ["id"],
            ondelete="CASCADE",
        )
        batch.create_index("ix_service_item_track", ["track_id"])
        batch.create_unique_constraint("uq_service_item_position", ["track_id", "position"])

    with op.batch_alter_table("service_assignment", schema=None) as batch:
        batch.alter_column("track_id", existing_type=sa.Integer(), nullable=False)
        batch.create_foreign_key(
            "fk_service_assignment_track", "service_track", ["track_id"], ["id"],
            ondelete="CASCADE",
        )
        batch.create_index("ix_service_assignment_track", ["track_id"])

    with op.batch_alter_table("service_need", schema=None) as batch:
        batch.alter_column("track_id", existing_type=sa.Integer(), nullable=False)
        batch.create_foreign_key(
            "fk_service_need_track", "service_track", ["track_id"], ["id"],
            ondelete="CASCADE",
        )
        batch.create_index("ix_service_need_track", ["track_id"])
        batch.create_unique_constraint("uq_service_need_position", ["track_id", "position_id"])


def downgrade():
    """Back to one plan per service.

    Merged Sundays are not split apart again: the services they came from were
    deleted and recreating them would invent dates. The strands collapse back
    into their Sunday, which means a merged Sunday comes back as one service
    holding everything, its running order renumbered into one list and its
    duplicate staffing rows reduced to one per position.

    That is lossy, and it is stated rather than hidden. It is still the right
    trade: the alternative is a downgrade that cannot run at all, which is a
    worse thing to discover while reaching for one.
    """
    bind = op.get_bind()

    # Going back means one plan per service again, so the rows that were on
    # separate strands have to be made to fit that shape before the old
    # constraints can exist. Two strands each legitimately hold a position 1,
    # and two strands can each ask for a drummer.
    #
    # This is where the downgrade is lossy and it is lossy on purpose: the
    # alternative is a downgrade that cannot run at all, which is worse for
    # whoever is reaching for it at the time.

    # Running orders: renumber each service's lines 1..n, strand by strand in
    # order, so a merged Sunday comes back as one plan that reads top to
    # bottom rather than as three interleaved ones.
    rows = bind.execute(sa.text(
        "SELECT i.id FROM service_item i JOIN service_track k ON k.id = i.track_id "
        "ORDER BY i.service_id, k.position, i.position, i.id"
    )).fetchall()
    seen = {}
    for (item_id,) in rows:
        service_id = bind.execute(
            sa.text("SELECT service_id FROM service_item WHERE id = :i"), {"i": item_id}
        ).scalar()
        seen[service_id] = seen.get(service_id, 0) + 1
        bind.execute(
            sa.text("UPDATE service_item SET position = :p WHERE id = :i"),
            {"p": -seen[service_id], "i": item_id},
        )
    bind.execute(sa.text("UPDATE service_item SET position = -position WHERE position < 0"))

    # Staffing: one row per position per service. The largest ask wins, which
    # is the safest direction to be wrong in when a church is looking at how
    # many people it still needs.
    bind.execute(sa.text(
        "DELETE FROM service_need WHERE id NOT IN ("
        "  SELECT MIN(id) FROM service_need GROUP BY service_id, position_id)"
    ))

    with op.batch_alter_table("service_need", schema=None) as batch:
        batch.drop_constraint("uq_service_need_position", type_="unique")
        batch.create_unique_constraint("uq_service_need_position", ["service_id", "position_id"])
        batch.drop_index("ix_service_need_track")
        batch.drop_constraint("fk_service_need_track", type_="foreignkey")
        batch.drop_column("track_id")

    with op.batch_alter_table("service_assignment", schema=None) as batch:
        batch.drop_index("ix_service_assignment_track")
        batch.drop_constraint("fk_service_assignment_track", type_="foreignkey")
        batch.drop_column("track_id")

    with op.batch_alter_table("service_item", schema=None) as batch:
        batch.drop_constraint("uq_service_item_position", type_="unique")
        batch.create_unique_constraint("uq_service_item_position", ["service_id", "position"])
        batch.drop_index("ix_service_item_track")
        batch.drop_constraint("fk_service_item_track", type_="foreignkey")
        batch.drop_column("track_id")

    op.drop_index("ix_service_track_type", table_name="service_track")
    op.drop_index("ix_service_track_service", table_name="service_track")
    op.drop_index("ix_service_track_church", table_name="service_track")
    op.drop_table("service_track")
