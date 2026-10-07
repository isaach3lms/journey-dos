"""Telling the staff of a church that something needs them.

Four screens needed this and four screens grew their own copy of it: a
connect card, a pastoral request, a reported message, and somebody asking for
an account. Every copy had the same two branches, and three of the four got
the second branch wrong, which is why a pastoral request arrived in an inbox
and never on a phone.

The two branches are real and neither can go away:

**A named list is addresses, not accounts.** A care team is usually not a set
of logins in this system. The church types in three email addresses and those
people are told. There is no person to push to, so these are email only, and
that is a property of the address rather than a decision anybody made.

**No named list means every active staff account.** These are people with
records and phones, so they get the notification as well as the email, which
on a Sunday morning is the half that actually reaches anybody.

So: one function, both branches, and a caller that cannot pick the wrong one.

**What this never does is decide what to say.** The reason a pastoral request
reaches staff as "somebody asked for support, here is the link" and not as
the words they wrote is a decision that belongs to the pastoral screen, and it
stays there. This function moves whatever it is handed.
"""

from __future__ import annotations

from dataclasses import dataclass

from flask import current_app

from app.extensions import db


@dataclass(frozen=True)
class Told:
    """How many were reached, and by which route.

    `emailed` and `pushed` overlap: one staff member with a phone counts in
    both. `reached` is the number of humans, which is the number a flash
    message should say out loud.
    """

    reached: int = 0
    emailed: int = 0
    pushed: int = 0

    def __bool__(self) -> bool:
        return bool(self.reached)


def staff_users(church_id: int):
    """Every active staff account for this church.

    A leader is not included. The things this module announces, a reported
    message and a request for pastoral care among them, are staff work, and a
    volunteer running the check-in desk did not sign up for them.

    **A kiosk account is not a person.** It is the shared login on the iPad in
    the lobby, and it holds staff role because the check-in screens need it.
    Pushing a pastoral request to it puts somebody's worst week on a screen
    facing a room full of families, and emailing it sends church mail to an
    address several volunteers share. Two of the four hand-rolled copies of
    this query left this out, which is the second reason there is now one
    copy of it.
    """
    from app.models import User

    return db.session.scalars(
        db.select(User).where(
            User.church_id == church_id,
            User.role == "staff",
            User.is_active_account.is_(True),
            User.is_kiosk.is_(False),
        )
    ).all()


def tell_staff(
    church,
    *,
    category: str,
    subject: str,
    body_text: str,
    key: str,
    push_title: str | None = None,
    push_body: str | None = None,
    path: str = "/",
    tag: str | None = None,
    named=None,
) -> Told:
    """Tell this church's staff one thing, on every channel they have.

    `named` is the church's configured address list for this kind of alert,
    already parsed. Passing it is how a caller says "this church chose who
    hears about connect cards"; passing None or an empty list falls back to
    staff accounts.

    `key` is the stable part of the dedupe key, something like
    `"support:41"`. Each recipient's id is appended, so one alert cannot
    email one person twice and a retried request is harmless.

    `path` is where tapping the notification lands and must be a path on this
    app, not an absolute address: a push payload is delivered by a third party
    and is not the place to teach a phone to trust a hostname. The absolute
    link belongs in `body_text`, which the caller built.

    Never raises. Every caller is a person's submission or a volunteer's tap,
    and an error page that says the form failed, when the form worked and only
    the notification did not, is worse than a notification nobody got.
    """
    from app.mail import NotQueued, queue
    from app.notify import notify

    reached = emailed = pushed = 0

    if named:
        for address in named:
            if not address:
                continue
            try:
                queue(
                    church_id=church.id,
                    category=category,
                    subject=subject,
                    body_text=body_text,
                    to_email=address,
                    dedupe_key=f"{key}:{address}",
                )
            except NotQueued:
                continue
            except Exception:  # noqa: BLE001
                current_app.logger.exception("Staff alert failed for %s", address)
                continue
            reached += 1
            emailed += 1
        return Told(reached=reached, emailed=emailed, pushed=pushed)

    for user in staff_users(church.id):
        # A staff account with a roster record is a person, and a person can be
        # pushed to. This is the branch the hand-rolled copies kept missing.
        if user.person is not None:
            try:
                result = notify(
                    person=user.person,
                    church_id=church.id,
                    category=category,
                    subject=subject,
                    body_text=body_text,
                    push_title=push_title or subject,
                    push_body=push_body,
                    url=path,
                    tag=tag,
                    dedupe_key=f"{key}:user:{user.id}",
                )
            except Exception:  # noqa: BLE001
                current_app.logger.exception(
                    "Staff alert failed for user %s", user.id
                )
                continue
            pushed += result.pushed
            if result.emailed:
                emailed += 1
                reached += 1
                continue

            # No email went. Either the roster record has no address, or an
            # identical alert is already queued. Only the first is worth
            # recovering from, and the login's address is the place to
            # recover to: a staff account is created by email and linking it
            # to a roster record is a separate step, so a record imported
            # without an address is ordinary rather than exceptional.
            #
            # Safe to reuse the key. `queue` checks for an address before it
            # checks the dedupe key, so the failed attempt above consumed
            # nothing, and a retried request still finds this one and stops.
            record_address = (getattr(user.person, "email", None) or "").strip()
            if not record_address and user.email:
                try:
                    queue(
                        church_id=church.id,
                        category=category,
                        subject=subject,
                        body_text=body_text,
                        to_email=user.email,
                        to_name=user.name,
                        dedupe_key=f"{key}:user:{user.id}",
                    )
                except NotQueued:
                    pass
                except Exception:  # noqa: BLE001
                    current_app.logger.exception(
                        "Staff alert fallback failed for user %s", user.id
                    )
                else:
                    emailed += 1
                    reached += 1
                    continue

            # Push alone still counts as told.
            if result.pushed:
                reached += 1
            continue

        # No roster record, so nothing to push to and no preferences to read.
        # The login's own address is the only route left.
        if not user.email:
            continue
        try:
            queue(
                church_id=church.id,
                category=category,
                subject=subject,
                body_text=body_text,
                to_email=user.email,
                to_name=user.name,
                dedupe_key=f"{key}:user:{user.id}",
            )
        except NotQueued:
            continue
        except Exception:  # noqa: BLE001
            current_app.logger.exception("Staff alert failed for user %s", user.id)
            continue
        reached += 1
        emailed += 1

    return Told(reached=reached, emailed=emailed, pushed=pushed)
