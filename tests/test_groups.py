"""Increment 9: groups, meetings, and RSVPs.

Two things carry the risk. Timezone rendering, because a group that reads an
hour wrong sends people to an empty house. And RSVP authorization, because the
count a leader plans around has to mean something.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.models import (
    ROLE_LEADER,
    ROLE_MEMBER,
    RSVP_GOING,
    RSVP_MAYBE,
    RSVP_NOT_GOING,
    Church,
    Group,
    GroupMeeting,
    GroupMembership,
    MeetingRSVP,
    Person,
    User,
)
from app.models.base import utcnow
from app.timeutil import (
    DEFAULT_TIMEZONE,
    format_local,
    from_local,
    is_valid_timezone,
    to_local,
    zone_for,
)
from tests.conftest import JOURNEY_HOST

MEMBER_EMAIL = "member@journeychurchsemo.com"


@pytest.fixture
def journey(db):
    church = db.session.scalar(db.select(Church).where(Church.slug == "journey"))
    church.timezone = "America/Chicago"
    db.session.commit()
    return church


@pytest.fixture
def linked_member(db, journey):
    person = Person(
        church_id=journey.id, first_name="Alicia", last_name="Romero",
        email=MEMBER_EMAIL, stage="attender",
    )
    db.session.add(person)
    db.session.flush()
    user = db.session.scalar(
        db.select(User).where(User.email == MEMBER_EMAIL, User.church_id == journey.id)
    )
    user.person_id = person.id
    db.session.commit()
    return person


@pytest.fixture
def group(db, journey, linked_member):
    grp = Group(
        church_id=journey.id,
        name="Wednesday Women",
        meeting_pattern="Wednesdays 7:00pm",
        location="The Hollands' house",
    )
    db.session.add(grp)
    db.session.flush()
    db.session.add(
        GroupMembership(
            church_id=journey.id, group_id=grp.id,
            person_id=linked_member.id, role=ROLE_MEMBER,
        )
    )
    db.session.commit()
    return grp


class TestTimezone:
    def test_a_stored_utc_time_renders_in_the_church_zone(self, journey):
        # 01:00 UTC is 7:00pm the previous evening in Chicago (CDT, June).
        stored = datetime(2026, 6, 11, 1, 0, tzinfo=timezone.utc)
        local = to_local(stored, journey)
        assert local.hour == 20 or local.hour == 19
        assert format_local(stored, journey, "%-I:%M%p").endswith("pm")

    def test_a_wall_clock_time_round_trips(self, journey):
        typed = datetime(2026, 6, 10, 19, 0)  # naive, as a form gives it
        stored = from_local(typed, journey)
        assert stored.tzinfo is not None
        back = to_local(stored, journey)
        assert (back.hour, back.minute) == (19, 0)

    def test_the_stored_value_is_utc_not_local(self, journey):
        stored = from_local(datetime(2026, 6, 10, 19, 0), journey)
        assert stored.utcoffset() == timedelta(0)
        assert stored.hour != 19

    def test_daylight_saving_is_handled_by_the_zone(self, journey):
        """Chicago is UTC-5 in June and UTC-6 in January."""
        summer = from_local(datetime(2026, 6, 10, 19, 0), journey)
        winter = from_local(datetime(2026, 1, 10, 19, 0), journey)
        assert summer.hour != winter.hour

    def test_two_churches_read_the_same_instant_differently(self, db, journey):
        riverbend = db.session.scalar(db.select(Church).where(Church.slug == "riverbend"))
        riverbend.timezone = "America/New_York"
        db.session.commit()

        instant = datetime(2026, 6, 11, 0, 0, tzinfo=timezone.utc)
        assert to_local(instant, journey).hour != to_local(instant, riverbend).hour

    def test_a_missing_timezone_falls_back(self, db, journey):
        journey.timezone = None
        db.session.commit()
        assert str(zone_for(journey)) == DEFAULT_TIMEZONE

    def test_a_nonsense_timezone_falls_back_rather_than_raising(self, db, journey):
        """A bad column value shows the wrong hour. An exception takes the page down."""
        journey.timezone = "Middle/Earth"
        db.session.commit()
        assert str(zone_for(journey)) == DEFAULT_TIMEZONE
        assert format_local(utcnow(), journey) != ""

    def test_validation(self):
        assert is_valid_timezone("America/Chicago")
        assert not is_valid_timezone("Middle/Earth")
        assert not is_valid_timezone(None)
        assert not is_valid_timezone("")

    def test_a_naive_value_is_treated_as_utc_not_local(self, journey):
        """Guessing local here would shift every stored time silently."""
        naive = datetime(2026, 6, 11, 1, 0)
        assert to_local(naive, journey) == to_local(
            naive.replace(tzinfo=timezone.utc), journey
        )


class TestGroupLeadershipIsNotALoginRole:
    def test_a_group_leader_is_a_membership_not_a_user_role(self, db, group, linked_member):
        membership = group.memberships[0]
        membership.role = ROLE_LEADER
        db.session.commit()

        assert membership.is_leader
        user = db.session.scalar(
            db.select(User).where(User.person_id == linked_member.id)
        )
        # Leading a group grants nothing church-wide.
        assert user.role == "member"

    def test_leading_a_group_does_not_open_the_roster(self, db, group, member, linked_member):
        group.memberships[0].role = ROLE_LEADER
        db.session.commit()
        assert member.get("/people/", headers={"Host": JOURNEY_HOST}).status_code == 403

    def test_leading_a_group_does_not_open_the_groups_admin(self, db, group, member):
        group.memberships[0].role = ROLE_LEADER
        db.session.commit()
        assert member.get("/groups/", headers={"Host": JOURNEY_HOST}).status_code == 403


class TestMembership:
    def test_one_person_cannot_join_twice(self, db, group, linked_member):
        db.session.add(
            GroupMembership(
                church_id=group.church_id, group_id=group.id,
                person_id=linked_member.id, role=ROLE_MEMBER,
            )
        )
        with pytest.raises(Exception):
            db.session.commit()
        db.session.rollback()

    def test_adding_someone_from_another_church_is_refused(self, db, group, staff):
        riverbend = db.session.scalar(db.select(Church).where(Church.slug == "riverbend"))
        stranger = Person(
            church_id=riverbend.id, first_name="Not", last_name="Ours", stage="member"
        )
        db.session.add(stranger)
        db.session.commit()

        r = staff.post(
            f"/groups/{group.id}/members/",
            data={"person_id": stranger.id},
            headers={"Host": JOURNEY_HOST},
        )
        assert r.status_code == 400
        assert not group.has_person(stranger.id)

    def test_removing_a_membership_keeps_the_person(self, db, group, linked_member, staff):
        membership = group.memberships[0]
        staff.post(
            f"/groups/{group.id}/members/{membership.id}/remove/",
            headers={"Host": JOURNEY_HOST},
        )
        db.session.expire_all()
        assert Person.get_for_church(group.church_id, linked_member.id) is not None
        assert db.session.get(Group, group.id).memberships == []

    def test_deleting_a_group_keeps_its_people(self, db, group, linked_member):
        db.session.delete(group)
        db.session.commit()
        assert Person.get_for_church(linked_member.church_id, linked_member.id) is not None

    def test_the_in_a_group_count_is_distinct_people(self, db, group, linked_member, journey):
        second = Group(church_id=journey.id, name="Men's Breakfast")
        db.session.add(second)
        db.session.flush()
        db.session.add(
            GroupMembership(
                church_id=journey.id, group_id=second.id,
                person_id=linked_member.id, role=ROLE_MEMBER,
            )
        )
        db.session.commit()
        assert Group.people_in_a_group(journey.id) == 1

    def test_an_inactive_group_does_not_count(self, db, group, journey):
        assert Group.people_in_a_group(journey.id) == 1
        group.is_active = False
        db.session.commit()
        assert Group.people_in_a_group(journey.id) == 0


class TestMeetingsAndRSVPs:
    def _meeting(self, db, group, days=3):
        meeting = GroupMeeting(
            church_id=group.church_id,
            group_id=group.id,
            meets_at=utcnow() + timedelta(days=days),
            location="The Hollands' house",
        )
        db.session.add(meeting)
        db.session.commit()
        return meeting

    def test_going_count(self, db, group, linked_member):
        meeting = self._meeting(db, group)
        MeetingRSVP.set(group.church_id, meeting, linked_member.id, RSVP_GOING)
        db.session.commit()
        db.session.refresh(meeting)
        assert meeting.going_count == 1

    def test_maybe_and_no_do_not_count_as_going(self, db, group, linked_member):
        meeting = self._meeting(db, group)
        MeetingRSVP.set(group.church_id, meeting, linked_member.id, RSVP_MAYBE)
        db.session.commit()
        db.session.refresh(meeting)
        assert meeting.going_count == 0

    def test_changing_an_answer_updates_rather_than_duplicates(
        self, db, group, linked_member
    ):
        """An RSVP is a current intention, not a fact about a moment."""
        meeting = self._meeting(db, group)
        MeetingRSVP.set(group.church_id, meeting, linked_member.id, RSVP_GOING)
        db.session.commit()
        MeetingRSVP.set(group.church_id, meeting, linked_member.id, RSVP_NOT_GOING)
        db.session.commit()

        db.session.refresh(meeting)
        assert len(meeting.rsvps) == 1
        assert meeting.response_for(linked_member.id) == RSVP_NOT_GOING
        assert meeting.going_count == 0

    def test_an_unknown_response_is_refused(self, db, group, linked_member):
        meeting = self._meeting(db, group)
        with pytest.raises(ValueError):
            MeetingRSVP.set(group.church_id, meeting, linked_member.id, "perhaps")

    def test_next_meeting_skips_past_ones(self, db, group):
        self._meeting(db, group, days=-5)
        upcoming = self._meeting(db, group, days=2)
        db.session.refresh(group)
        assert group.next_meeting().id == upcoming.id

    def test_deleting_a_meeting_takes_its_rsvps(self, db, group, linked_member):
        meeting = self._meeting(db, group)
        MeetingRSVP.set(group.church_id, meeting, linked_member.id, RSVP_GOING)
        db.session.commit()

        db.session.delete(meeting)
        db.session.commit()
        assert db.session.scalars(db.select(MeetingRSVP)).all() == []

    def test_a_staff_member_schedules_in_wall_clock_time(self, db, group, staff, journey):
        staff.post(
            f"/groups/{group.id}/meetings/",
            data={"meets_at": "2026-06-10T19:00", "location": "Here"},
            headers={"Host": JOURNEY_HOST},
        )
        db.session.refresh(group)
        meeting = group.meetings[0]
        # Stored as UTC, reads back as the 7pm that was typed.
        assert meeting.meets_at.utcoffset() == timedelta(0)
        assert to_local(meeting.meets_at, journey).hour == 19

    def test_a_bad_time_is_refused_with_a_message(self, db, group, staff):
        r = staff.post(
            f"/groups/{group.id}/meetings/",
            data={"meets_at": "next tuesday"},
            headers={"Host": JOURNEY_HOST},
            follow_redirects=True,
        )
        assert b"does not look like a date" in r.data
        db.session.refresh(group)
        assert group.meetings == []


class TestMemberRSVP:
    def _meeting(self, db, group):
        meeting = GroupMeeting(
            church_id=group.church_id, group_id=group.id,
            meets_at=utcnow() + timedelta(days=3),
        )
        db.session.add(meeting)
        db.session.commit()
        return meeting

    def test_a_member_sees_their_own_group(self, db, group, linked_member, member):
        r = member.get("/me/groups/", headers={"Host": JOURNEY_HOST})
        assert r.status_code == 200
        assert b"Wednesday Women" in r.data

    def test_a_member_not_in_a_group_is_told_so(self, db, journey, linked_member, member):
        r = member.get("/me/groups/", headers={"Host": JOURNEY_HOST})
        assert b"not in a group yet" in r.data

    def test_a_member_can_answer(self, db, group, linked_member, member):
        meeting = self._meeting(db, group)
        member.post(
            f"/me/groups/{meeting.id}/rsvp/",
            data={"response": RSVP_GOING},
            headers={"Host": JOURNEY_HOST},
        )
        db.session.refresh(meeting)
        assert meeting.response_for(linked_member.id) == RSVP_GOING

    def test_somebody_outside_the_group_cannot_answer(self, db, group, linked_member, member):
        """Otherwise the count a leader plans around means nothing."""
        meeting = self._meeting(db, group)
        db.session.delete(group.memberships[0])
        db.session.commit()

        r = member.post(
            f"/me/groups/{meeting.id}/rsvp/",
            data={"response": RSVP_GOING},
            headers={"Host": JOURNEY_HOST},
        )
        assert r.status_code == 403
        db.session.refresh(meeting)
        assert meeting.rsvps == []

    def test_a_meeting_at_another_church_is_a_404(self, db, group, linked_member, member):
        riverbend = db.session.scalar(db.select(Church).where(Church.slug == "riverbend"))
        theirs = Group(church_id=riverbend.id, name="Theirs")
        db.session.add(theirs)
        db.session.flush()
        meeting = GroupMeeting(
            church_id=riverbend.id, group_id=theirs.id,
            meets_at=utcnow() + timedelta(days=1),
        )
        db.session.add(meeting)
        db.session.commit()

        r = member.post(
            f"/me/groups/{meeting.id}/rsvp/",
            data={"response": RSVP_GOING},
            headers={"Host": JOURNEY_HOST},
        )
        assert r.status_code == 404

    def test_an_invalid_response_is_refused(self, db, group, linked_member, member):
        meeting = self._meeting(db, group)
        r = member.post(
            f"/me/groups/{meeting.id}/rsvp/",
            data={"response": "perhaps"},
            headers={"Host": JOURNEY_HOST},
        )
        assert r.status_code == 400

    def test_the_member_route_takes_no_person_id(self, app):
        for rule in app.url_map.iter_rules():
            if rule.endpoint in ("member.groups", "member.rsvp"):
                assert "person_id" not in rule.arguments


class TestTenantIsolation:
    def test_the_list_only_shows_this_church(self, db, group, staff):
        riverbend = db.session.scalar(db.select(Church).where(Church.slug == "riverbend"))
        db.session.add(Group(church_id=riverbend.id, name="Theirs"))
        db.session.commit()

        r = staff.get("/groups/", headers={"Host": JOURNEY_HOST})
        assert b"Wednesday Women" in r.data
        assert b"Theirs" not in r.data

    def test_a_group_from_another_church_is_a_404(self, db, staff):
        riverbend = db.session.scalar(db.select(Church).where(Church.slug == "riverbend"))
        theirs = Group(church_id=riverbend.id, name="Theirs")
        db.session.add(theirs)
        db.session.commit()

        assert staff.get(
            f"/groups/{theirs.id}/", headers={"Host": JOURNEY_HOST}
        ).status_code == 404
