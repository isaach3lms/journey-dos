"""The weekly prayer check-in.

A tile on the member home screen, a one-question form behind it, and a list
staff pray through on a Tuesday morning.

Most of this file is about four decisions rather than about mechanics, because
the mechanics are a row and a form and the decisions are what will be wrong in
six months if nobody holds them in place:

**The week is the key, so it resets by itself.** There is no expiry job and no
nightly task. Next Sunday is a different key and last week's row simply is not
this week's. The tests that matter here are the ones that would catch somebody
"fixing" this with a timestamp comparison, which would work in UTC and be a
day out for a church in Missouri.

**Within a week it is edited, not stacked.** Somebody who writes on Sunday and
remembers more on Tuesday checked in once. Two rows would turn "people who
checked in" into "times a form was submitted".

**An ordinary check-in notifies nobody.** Sixty members on a Sunday is sixty
notifications, and a channel that fires that often gets turned off, taking the
pastoral alerts with it. The one thing that does reach a pastor is a member
ticking the box asking to be contacted.

**What somebody writes stays on a screen behind a login.** Never an email,
never a push body, never their own timeline. Same rule as a pastoral request,
and here it is asserted against the actual queued rows rather than trusted.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from app.models import (
    Church,
    OutboxMessage,
    Person,
    PersonEvent,
    PrayerCheckin,
    PushQueueItem,
    SupportRequest,
    User,
)
from app.models.base import utcnow
from app.models.support import KIND_PRAYER
from app.models.verse import week_of
from app.next_steps import PRAYER_CODE, PRAYER_TILE, Tile, tiles_for, url_for_tile
from tests.conftest import JOURNEY_HOST, PASSWORD, RIVERBEND_HOST

H = {"Host": JOURNEY_HOST}


@pytest.fixture
def journey(db):
    return db.session.scalar(db.select(Church).where(Church.slug == "journey"))


@pytest.fixture
def linked(db, journey):
    """A member with a login, which is what the member app requires."""
    person = Person(church_id=journey.id, first_name="Alicia", last_name="Romero",
                    stage="member", email="member@journeychurchsemo.com",
                    approved_at=utcnow())
    db.session.add(person)
    db.session.flush()
    user = db.session.scalar(
        db.select(User).where(
            User.church_id == journey.id,
            User.email == "member@journeychurchsemo.com",
        )
    )
    user.person_id = person.id
    db.session.commit()
    return person


@pytest.fixture
def member(client, sign_in, linked):
    sign_in("member@journeychurchsemo.com")
    return client


def checkins(db, journey):
    return db.session.scalars(
        db.select(PrayerCheckin)
        .where(PrayerCheckin.church_id == journey.id)
        .order_by(PrayerCheckin.id)
    ).all()


def a_checkin(db, journey, person, body="Pray for my mother.", when=None, **kwargs):
    row = PrayerCheckin(
        church_id=journey.id,
        person_id=person.id,
        week_of=when or week_of(date.today()),
        body=body,
        **kwargs,
    )
    db.session.add(row)
    db.session.commit()
    return row


def send(client, body="Pray for my mother.", **extra):
    data = {"body": body}
    data.update(extra)
    return client.post("/me/prayer/", data=data, headers=H, follow_redirects=True)


# ---------------------------------------------------------------------------


class TestTheWeek:
    def test_it_is_the_same_week_as_the_verse_of_the_week(self):
        """One week, not two.

        A member reading this week's verse and writing this week's check-in
        is in one week. If these ever diverged, a church would have a verse
        week starting Sunday and a prayer week starting Monday, and the two
        screens would disagree about what "this week" means on a Sunday
        night with nothing to point at as the cause.
        """
        from app.models.verse import week_of as verse_week

        for offset in range(14):
            day = date(2026, 11, 1) + timedelta(days=offset)
            assert week_of(day) == verse_week(day)

    def test_the_week_starts_on_sunday(self):
        # Sunday 8 November 2026.
        assert week_of(date(2026, 11, 8)) == date(2026, 11, 8)
        assert week_of(date(2026, 11, 14)) == date(2026, 11, 8)
        assert week_of(date(2026, 11, 15)) == date(2026, 11, 15)

    def test_saturday_evening_in_missouri_is_still_this_week(self, member, db,
                                                             journey, linked,
                                                             monkeypatch):
        """The church's clock, not the server's.

        At 6pm on a Saturday in Jackson it is already Sunday in UTC. A
        check-in written then belongs to the week that is ending. Filed by
        the server clock it lands a week early, which means the tile reads
        "Done this week" on a week they never wrote for and the prayer team
        never sees what they did write.

        Patched at `app.timeutil`, which is where the route imports it from,
        so this exercises the route rather than the helper.
        """
        import app.timeutil as timeutil
        from zoneinfo import ZoneInfo

        # 00:30 UTC on Sunday 18 October 2026, which in Jackson is 7:30pm on
        # Saturday the 17th, in the week that began Sunday the 11th.
        #
        # Three different answers, deliberately: the right one is the 11th,
        # reading the UTC date gives the 18th, and reading the server's own
        # today gives whatever week the suite is run in. A moment whose local
        # week happened to match today's would let the server-clock version
        # pass, which is exactly what it did the first time this was written.
        moment = datetime(2026, 10, 18, 0, 30, tzinfo=timezone.utc)
        monkeypatch.setattr(
            timeutil, "now_local",
            lambda church: moment.astimezone(ZoneInfo("America/Chicago")),
        )

        send(member, "Saturday night.")

        rows = checkins(db, journey)
        assert len(rows) == 1
        assert rows[0].week_of == date(2026, 10, 11)
        assert rows[0].week_of != week_of(moment.date())
        assert rows[0].week_of != week_of(date.today())


class TestCheckingIn:
    def test_it_records_what_they_wrote(self, member, db, journey, linked):
        send(member, "Pray for my mother, she is having a scan on Thursday.")

        rows = checkins(db, journey)
        assert len(rows) == 1
        assert rows[0].body == "Pray for my mother, she is having a scan on Thursday."
        assert rows[0].person_id == linked.id
        assert rows[0].wants_contact is False
        assert rows[0].prayed_at is None

    def test_an_empty_check_in_is_refused(self, member, db, journey, linked):
        """A blank row is not a check-in.

        Saved, it would sit on the prayer team's list as somebody's name with
        nothing under it, and whoever is praying through the week would have
        to decide whether that means anything.
        """
        response = send(member, "   ")

        assert checkins(db, journey) == []
        assert "Say anything at all" in response.get_data(as_text=True)

    def test_the_tile_says_done_once_they_have(self, member, db, journey, linked):
        before = member.get("/me/", headers=H).get_data(as_text=True)
        assert "Tell us how to pray this week" in before
        assert "Done this week" not in before

        send(member)

        after = member.get("/me/", headers=H).get_data(as_text=True)
        assert "Done this week" in after

    def test_the_timeline_says_they_checked_in_and_not_what_they_wrote(
        self, member, db, journey, linked
    ):
        """One copy of what somebody wrote.

        The timeline is read by every leader who opens the person's page; the
        prayer list is read by the people praying. Putting the words on both
        doubles the number of people who see them for no gain.
        """
        send(member, "I have been struggling with my drinking again.")

        events = db.session.scalars(
            db.select(PersonEvent).where(PersonEvent.person_id == linked.id)
        ).all()
        lines = [f"{e.summary or ''} {e.detail or ''}" for e in events]

        assert any("prayer check in" in line.lower() for line in lines)
        assert not any("drinking" in line.lower() for line in lines)

    def test_a_long_check_in_is_cut_rather_than_rejected(self, member, db, journey,
                                                         linked):
        """Somebody who wrote four thousand words still checked in.

        Refusing the whole thing over length loses all of it, and the person
        who wrote it is the one person who cannot easily write it again.
        """
        from app.models.prayer import MAX_BODY_LENGTH

        send(member, "x" * (MAX_BODY_LENGTH + 500))

        rows = checkins(db, journey)
        assert len(rows) == 1
        assert len(rows[0].body) == MAX_BODY_LENGTH


class TestItIsEditedNotStacked:
    def test_a_second_check_in_the_same_week_edits_the_first(self, member, db,
                                                             journey, linked):
        send(member, "Pray for my mother.")
        send(member, "Pray for my mother, and for my job interview.")

        rows = checkins(db, journey)
        assert len(rows) == 1
        assert rows[0].body == "Pray for my mother, and for my job interview."

    def test_the_database_refuses_a_second_row_for_the_same_week(self, db, journey,
                                                                 linked):
        """The constraint, not only the code path.

        The route is careful. A later import, a shell session or a second
        route would not be, and this is the thing that makes "one per week"
        true of the data rather than true of one function.
        """
        from sqlalchemy.exc import IntegrityError

        a_checkin(db, journey, linked)

        db.session.add(PrayerCheckin(
            church_id=journey.id, person_id=linked.id,
            week_of=week_of(date.today()), body="Again.",
        ))
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()

    def test_last_weeks_check_in_is_not_this_weeks(self, member, db, journey, linked):
        """The reset, which is the whole point of the feature.

        No job runs and nothing expires. Last week's row is simply under a
        different key, so the tile goes back to inviting them.
        """
        a_checkin(db, journey, linked, body="Last week.",
                  when=week_of(date.today()) - timedelta(days=7))

        page = member.get("/me/", headers=H).get_data(as_text=True)
        assert "Done this week" not in page
        assert "Tell us how to pray this week" in page

    def test_checking_in_again_next_week_writes_a_new_row(self, member, db, journey,
                                                          linked):
        a_checkin(db, journey, linked, body="Last week.",
                  when=week_of(date.today()) - timedelta(days=7))

        send(member, "This week.")

        rows = checkins(db, journey)
        assert len(rows) == 2
        assert {row.body for row in rows} == {"Last week.", "This week."}

    def test_last_weeks_words_are_shown_while_writing_this_weeks(self, member, db,
                                                                 journey, linked):
        a_checkin(db, journey, linked, body="The scan on Thursday.",
                  when=week_of(date.today()) - timedelta(days=7))

        page = member.get("/me/prayer/", headers=H).get_data(as_text=True)
        assert "The scan on Thursday." in page
        assert "Last week you asked for prayer about" in page

    def test_nothing_from_two_weeks_ago_is_shown(self, member, db, journey, linked):
        """Last week, not the last one there was.

        A check-in from August resurfacing under "last week" would be wrong
        about the fact as well as unwelcome.
        """
        a_checkin(db, journey, linked, body="Ancient history.",
                  when=week_of(date.today()) - timedelta(days=14))

        page = member.get("/me/prayer/", headers=H).get_data(as_text=True)
        assert "Ancient history." not in page


class TestItDoesNotNotifyAnybody:
    def test_an_ordinary_check_in_sends_nothing(self, member, db, journey, linked):
        """Sixty check-ins must not be sixty notifications.

        This is the test that stops somebody adding "just a quick alert" to
        this route. The channel that would carry it is the one that carries
        a pastoral request, and a channel that fires every Sunday morning is
        a channel a pastor mutes.
        """
        send(member, "Pray for my week.")

        emails = db.session.scalars(
            db.select(OutboxMessage).where(OutboxMessage.church_id == journey.id)
        ).all()
        pushes = db.session.scalars(
            db.select(PushQueueItem).where(PushQueueItem.church_id == journey.id)
        ).all()

        assert emails == []
        assert pushes == []


class TestAskingForSomebodyToReachOut:
    def test_it_raises_a_pastoral_request(self, member, db, journey, linked):
        send(member, "I am not coping.", wants_contact="1")

        asks = db.session.scalars(
            db.select(SupportRequest).where(SupportRequest.church_id == journey.id)
        ).all()
        assert len(asks) == 1
        assert asks[0].kind == KIND_PRAYER
        assert asks[0].person_id == linked.id
        assert asks[0].is_open

    def test_the_member_is_told_a_pastor_knows(self, member, db, journey, linked):
        response = send(member, "I am not coping.", wants_contact="1")
        assert "a pastor has been told" in response.get_data(as_text=True)

    def test_it_tells_the_care_team_that_one_exists(self, member, db, journey, linked):
        """The escalation is the one thing here that does notify.

        It rides the existing pastoral channel rather than a new one, so a
        church that already decided who hears about pastoral requests does
        not have to decide again.
        """
        send(member, "I am not coping.", wants_contact="1")

        pastoral = db.session.scalars(
            db.select(OutboxMessage).where(
                OutboxMessage.church_id == journey.id,
                OutboxMessage.category == "pastoral",
            )
        ).all()
        assert pastoral

    def test_the_alert_does_not_carry_what_they_wrote(self, member, db, journey,
                                                      linked):
        """A pastoral request is never quoted into an inbox.

        The existing rule, asserted here because this is a new way into that
        queue and a new way in is how a rule gets bypassed.
        """
        send(member, "My husband has been hitting me.", wants_contact="1")

        rows = db.session.scalars(
            db.select(OutboxMessage).where(OutboxMessage.church_id == journey.id)
        ).all()
        pushes = db.session.scalars(
            db.select(PushQueueItem).where(PushQueueItem.church_id == journey.id)
        ).all()

        for row in rows:
            assert "hitting" not in (row.body_text or "").lower()
            assert "hitting" not in (row.subject or "").lower()
        for push in pushes:
            assert "hitting" not in f"{push.title} {push.body}".lower()

    def test_editing_later_does_not_raise_a_second_request(self, member, db, journey,
                                                           linked):
        """One ask is one ask.

        Somebody who ticks the box on Sunday and comes back on Tuesday to add
        a sentence has asked once. Two rows would put them on the pastoral
        list twice and have two people ring them about one thing.
        """
        send(member, "I am not coping.", wants_contact="1")
        send(member, "I am not coping, and my sister is unwell too.",
             wants_contact="1")

        asks = db.session.scalars(
            db.select(SupportRequest).where(SupportRequest.church_id == journey.id)
        ).all()
        assert len(asks) == 1

    def test_the_ask_cannot_be_withdrawn_by_editing(self, member, db, journey, linked):
        """Unticking the box does not unring the bell.

        A pastor has already been told. Clearing the flag would leave the
        request live on their list and gone from the member's screen, which
        is the worst of both: the member thinks they cancelled and somebody
        rings them anyway.
        """
        send(member, "I am not coping.", wants_contact="1")
        send(member, "Actually it is fine.")  # box not ticked this time

        rows = checkins(db, journey)
        assert rows[0].wants_contact is True

    def test_the_box_is_gone_once_it_has_been_ticked(self, member, db, journey,
                                                     linked):
        send(member, "I am not coping.", wants_contact="1")

        page = member.get("/me/prayer/", headers=H).get_data(as_text=True)
        assert "a pastor has been told" in page
        assert 'name="wants_contact"' not in page

    def test_somebody_already_waiting_is_not_queued_twice(self, member, db, journey,
                                                          linked):
        """They already have an open request. Adding a second says nothing.

        The pastoral list is "people waiting on a pastor", and a person can
        only be waiting once.
        """
        from app.models.support import CONTACT_EITHER

        db.session.add(SupportRequest(
            church_id=journey.id, person_id=linked.id, kind=KIND_PRAYER,
            message="Asked on Friday.", contact_pref=CONTACT_EITHER,
        ))
        db.session.commit()

        send(member, "Asking again on Sunday.", wants_contact="1")

        asks = db.session.scalars(
            db.select(SupportRequest).where(SupportRequest.church_id == journey.id)
        ).all()
        assert len(asks) == 1
        assert asks[0].message == "Asked on Friday."


class TestTheTile:
    def test_there_are_three_tiles_and_prayer_is_last(self, app):
        with app.app_context():
            codes = [tile.code for tile in tiles_for(None)]
        assert codes == ["baptism", "volunteer", PRAYER_CODE]

    def test_the_offers_still_come_through_as_tiles(self, app):
        """The grid is one loop. An offer that stopped appearing in it would
        be a tile that silently vanished from the home screen."""
        from app.next_steps import OFFERS

        with app.app_context():
            tiles = {tile.code: tile for tile in tiles_for(None)}

        for offer in OFFERS:
            assert tiles[offer.code].title == offer.title
            assert tiles[offer.code].subtitle == offer.subtitle
            assert tiles[offer.code].icon == offer.icon
            assert tiles[offer.code].tint == offer.tint

    def test_the_prayer_tile_spans_the_row(self):
        """Three tiles in a two-column grid leaves the third beside half a
        row of nothing, which reads as a tile that failed to load."""
        assert PRAYER_TILE.wide is True
        assert all(
            not tile.wide
            for tile in tiles_for(None)
            if tile.code != PRAYER_CODE
        )

    def test_the_prayer_tile_is_not_a_one_time_ask(self):
        """Which is what stops it ever saying "You have asked"."""
        assert PRAYER_TILE.one_time is False

    def test_each_kind_of_tile_goes_where_it_should(self, app):
        """One of them carries its code in the URL and one does not, which is
        the whole difference between an offer and the check-in."""
        with app.test_request_context("/", headers={"Host": JOURNEY_HOST}):
            tiles = {tile.code: tile for tile in tiles_for(None)}
            assert url_for_tile(tiles["baptism"]) == "/me/next-step/baptism/"
            assert url_for_tile(tiles[PRAYER_CODE]) == "/me/prayer/"

    def test_the_prayer_tint_is_visible_on_the_tile(self):
        """The same floor every other tint is held to. The icon is the only
        colour on a tile and the first thing anybody sees."""
        from app.brand import PALETTES, contrast

        for key, palette in PALETTES.items():
            got = contrast(PRAYER_TILE.tint, palette.tile)
            assert got >= 3.0, (
                f"{PRAYER_TILE.tint} is {got:.2f}:1 on {key}'s tile. Darken it."
            )

    def test_the_tint_is_not_one_of_the_others(self):
        """Colour is how three tiles are told apart at arm's length."""
        tints = [tile.tint for tile in tiles_for(None)]
        assert len(set(tints)) == len(tints)

    def test_its_glyph_exists(self):
        """A tile with an empty path renders a blank square where the one
        piece of colour on it should be."""
        from app.next_steps import icon_path

        assert icon_path(PRAYER_TILE.icon).startswith("M")

    def test_it_does_not_reuse_a_tab_bar_glyph(self):
        """The member tab bar already uses a speech bubble for Chat and a
        heart for Give. A tile wearing either one points at the wrong place.
        """
        shell = open("app/templates/member/base.html").read()
        assert PRAYER_TILE.icon not in ("bubble", "heart")
        from app.next_steps import icon_path

        assert icon_path(PRAYER_TILE.icon) not in shell


class TestTheStaffList:
    def test_a_leader_sees_this_weeks_check_ins(self, app, db, journey, linked,
                                                client, sign_in):
        a_checkin(db, journey, linked, body="Pray for the scan on Thursday.")

        sign_in("leader@journeychurchsemo.com")
        page = client.get("/people/prayer/", headers=H).get_data(as_text=True)

        assert "Pray for the scan on Thursday." in page
        assert "Alicia Romero" in page

    def test_the_whole_thing_is_on_the_row(self, app, db, journey, linked, client,
                                           sign_in):
        """Not a first line with the rest behind a click.

        Unlike a pastoral request, this screen exists to be prayed through.
        Sending somebody to seven separate pages to do that is how praying
        through the week stops happening.
        """
        long_body = (
            "My mother is having a scan on Thursday. "
            "Also my eldest is struggling at school and I do not know how to help."
        )
        a_checkin(db, journey, linked, body=long_body)

        sign_in("leader@journeychurchsemo.com")
        page = client.get("/people/prayer/", headers=H).get_data(as_text=True)
        assert long_body in page

    def test_unprayed_ones_come_first(self, app, db, journey, linked, client,
                                      sign_in):
        """The list is worked down, so it must not reorder as it is worked.

        Sorting by "prayed for" and then by when it arrived means a row that
        has just been marked moves once, to the bottom, rather than the list
        shuffling under somebody halfway through it.
        """
        from app.models.base import utcnow

        second = Person(church_id=journey.id, first_name="Bo", last_name="Niles",
                        stage="member", approved_at=utcnow())
        db.session.add(second)
        db.session.commit()

        a_checkin(db, journey, linked, body="First in, already prayed for.",
                  prayed_at=utcnow(), prayed_by_name="Dana Webb")
        a_checkin(db, journey, second, body="Second in, not prayed for.")

        with app.app_context():
            rows = db.session.scalars(
                PrayerCheckin.for_week(journey.id, week_of(date.today()))
            ).all()

        assert [row.body for row in rows] == [
            "Second in, not prayed for.",
            "First in, already prayed for.",
        ]

    def test_marking_one_records_who_did_it(self, app, db, journey, linked, client,
                                            sign_in):
        row = a_checkin(db, journey, linked)

        sign_in("leader@journeychurchsemo.com")
        client.post(f"/people/prayer/{row.id}/prayed/", headers=H,
                    follow_redirects=True)

        db.session.refresh(row)
        assert row.is_prayed_for
        assert row.prayed_by_name == "Dana Webb"

    def test_a_second_tap_does_not_rewrite_whose_name_is_on_it(self, db, journey,
                                                               linked):
        """A double tap must not credit whoever happened to tap second."""
        row = a_checkin(db, journey, linked)

        class Actor:
            id, name = 1, "Dana Webb"

        class Other:
            id, name = 2, "Pastor Reed"

        assert row.mark_prayed(Actor()) is True
        assert row.mark_prayed(Other()) is False
        assert row.prayed_by_name == "Dana Webb"

    def test_it_can_be_put_back_on_the_list(self, app, db, journey, linked, client,
                                            sign_in):
        row = a_checkin(db, journey, linked, prayed_at=utcnow(),
                        prayed_by_name="Dana Webb")

        sign_in("leader@journeychurchsemo.com")
        client.post(f"/people/prayer/{row.id}/undo/", headers=H, follow_redirects=True)

        db.session.refresh(row)
        assert row.is_prayed_for is False
        assert row.prayed_by_name is None

    def test_a_marked_one_stays_on_the_list(self, app, db, journey, linked, client,
                                            sign_in):
        """Faded, not gone.

        A church that cannot show what it prayed for last week cannot answer
        somebody who asks whether anybody did.
        """
        a_checkin(db, journey, linked, body="Pray for the scan on Thursday.",
                  prayed_at=utcnow(), prayed_by_name="Dana Webb")

        sign_in("leader@journeychurchsemo.com")
        page = client.get("/people/prayer/", headers=H).get_data(as_text=True)

        assert "Pray for the scan on Thursday." in page
        assert "Dana Webb prayed for this" in page

    def test_an_older_week_can_be_opened(self, app, db, journey, linked, client,
                                         sign_in):
        last = week_of(date.today()) - timedelta(days=7)
        a_checkin(db, journey, linked, body="From the week before.", when=last)

        sign_in("leader@journeychurchsemo.com")
        page = client.get(f"/people/prayer/?week={last.isoformat()}",
                          headers=H).get_data(as_text=True)
        assert "From the week before." in page

    def test_a_mid_week_date_in_the_url_means_that_week(self, app, db, journey,
                                                        linked, client, sign_in):
        """Snapped rather than refused. A date somebody typed or a link that
        lost its Sunday should still show the week they meant."""
        last_sunday = week_of(date.today()) - timedelta(days=7)
        a_checkin(db, journey, linked, body="From the week before.", when=last_sunday)

        sign_in("leader@journeychurchsemo.com")
        wednesday = last_sunday + timedelta(days=3)
        page = client.get(f"/people/prayer/?week={wednesday.isoformat()}",
                          headers=H).get_data(as_text=True)
        assert "From the week before." in page

    def test_rubbish_in_the_week_parameter_is_refused(self, app, client, sign_in):
        sign_in("leader@journeychurchsemo.com")
        assert client.get("/people/prayer/?week=soon", headers=H).status_code == 404

    def test_the_count_on_the_button_is_what_is_left_to_pray_for(self, db, journey,
                                                                 linked):
        second = Person(church_id=journey.id, first_name="Bo", last_name="Niles",
                        stage="member", approved_at=utcnow())
        db.session.add(second)
        db.session.commit()

        this_week = week_of(date.today())
        a_checkin(db, journey, linked, prayed_at=utcnow(), prayed_by_name="Dana")
        a_checkin(db, journey, second, body="Not yet.")

        assert PrayerCheckin.unprayed_count(journey.id, this_week) == 1

    def test_last_weeks_leftovers_are_not_counted_on_the_button(self, db, journey,
                                                                linked):
        """The button says what is left of this week.

        Counting every unprayed check-in ever would mean a number that only
        grows and a button nobody believes.
        """
        a_checkin(db, journey, linked, body="Never got to it.",
                  when=week_of(date.today()) - timedelta(days=7))

        assert PrayerCheckin.unprayed_count(journey.id, week_of(date.today())) == 0


class TestOnTheirOwnPage:
    def test_recent_check_ins_are_on_the_person_page(self, app, db, journey, linked,
                                                     client, sign_in):
        """The page somebody opens before a visit.

        Four weeks of a person's own words about their own weeks is the
        context that phone call needs, and it is already in the system.
        """
        a_checkin(db, journey, linked, body="The scan on Thursday.")

        sign_in("leader@journeychurchsemo.com")
        page = client.get(f"/people/{linked.id}/", headers=H).get_data(as_text=True)

        assert "Prayer check-ins" in page
        assert "The scan on Thursday." in page

    def test_the_newest_week_is_first(self, db, journey, linked):
        this_week = week_of(date.today())
        a_checkin(db, journey, linked, body="Older.",
                  when=this_week - timedelta(days=14))
        a_checkin(db, journey, linked, body="Newer.", when=this_week)

        rows = db.session.scalars(
            PrayerCheckin.recent_for_person(journey.id, linked.id)
        ).all()
        assert [row.body for row in rows] == ["Newer.", "Older."]

    def test_it_stops_after_a_few_weeks(self, db, journey, linked):
        """A year of weeks on a profile is a page nobody scrolls."""
        this_week = week_of(date.today())
        for back in range(10):
            a_checkin(db, journey, linked, body=f"Week minus {back}.",
                      when=this_week - timedelta(days=7 * back))

        rows = db.session.scalars(
            PrayerCheckin.recent_for_person(journey.id, linked.id)
        ).all()
        assert len(rows) == 6
        assert rows[0].body == "Week minus 0."

    def test_another_churchs_check_ins_are_not_on_it(self, app, db, client, sign_in):
        riverbend = db.session.scalar(
            db.select(Church).where(Church.slug == "riverbend")
        )
        journey = db.session.scalar(db.select(Church).where(Church.slug == "journey"))

        theirs = Person(church_id=riverbend.id, first_name="Jo", last_name="Alder",
                        stage="member", approved_at=utcnow())
        db.session.add(theirs)
        db.session.commit()
        a_checkin(db, riverbend, theirs, body="Riverbend private business.")

        # Same row id space, different church. The query has to filter on
        # church id and not only on person id.
        rows = db.session.scalars(
            PrayerCheckin.recent_for_person(journey.id, theirs.id)
        ).all()
        assert rows == []

    def test_a_page_with_no_check_ins_does_not_show_an_empty_card(
        self, app, db, journey, linked, client, sign_in
    ):
        sign_in("leader@journeychurchsemo.com")
        page = client.get(f"/people/{linked.id}/", headers=H).get_data(as_text=True)
        assert "Prayer check-ins" not in page


class TestWhoCanSeeIt:
    def test_a_member_cannot_open_the_staff_list(self, member):
        assert member.get("/people/prayer/", headers=H).status_code == 403

    def test_a_member_cannot_mark_one_prayed_for(self, member, db, journey, linked):
        row = a_checkin(db, journey, linked)
        response = member.post(f"/people/prayer/{row.id}/prayed/", headers=H)

        assert response.status_code == 403
        db.session.refresh(row)
        assert row.is_prayed_for is False

    def test_another_churchs_check_in_is_not_on_this_list(self, app, db, client,
                                                          sign_in):
        """Tenant scoping, asserted through the screen rather than the query.

        Every table here carries a church id; this is the test that the list
        actually filters on it.
        """
        riverbend = db.session.scalar(
            db.select(Church).where(Church.slug == "riverbend")
        )
        journey = db.session.scalar(db.select(Church).where(Church.slug == "journey"))

        theirs = Person(church_id=riverbend.id, first_name="Jo", last_name="Alder",
                        stage="member", approved_at=utcnow())
        db.session.add(theirs)
        db.session.commit()
        a_checkin(db, riverbend, theirs, body="Riverbend's private business.")

        sign_in("leader@journeychurchsemo.com")
        page = client.get("/people/prayer/", headers=H).get_data(as_text=True)

        assert "Riverbend's private business." not in page
        assert "Jo Alder" not in page

    def test_another_churchs_row_cannot_be_marked_from_here(self, app, db, client,
                                                            sign_in):
        riverbend = db.session.scalar(
            db.select(Church).where(Church.slug == "riverbend")
        )
        theirs = Person(church_id=riverbend.id, first_name="Jo", last_name="Alder",
                        stage="member", approved_at=utcnow())
        db.session.add(theirs)
        db.session.commit()
        row = a_checkin(db, riverbend, theirs)

        sign_in("leader@journeychurchsemo.com")
        response = client.post(f"/people/prayer/{row.id}/prayed/", headers=H)

        assert response.status_code == 404
        db.session.refresh(row)
        assert row.is_prayed_for is False

    def test_signing_in_is_required_to_check_in(self, client):
        response = client.get("/me/prayer/", headers=H)
        assert response.status_code in (301, 302)
        assert "/auth/login" in response.headers["Location"]


class TestTheListReadsAsAListToWorkDown:
    """Three CSS rules carry meaning on the staff screen, so they are pinned.

    Pinned structurally rather than by behaviour because a stylesheet has no
    behaviour a request can assert. Each of these was found by rendering the
    page and measuring it, not by looking at it, and each one failed quietly
    the first time: a rule that loses the cascade changes nothing visible in
    the markup and everything visible on the screen.
    """

    @staticmethod
    def css() -> str:
        return open("app/static/css/app.css").read()

    def test_the_words_somebody_wrote_are_full_ink(self):
        """`.card .bd p` mutes body copy. That is right for a caption and
        wrong for the one thing on this screen somebody came to read, and a
        lower-specificity rule loses to it silently."""
        assert ".card .bd p.praybody{color:var(--ink)}" in self.css()

    def test_a_prayed_for_one_is_faded_rather_than_gone(self):
        """It stays on the list. A church that cannot show what it prayed for
        last week cannot answer somebody who asks whether anybody did."""
        assert ".card .bd .prayrow.done p.praybody{color:var(--muted)}" in self.css()

    def test_the_paragraphs_somebody_typed_survive(self):
        """Collapsed, a prayer request becomes a wall and loses where one
        thing ended and the next began."""
        css = self.css()
        start = css.index(".praybody{")
        assert "white-space:pre-wrap" in css[start:start + 300]

    def test_the_wide_tile_spans_the_whole_grid(self):
        css = self.css()
        start = css.index(".nstile.wide{")
        assert "grid-column:1/-1" in css[start:start + 200]


class TestTheWording:
    def test_the_member_is_told_who_reads_it(self, member):
        page = member.get("/me/prayer/", headers=H).get_data(as_text=True)
        assert "Seen by" in page

    def test_the_member_is_told_the_week_ends(self, member):
        """Somebody deciding whether to write something honest is deciding on
        how long it will be there. That is not small print."""
        page = member.get("/me/prayer/", headers=H).get_data(as_text=True)
        assert "starts again on Sunday" in page

    def test_it_does_not_promise_a_reply(self, member):
        """The prayer team prays. It does not undertake to ring everybody,
        and a form that implies otherwise is a form that disappoints people
        at the worst possible time."""
        page = member.get("/me/prayer/", headers=H).get_data(as_text=True).lower()
        for promise in ("we will get back to you", "somebody will be in touch"):
            assert promise not in page

    def test_the_staff_screen_says_the_words_go_no_further(self, app, client,
                                                           sign_in, db, journey,
                                                           linked):
        a_checkin(db, journey, linked)
        sign_in("leader@journeychurchsemo.com")
        page = client.get("/people/prayer/", headers=H).get_data(as_text=True)
        assert "never emailed" in page
