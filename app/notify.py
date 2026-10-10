"""Telling somebody something, on every channel they have turned on.

This module exists because of a bug that was invisible for months: the push
machinery was complete, tested, and wired to nothing. `send_to_person` had no
callers. Every notification in the system went out by email only, and the
"app notifications" a member switched on in their preferences did nothing at
all.

The cause was structural rather than careless. Email went through
`app.mail.queue`, push went through `app.push.send_to_person`, and every send
site had to remember both. A send site that remembered one worked, looked
right in testing, and quietly delivered half of what it promised.

So there is now one way to notify a person, and it does both. A caller that
forgets push is a caller that does not exist.

Two things stay true whichever channel is used:

**One opt-out, not two.** Somebody who turned off "Groups and serving" turned
it off, not "the email part of it". Both channels run the same check, and
transactional categories ignore it in both for the same reason: a pickup code
that never arrives because somebody left the newsletter is worse than no
notification at all.

**Email is the one that has to work.** Push is best effort: most people never
grant it, a subscription expires silently, a phone is off. If push fails the
email still goes, and the caller is never told about it, because there is
nothing anybody can do with that fact. Email failing is a real problem and is
visible on the delivery screen in Settings.

**Neither channel sends here.** Both queue. Push used to be the exception,
sent inline, one HTTPS call per device with a ten second timeout, with the
person who pressed the button waiting through all of them. In a room of
twenty that is twenty round trips before the page comes back, and the visible
result was a church thread with the same message in it six times, because a
send button that looks untouched gets pressed again. The worker sends both
queues now. See `app.push.queue`.
"""

from __future__ import annotations

from dataclasses import dataclass

from flask import current_app

from app.mail import NotQueued, queue
from app.push.queue import enqueue


def _first_line(text: str | None) -> str:
    """A fallback notification body. The greeting line of an email is a poor
    notification, which is why callers should pass their own."""
    for line in (text or "").splitlines():
        if line.strip():
            return line.strip()
    return ""


@dataclass(frozen=True)
class Delivered:
    """What went out. Mostly for tests and the staff screens.

    `pushed` counts **people**, so it is 1 or 0. It used to count devices,
    because push was sent here and the transport came back with a number. Now
    that it is queued there is no number to come back with until the worker
    runs, and one per person is the honest answer at this point: a
    notification is on its way to this person, on whatever they carry.

    The screens that add these up were already reporting "how many people
    were told", which is what they now get. Before, a staff member with a
    phone and a laptop counted twice.
    """

    emailed: bool
    pushed: int
    suppressed: bool = False

    def __bool__(self) -> bool:
        return self.emailed or bool(self.pushed)


def notify(
    *,
    person,
    church_id: int,
    category: str,
    subject: str,
    body_text: str,
    push_title: str | None = None,
    push_body: str | None = None,
    url: str = "/",
    tag: str | None = None,
    dedupe_key: str | None = None,
    actor=None,
    push: bool = True,
    email: bool = True,
) -> Delivered:
    """Email and push one person about one thing.

    `subject` and `body_text` are the email. `push_title` and `push_body` are
    the notification, and they are separate arguments on purpose: an email
    subject reads as a subject line and makes a poor notification, and a
    notification body has to say the whole thing in about a hundred
    characters. Leaving them out falls back to the email's wording, which is
    better than nothing but worse than writing both.

    `url` is where tapping the notification lands. It must be a path on this
    app, not an absolute address: a push payload is delivered by a third party
    and is not the place to teach a browser to trust a hostname.

    `push=False` sends the email on its own. It defaults to True because the
    whole reason this function exists is that callers forgot push; a caller
    that wants email only has to say so, and the only one that does is a
    church-wide email long enough to make a poor notification.

    `email=False` is the mirror of it, and has exactly one caller: posting a
    church-wide announcement, where whether a second copy lands in three
    hundred inboxes is a tick box the person posting chose. Without this
    argument that screen had to reach past this function to the push
    machinery directly, which is the shape of the bug this module exists to
    make impossible. Both channels still run the same opt-out check, so
    `email=False` is "do not also mail this", never "ignore what they chose".
    """
    emailed = False
    suppressed = False

    if email:
        try:
            message = queue(
                church_id=church_id,
                category=category,
                subject=subject,
                body_text=body_text,
                person=person,
                dedupe_key=dedupe_key,
                actor=actor,
            )
            emailed = message is not None
        except NotQueued as exc:
            # Opted out, or no address. Neither is an error here: the caller
            # asked for this person to be told, and the system's answer is
            # that they have said not to, or cannot be reached.
            suppressed = "opted out" in str(exc)

    pushed = 0
    if not push:
        return Delivered(emailed=emailed, pushed=0, suppressed=suppressed)

    try:
        item = enqueue(
            person=person,
            category=category,
            title=(push_title or subject)[:80],
            body=push_body or _first_line(body_text),
            url=url,
            tag=tag,
            # The email's key would collide with it, and the two channels
            # queue separately: somebody can be emailed and not pushable.
            dedupe_key=f"push:{dedupe_key}" if dedupe_key else None,
        )
        pushed = 1 if item is not None else 0
    except Exception:  # noqa: BLE001
        # Best effort, and deliberately swallowed. The push machinery having
        # a bad afternoon must never be the reason a volunteer does not get
        # the email telling them they are on the plan on Sunday.
        current_app.logger.exception(
            "push could not be queued for person %s", getattr(person, "id", None)
        )

    return Delivered(emailed=emailed, pushed=pushed, suppressed=suppressed)
