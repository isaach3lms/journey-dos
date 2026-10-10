"""The `church` table. Adding a church is a row, not a migration.

Everything that makes one tenant look and behave differently from another
lives in these columns. There is no per-tenant code, no per-tenant template,
and no per-tenant deploy.
"""

from __future__ import annotations

# SQLAlchemy evaluates the annotation inside `Mapped[...]` at class-definition
# time, so `from __future__ import annotations` does not defer it the way it
# defers ordinary function annotations. `str | None` therefore needs a Python
# that can evaluate PEP 604 unions at runtime, which means 3.10 or newer.
# `Optional[...]` resolves on every version, so the models do not depend on
# which interpreter happens to be on the machine.
from typing import Optional

import re

from sqlalchemy import (
    Boolean,
    Float,
    String,
    Text,
    UniqueConstraint,
    false,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.extensions import db
from app.models.base import TimestampMixin

SLUG_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,48}[a-z0-9])?$")


# Where a request for a login goes when a church has not named an inbox.
#
# A constant rather than a column default, so changing it is a deploy and not
# a migration plus a backfill of every church row.
DEFAULT_ACCOUNT_REQUEST_EMAIL = "isaac@betweensundaysconsulting.com"


def _addresses(raw: str | None) -> list[str]:
    """A typed list of addresses, cleaned. One parser for every such setting.

    Commas and newlines both separate, because a person given a box types
    whichever they think of first. Anything without an `@` is dropped rather
    than queued and bounced, and duplicates are collapsed so one inbox does
    not get two copies of the same alert.
    """
    text = (raw or "").replace(",", "\n")
    seen, out = set(), []
    for line in text.splitlines():
        address = line.strip().lower()
        if not address or "@" not in address or address in seen:
            continue
        seen.add(address)
        out.append(address)
    return out


class Church(TimestampMixin, db.Model):
    __tablename__ = "church"
    __table_args__ = (
        UniqueConstraint("slug", name="uq_church_slug"),
        UniqueConstraint("custom_domain", name="uq_church_custom_domain"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    # Identity
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    slug: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    city: Mapped[Optional[str]] = mapped_column(String(80))

    # Routing. `slug` resolves the platform subdomain. `custom_domain` is the
    # optional vanity host a church points at us later.
    custom_domain: Mapped[Optional[str]] = mapped_column(String(255), index=True)

    # Branding. These two columns are the entire theming surface. See
    # app/brand.py, which is the sole lever. Templates carry no colors.
    palette_key: Mapped[str] = mapped_column(
        String(40), nullable=False, default="between-sundays"
    )
    accent_hex: Mapped[Optional[str]] = mapped_column(String(7))
    logo_reversed_path: Mapped[Optional[str]] = mapped_column(String(255))

    # App store presentation, shown on the Settings screen.
    app_name: Mapped[Optional[str]] = mapped_column(String(120))
    app_domain: Mapped[Optional[str]] = mapped_column(String(255))

    # Giving. This system never touches a card number: it links out to the
    # platform the church already uses. See app/giving.py and spec v3 A.3.
    giving_provider: Mapped[Optional[str]] = mapped_column(String(30))
    giving_admin_url: Mapped[Optional[str]] = mapped_column(String(500))
    giving_form_url: Mapped[Optional[str]] = mapped_column(String(500))

    # Wall-clock time for meetings. See app/timeutil.py. A wrong value here
    # is silent, so onboarding sets it rather than the system guessing.
    timezone: Mapped[Optional[str]] = mapped_column(String(64))

    # Whether anyone can create their own account.
    #
    # Default off, and the default is the decision. A church that has not
    # thought about it yet should not discover that strangers can read its
    # announcements. Turning it on is one toggle in Settings, made
    # deliberately by somebody who understands what it opens.
    allow_self_signup: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )

    # Whether members, not only staff, can post church-wide announcements.
    # On by the church's choice. Only members staff have approved can post,
    # the word filter applies, every post can be reported, and staff can
    # delete any of it. Emailing an announcement stays staff only, because an
    # inbox is harder to take back than a screen.
    members_can_announce: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )

    # The church's CCLI Church Copyright License number. Shown on the songs
    # page and the plan so whoever builds the slides has it for the lyric
    # footer. Stored as text: it is an identifier, not a quantity.
    ccli_license_number: Mapped[Optional[str]] = mapped_column(String(20))

    # Whether kids check-in and pickup use the last four digits of a parent's
    # phone number instead of generated codes.
    #
    # Default off, and the default is the decision. Turning it on gives a
    # family one number to remember and gives up two things: the pickup code
    # stops expiring, and it becomes something a stranger could know. A church
    # that has not weighed that should not inherit it. See app/phonecode.py
    # for the full trade and app/pickup.py for what it replaces.
    #
    # The generated codes keep being issued and stored either way, so turning
    # this off puts the old behaviour back with nothing lost.
    phone_checkin: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )

    # Which label is in the check-in printer.
    #
    # A code from `app/labels.py`, not a measurement. A church that types 62
    # when the roll is 29 prints nothing and has no way to tell why; the code
    # is on the box the roll came in. Null means the default, so a church that
    # never opens this setting still prints.
    kids_label_size: Mapped[Optional[str]] = mapped_column(String(20))

    # Moving the whole tag, in millimetres, for a printer that prints
    # off-centre.
    #
    # A Brother QL does not centre what it prints on the page it is handed:
    # the head is narrower than the roll and sits to one side of it, and how
    # far depends on the machine and on how the roll is sitting. The tag
    # layout leaves enough clear space to absorb the usual amount, which is
    # the fix for most churches. This is for the one whose printer is worse
    # than usual, and it beats the alternative, which is a church printing
    # half a name every Sunday with nothing they can do about it.
    #
    # Integers in tenths of a millimetre would be false precision. A float in
    # millimetres is what somebody reads off the alignment test label.
    kids_tag_nudge_x: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0, server_default="0"
    )
    kids_tag_nudge_y: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0, server_default="0"
    )

    # Past this the tag is off the label whatever the printer does, and the
    # person typing it has misread the test print.
    NUDGE_LIMIT_MM = 15.0

    @property
    def label_size(self):
        from app.labels import size_for

        return size_for(self.kids_label_size)

    @property
    def tag_nudge(self) -> tuple:
        """(right, up) in millimetres. Clamped, so a bad value in the
        database cannot produce a blank label nobody can explain."""
        limit = self.NUDGE_LIMIT_MM
        return (
            max(-limit, min(limit, self.kids_tag_nudge_x or 0.0)),
            max(-limit, min(limit, self.kids_tag_nudge_y or 0.0)),
        )

    # Who hears about a pastoral request.
    #
    # Empty means every active staff account, which is the right default for a
    # church that has not thought about it: the people who can already see the
    # request are the people told it exists. Setting it names the specific
    # inboxes instead, which is what a church with a care team wants, and lets
    # somebody outside the staff list be on it.
    #
    # One address per line. Stored as text rather than a table because it is a
    # short list a human edits, and a table would mean a screen to manage rows
    # for something that reads better as a box.
    pastoral_alert_emails: Mapped[Optional[str]] = mapped_column(Text)

    @property
    def pastoral_recipients(self) -> list[str]:
        """The addresses, cleaned. Empty list means fall back to staff."""
        return _addresses(self.pastoral_alert_emails)

    # Who hears that a connect card came in. Same shape and same fallback as
    # the pastoral list above: empty means every active staff account.
    guest_alert_emails: Mapped[Optional[str]] = mapped_column(Text)

    @property
    def guest_recipients(self) -> list[str]:
        return _addresses(self.guest_alert_emails)

    # Where a guest's request for a login goes.
    #
    # Not the church's own staff by default, because creating an account is
    # not a thing most church staff do: it is platform administration, and on
    # every deployment so far that has been whoever set the church up. A
    # church that wants to handle its own puts its address here.
    account_request_email: Mapped[Optional[str]] = mapped_column(String(255))

    @property
    def account_request_recipients(self) -> list[str]:
        """Where to send an account request. Falls back to the platform."""
        named = _addresses(self.account_request_email)
        return named or [DEFAULT_ACCOUNT_REQUEST_EMAIL]

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    def __repr__(self) -> str:
        return f"<Church {self.slug} {self.name!r}>"

    # -- validation ---------------------------------------------------------

    @staticmethod
    def validate_slug(slug: str) -> str:
        """A slug becomes a hostname label, so it has to be a legal one."""
        slug = (slug or "").strip().lower()
        if not SLUG_RE.match(slug):
            raise ValueError(
                f"{slug!r} is not a valid subdomain label. Use lowercase "
                f"letters, digits, and hyphens, starting and ending with a "
                f"letter or digit."
            )
        return slug

    # -- lookups ------------------------------------------------------------

    @classmethod
    def by_slug(cls, slug: str) -> "Church | None":
        if not slug:
            return None
        return db.session.scalar(
            db.select(cls).where(cls.slug == slug.lower(), cls.is_active.is_(True))
        )

    @classmethod
    def by_custom_domain(cls, host: str) -> "Church | None":
        if not host:
            return None
        return db.session.scalar(
            db.select(cls).where(
                cls.custom_domain == host.lower(), cls.is_active.is_(True)
            )
        )

    @property
    def display_app_name(self) -> str:
        return self.app_name or self.name

    @property
    def display_app_domain(self) -> str:
        return self.app_domain or self.custom_domain or f"{self.slug}.example.org"


    @property
    def giving_is_configured(self) -> bool:
        return bool(self.giving_form_url or self.giving_admin_url)

    @property
    def giving_provider_label(self) -> str:
        from app.giving import PROVIDER_TITHELY, provider_label

        return provider_label(self.giving_provider or PROVIDER_TITHELY)


    @staticmethod
    def normalize_host(raw: str | None) -> str:
        """A bare hostname, or "".

        People paste what is in their address bar, which includes a scheme and
        often a trailing slash. Refusing that would be correct and useless; it
        is a hostname either way.
        """
        if not raw:
            return ""
        host = raw.strip().lower()
        for prefix in ("https://", "http://"):
            if host.startswith(prefix):
                host = host[len(prefix):]
        host = host.split("/", 1)[0].split("?", 1)[0].strip().rstrip(".")
        return host

    @staticmethod
    def host_looks_valid(host: str) -> bool:
        import re

        if not host or len(host) > 253 or "." not in host:
            return False
        return bool(re.fullmatch(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+", host))
