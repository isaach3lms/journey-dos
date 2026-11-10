"""Keeping the "what sends a notification" screen honest.

A hand-kept list of what the system does drifts from reality within a month
and then actively misleads, which is worse than having no list: staff stop
reporting gaps because the page says the gap cannot exist.

So the catalogue is checked against the code it describes. Every category it
names has to exist. Every category a member can switch off has to appear on
it, or the preferences screen offers a toggle for something this page says
nothing about. And anything claiming to push has to be backed by a send site
that actually passes push wording, which is the specific failure this whole
change set was about.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.categories import CATEGORIES, CATEGORY_BY_CODE
from app.notify_catalogue import (
    BOTH,
    CHANNEL_LABELS,

    GROUPS,
    PUSH,
    categories_used,
    every_event,
)

APP = Path(__file__).resolve().parent.parent / "app"


class TestItDescribesCategoriesThatExist:
    @pytest.mark.parametrize("category", sorted(categories_used()))
    def test_every_category_named_is_real(self, category):
        """A typo here is a page confidently describing something that cannot
        happen, and a renamed category that nobody updated reads the same."""
        assert category in CATEGORY_BY_CODE, (
            f"{category!r} is on the notification catalogue but is not a "
            "category. Either the code name changed or the catalogue has a "
            "typo in it."
        )

    def test_every_optional_category_appears_at_least_once(self):
        """If a member can switch it off, this page has to say what it is.

        Otherwise the preferences screen offers a toggle for something the
        catalogue is silent about, and somebody turns off a thing they then
        cannot find an explanation for.
        """
        optional = {c.code for c in CATEGORIES if not c.is_transactional}
        missing = optional - categories_used()
        # `digest` has no sender yet. Listing it would describe something the
        # system does not do, which is the exact failure this file prevents.
        missing -= {"digest"}
        assert not missing, (
            f"These are switchable in a member's preferences but appear "
            f"nowhere on the catalogue: {sorted(missing)}."
        )

    def test_every_transactional_category_with_a_sender_appears(self):
        """The always-send ones are the ones somebody is waiting on."""
        for code in ("account", "kids_checkin", "pastoral", "moderation",
                     "guest_card"):
            assert code in categories_used(), (
                f"{code!r} always sends and is not described anywhere."
            )


class TestItIsWellFormed:
    def test_every_channel_value_is_one_we_render(self):
        for event in every_event():
            assert event.channels in CHANNEL_LABELS, (
                f"{event.what!r} has channels {event.channels!r}, which the "
                "template cannot label."
            )

    def test_nothing_is_listed_twice(self):
        whats = [event.what for event in every_event()]
        assert len(whats) == len(set(whats))

    def test_every_group_has_something_in_it(self):
        for group in GROUPS:
            assert group.events, f"{group.title!r} is an empty heading."

    def test_pushes_agrees_with_channels(self):
        for event in every_event():
            assert event.pushes == (event.channels in (PUSH, BOTH))

    def test_the_notes_are_sentences_not_fragments(self):
        """This page is read by somebody deciding whether to file a bug."""
        for event in every_event():
            if event.note:
                assert event.note.endswith("."), event.what
                assert event.note[0].isupper(), event.what


class TestItMatchesWhatTheCodeDoes:
    """The part that cannot be faked by editing the catalogue."""

    def _modules_calling(self, name: str) -> set:
        found = set()
        for path in APP.rglob("*.py"):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Name)
                        and node.func.id == name):
                    found.add(path.relative_to(APP.parent).as_posix())
        return found

    def test_something_in_the_code_actually_pushes(self):
        """A catalogue of fifteen pushing events and no `notify` call anywhere
        would pass every test above."""
        assert self._modules_calling("notify"), (
            "the catalogue claims notifications are sent and nothing calls "
            "app.notify.notify"
        )

    def test_the_four_paths_that_were_broken_each_have_a_sender(self):
        """Named individually because these are the four that shipped email
        only, and a refactor that quietly dropped one of them should fail
        here rather than in somebody's week."""
        for module in ("app/alerts.py", "app/serving_notify.py",
                       "app/kids_notify.py", "app/chat_notify.py"):
            assert (APP.parent / module).exists(), f"{module} is gone"
            assert "notify(" in (APP.parent / module).read_text(), (
                f"{module} no longer sends anything"
            )

    def test_every_pushing_event_has_push_wording_behind_it(self):
        """A send site that passes no `push_title` falls back to the email
        subject, which reads as a subject line on a lock screen. That is not
        broken, but a catalogue row claiming a notification should mean
        somebody wrote one.
        """
        from app.content import KIDS, MEMBER, MESSAGES, SERVICES

        wording = {
            "pastoral": MEMBER.get("support_push_body"),
            "moderation": MESSAGES.get("alert_push_body"),
            "kids_checkin": KIDS.get("notify_in_push"),
            "group": SERVICES.get("invite_push_body"),
        }
        for category, text in wording.items():
            assert text, (
                f"the catalogue says {category} sends a notification and "
                "there is no notification wording for it"
            )


class TestTheScreen:
    def test_staff_can_see_it(self, app, db, staff):
        page = staff.get("/settings/?open=sends",
                         headers={"Host": "journey.dos.test"})
        assert page.status_code == 200
        body = page.get_data(as_text=True)
        assert "What sends a notification" in body

    def test_it_lists_the_things_that_were_missing(self, app, db, staff):
        """The four he reported, on one screen, each saying both channels."""
        page = staff.get("/settings/?open=sends",
                         headers={"Host": "journey.dos.test"})
        body = page.get_data(as_text=True)

        for row in ("pastoral support form",
                    "put on a published plan",
                    "checked in",
                    "posts in a room"):
            assert row in body, f"{row!r} is not on the screen"

    def test_a_leader_cannot_see_settings_at_all(self, app, db, leader):
        page = leader.get("/settings/?open=sends",
                          headers={"Host": "journey.dos.test"})
        assert page.status_code in (302, 403)
