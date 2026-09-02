"""Increment 10: services, songs, and teams.

`TestMusic` gets the most attention because it is pure logic with a right
answer, and because a band handed the wrong key notices in front of a
congregation.
"""

from datetime import datetime, timedelta

import pytest

from app.models import (
    ACCEPTED,
    DECLINED,
    INVITED,
    Church,
    OutboxMessage,
    Person,
    Service,
    ServiceAssignment,
    ServiceItem,
    Song,
    Team,
    TeamMembership,
    TeamPosition,
    User,
)
from app.models.base import utcnow
from app.music import (
    UnknownKey,
    capo_options,
    interval_between,
    key_choices,
    normalize_key,
    parse_key,
    transpose,
)
from app.timeutil import to_local
from tests.conftest import JOURNEY_HOST

MEMBER_EMAIL = "member@journeychurchsemo.com"


@pytest.fixture
def journey(db):
    church = db.session.scalar(db.select(Church).where(Church.slug == "journey"))
    church.timezone = "America/Chicago"
    db.session.commit()
    return church


@pytest.fixture
def volunteer(db, journey):
    person = Person(
        church_id=journey.id, first_name="Alicia", last_name="Romero",
        email=MEMBER_EMAIL, stage="volunteer",
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
def service(db, journey):
    svc = Service(
        church_id=journey.id, name="Sunday", starts_at=utcnow() + timedelta(days=5)
    )
    db.session.add(svc)
    db.session.commit()
    return svc


class TestMusic:
    def test_plain_keys_parse(self):
        assert str(parse_key("G")) == "G"
        assert str(parse_key("Bb")) == "Bb"
        assert str(parse_key("F#m")) == "F#m"

    def test_a_lone_lowercase_letter_is_not_minor(self):
        """Classical notation says "a" means A minor. This audience does not.

        A volunteer typing "g" into a key field means G major essentially
        always, and silently recording G minor would hand a band the wrong key
        with nothing on screen to reveal it. Minor has to be said out loud.
        """
        assert str(parse_key("a")) == "A"
        assert str(parse_key("g")) == "G"
        assert str(parse_key("gm")) == "Gm"

    @pytest.mark.parametrize("written", ["Am", "A minor", "Amin", "am"])
    def test_the_ways_people_write_minor(self, written):
        assert str(parse_key(written)) == "Am"

    def test_unicode_accidentals(self):
        assert str(parse_key("B♭")) == "Bb"
        assert str(parse_key("F♯m")) == "F#m"

    def test_enharmonics_are_respelled_conventionally(self):
        """A# and Bb are the same pitch and are not the same key.

        A band handed 'A# major' stops and asks, because that key signature has
        ten sharps.
        """
        assert str(parse_key("A#")) == "Bb"
        assert str(parse_key("C#")) == "Db"
        assert str(parse_key("Gb")) == "F#"

    def test_minor_keys_spell_differently_from_major(self):
        assert str(parse_key("A#m")) == "Bbm"
        assert str(parse_key("C#m")) == "C#m"  # not Dbm
        assert str(parse_key("Db")) == "Db"    # not C#

    def test_nonsense_is_refused_with_the_input_named(self):
        with pytest.raises(UnknownKey, match="Try G, Bb"):
            parse_key("H")
        with pytest.raises(UnknownKey):
            parse_key("")
        with pytest.raises(UnknownKey):
            parse_key(None)

    def test_transposition(self):
        assert str(transpose("G", 2)) == "A"
        assert str(transpose("A", -3)) == "F#"
        assert str(transpose("Bb", 1)) == "B"

    def test_transposition_keeps_the_quality(self):
        assert str(transpose("Am", 2)) == "Bm"
        assert str(transpose("F#m", 3)) == "Am"

    def test_transposition_wraps_the_octave(self):
        assert str(transpose("A", 12)) == "A"
        assert str(transpose("C", 13)) == "Db"
        assert str(transpose("C", -1)) == "B"

    def test_interval_takes_the_shorter_way(self):
        """Down a fourth and up a fifth are the same pitch and different
        instructions to a band."""
        assert interval_between("G", "A") == 2
        assert interval_between("G", "F") == -2
        assert interval_between("C", "G") == -5
        assert interval_between("G", "G") == 0

    def test_capo_gives_a_playable_shape(self):
        options = capo_options("A")
        assert options
        assert all(o.sounds_like == "A" for o in options)
        # Capo 2 with G shapes sounds in A. That is the answer a guitarist wants.
        assert any(o.capo == 2 and o.shape == "G" for o in options)

    def test_capo_works_for_an_awkward_key(self):
        """Bb is where a volunteer guitarist would otherwise give up."""
        options = capo_options("Bb")
        assert any(o.capo == 3 and o.shape == "G" for o in options)
        assert any(o.capo == 1 and o.shape == "A" for o in options)

    def test_capo_shapes_are_ones_a_volunteer_owns(self):
        for key in ("A", "Bb", "B", "C", "Db", "D", "Eb", "E", "F", "F#", "G", "Ab"):
            for option in capo_options(key):
                assert option.shape.rstrip("m") in ("G", "D", "A", "E", "C"), key

    def test_capo_handles_minor_keys(self):
        options = capo_options("Bm")
        assert options
        assert all(o.shape.endswith("m") for o in options)

    def test_normalize_key(self):
        assert normalize_key("a#") == "Bb"
        assert normalize_key("  g  ") == "G"
        assert normalize_key("") is None
        assert normalize_key(None) is None

    def test_key_choices_are_all_parseable(self):
        for key in key_choices():
            assert str(parse_key(key)) == key


class TestSongs:
    def test_a_song_stores_metadata_not_words(self):
        """Reproducing lyrics needs the church's CCLI licence, not ours."""
        columns = {c.name for c in Song.__table__.columns}
        for forbidden in ("lyrics", "chords", "chart", "sheet"):
            assert not any(forbidden in name for name in columns)

    def test_a_song_offers_capo_options(self, db, journey):
        song = Song(church_id=journey.id, title="Known", default_key="Bb")
        db.session.add(song)
        db.session.commit()
        assert song.capo_options

    def test_a_song_with_no_key_offers_none(self, db, journey):
        song = Song(church_id=journey.id, title="Known")
        db.session.add(song)
        db.session.commit()
        assert song.capo_options == []

    def test_a_broken_key_in_the_column_does_not_crash_the_page(self, db, journey):
        """Bad data shows less. It must not take the songs list down."""
        song = Song(church_id=journey.id, title="Known", default_key="H#")
        db.session.add(song)
        db.session.commit()
        assert song.capo_options == []

    def test_adding_a_song_normalizes_the_key(self, db, staff):
        staff.post(
            "/services/songs/",
            data={"title": "Known", "default_key": "a#"},
            headers={"Host": JOURNEY_HOST},
        )
        church = db.session.scalar(db.select(Church).where(Church.slug == "journey"))
        song = db.session.scalars(Song.for_church(church.id)).one()
        assert song.default_key == "Bb"

    def test_a_bad_key_is_refused_with_a_message(self, db, staff):
        r = staff.post(
            "/services/songs/",
            data={"title": "Known", "default_key": "H"},
            headers={"Host": JOURNEY_HOST},
            follow_redirects=True,
        )
        assert b"is not a key" in r.data
        church = db.session.scalar(db.select(Church).where(Church.slug == "journey"))
        assert db.session.scalars(Song.for_church(church.id)).all() == []

    def test_times_used_counts_plan_appearances(self, db, journey, service):
        song = Song(church_id=journey.id, title="Known", default_key="G")
        db.session.add(song)
        db.session.flush()
        for position in (1, 2):
            db.session.add(
                ServiceItem(
                    church_id=journey.id, service_id=service.id, position=position,
                    kind="song", title="Known", song_id=song.id,
                )
            )
        db.session.commit()
        assert Song.times_used(journey.id)[song.id] == 2


class TestPlan:
    def test_a_service_is_created_in_wall_clock_time(self, db, staff, journey):
        staff.post(
            "/services/",
            data={"name": "Sunday", "starts_at": "2026-06-14T09:00"},
            headers={"Host": JOURNEY_HOST},
        )
        service = db.session.scalars(db.select(Service)).one()
        assert to_local(service.starts_at, journey).hour == 9

    def test_a_bad_time_is_refused(self, db, staff):
        r = staff.post(
            "/services/",
            data={"starts_at": "sunday morning"},
            headers={"Host": JOURNEY_HOST},
            follow_redirects=True,
        )
        assert b"does not look like a date" in r.data
        assert db.session.scalars(db.select(Service)).all() == []

    def test_items_are_numbered_in_order(self, db, staff, service):
        for title in ("Welcome", "Offering", "Sermon"):
            staff.post(
                f"/services/{service.id}/items/",
                data={"kind": "element", "title": title, "minutes": 5},
                headers={"Host": JOURNEY_HOST},
            )
        db.session.refresh(service)
        assert [item.position for item in service.items] == [1, 2, 3]
        assert service.total_minutes == 15

    def test_a_song_item_takes_the_song_key_by_default(self, db, journey, staff, service):
        song = Song(church_id=journey.id, title="Known", default_key="G")
        db.session.add(song)
        db.session.commit()

        staff.post(
            f"/services/{service.id}/items/",
            data={"kind": "song", "song_id": song.id},
            headers={"Host": JOURNEY_HOST},
        )
        db.session.refresh(service)
        assert service.items[0].key == "G"

    def test_an_override_beats_the_song_default(self, db, journey, staff, service):
        """The same song does not sit in the same key every week."""
        song = Song(church_id=journey.id, title="Known", default_key="G")
        db.session.add(song)
        db.session.commit()

        staff.post(
            f"/services/{service.id}/items/",
            data={"kind": "song", "song_id": song.id, "key_override": "A"},
            headers={"Host": JOURNEY_HOST},
        )
        db.session.refresh(service)
        assert service.items[0].key == "A"
        assert song.default_key == "G"

    def test_a_song_from_another_church_is_refused(self, db, staff, service):
        riverbend = db.session.scalar(db.select(Church).where(Church.slug == "riverbend"))
        theirs = Song(church_id=riverbend.id, title="Theirs")
        db.session.add(theirs)
        db.session.commit()

        staff.post(
            f"/services/{service.id}/items/",
            data={"kind": "song", "song_id": theirs.id},
            headers={"Host": JOURNEY_HOST},
        )
        db.session.refresh(service)
        assert service.items == []

    def test_an_element_needs_a_title(self, db, staff, service):
        staff.post(
            f"/services/{service.id}/items/",
            data={"kind": "element", "title": "  "},
            headers={"Host": JOURNEY_HOST},
        )
        db.session.refresh(service)
        assert service.items == []

    def test_positions_are_unique_within_a_service(self, db, journey, service):
        for _ in range(2):
            db.session.add(
                ServiceItem(
                    church_id=journey.id, service_id=service.id,
                    position=1, kind="element", title="Clash",
                )
            )
        with pytest.raises(Exception):
            db.session.commit()
        db.session.rollback()

    def test_deleting_a_service_takes_its_plan(self, db, journey, service):
        db.session.add(
            ServiceItem(
                church_id=journey.id, service_id=service.id,
                position=1, kind="element", title="Welcome",
            )
        )
        db.session.commit()
        db.session.delete(service)
        db.session.commit()
        assert db.session.scalars(db.select(ServiceItem)).all() == []


class TestTeams:
    def test_a_position_is_unique_per_team(self, db, journey):
        team = Team(church_id=journey.id, name="Worship")
        db.session.add(team)
        db.session.flush()
        for _ in range(2):
            db.session.add(
                TeamPosition(church_id=journey.id, team_id=team.id, name="Drums")
            )
        with pytest.raises(Exception):
            db.session.commit()
        db.session.rollback()

    def test_a_person_joins_a_team_once(self, db, journey, volunteer):
        team = Team(church_id=journey.id, name="Worship")
        db.session.add(team)
        db.session.flush()
        for _ in range(2):
            db.session.add(
                TeamMembership(
                    church_id=journey.id, team_id=team.id, person_id=volunteer.id
                )
            )
        with pytest.raises(Exception):
            db.session.commit()
        db.session.rollback()

    def test_people_serving_is_distinct(self, db, journey, volunteer):
        for name in ("Worship", "Kids"):
            team = Team(church_id=journey.id, name=name)
            db.session.add(team)
            db.session.flush()
            db.session.add(
                TeamMembership(
                    church_id=journey.id, team_id=team.id, person_id=volunteer.id
                )
            )
        db.session.commit()
        assert Team.people_serving(journey.id) == 1

    def test_adding_someone_from_another_church_is_refused(self, db, journey, staff):
        team = Team(church_id=journey.id, name="Worship")
        db.session.add(team)
        db.session.commit()

        riverbend = db.session.scalar(db.select(Church).where(Church.slug == "riverbend"))
        stranger = Person(
            church_id=riverbend.id, first_name="Not", last_name="Ours", stage="member"
        )
        db.session.add(stranger)
        db.session.commit()

        r = staff.post(
            f"/services/teams/{team.id}/members/",
            data={"person_id": stranger.id},
            headers={"Host": JOURNEY_HOST},
        )
        assert r.status_code == 400


class TestAssignments:
    def _assign(self, db, journey, service, person, position=None):
        assignment = ServiceAssignment(
            church_id=journey.id, service_id=service.id, person_id=person.id,
            position_id=position.id if position else None,
            position_name=position.name if position else None,
            status=INVITED,
        )
        db.session.add(assignment)
        db.session.commit()
        return assignment

    def test_the_position_name_survives_the_position_being_deleted(
        self, db, journey, service, volunteer
    ):
        """"Drums, 12 March" must not become "None, 12 March"."""
        team = Team(church_id=journey.id, name="Worship")
        db.session.add(team)
        db.session.flush()
        position = TeamPosition(church_id=journey.id, team_id=team.id, name="Drums")
        db.session.add(position)
        db.session.commit()

        assignment = self._assign(db, journey, service, volunteer, position)
        db.session.delete(position)
        db.session.commit()
        db.session.refresh(assignment)

        assert assignment.role_name == "Drums"

    def test_counts_by_status(self, db, journey, service, volunteer):
        assignment = self._assign(db, journey, service, volunteer)
        db.session.refresh(service)
        assert (service.waiting_count, service.accepted_count) == (1, 0)

        assignment.respond(ACCEPTED)
        db.session.commit()
        db.session.refresh(service)
        assert (service.waiting_count, service.accepted_count) == (0, 1)

    def test_the_same_person_and_position_cannot_be_asked_twice(
        self, db, journey, service, volunteer, staff
    ):
        self._assign(db, journey, service, volunteer)
        r = staff.post(
            f"/services/{service.id}/assignments/",
            data={"person_id": volunteer.id},
            headers={"Host": JOURNEY_HOST},
            follow_redirects=True,
        )
        assert b"already been asked" in r.data
        db.session.refresh(service)
        assert len(service.assignments) == 1

    def test_an_invalid_answer_is_refused(self, db, journey, service, volunteer):
        assignment = self._assign(db, journey, service, volunteer)
        with pytest.raises(ValueError):
            assignment.respond("maybe")


class TestMemberServing:
    def _assign(self, db, journey, service, person):
        assignment = ServiceAssignment(
            church_id=journey.id, service_id=service.id, person_id=person.id,
            position_name="Acoustic", status=INVITED,
        )
        db.session.add(assignment)
        db.session.commit()
        return assignment

    def test_a_volunteer_sees_what_they_were_asked(
        self, db, journey, service, volunteer, member
    ):
        self._assign(db, journey, service, volunteer)
        r = member.get("/me/serve/", headers={"Host": JOURNEY_HOST})
        assert r.status_code == 200
        assert b"Acoustic" in r.data

    def test_a_volunteer_sees_the_plan(self, db, journey, service, volunteer, member):
        db.session.add(
            ServiceItem(
                church_id=journey.id, service_id=service.id, position=1,
                kind="song", title="Known", key_override="A",
            )
        )
        db.session.commit()
        self._assign(db, journey, service, volunteer)

        r = member.get("/me/serve/", headers={"Host": JOURNEY_HOST})
        assert b"Known" in r.data
        assert b">A<" in r.data

    def test_accepting(self, db, journey, service, volunteer, member):
        assignment = self._assign(db, journey, service, volunteer)
        member.post(
            f"/me/serve/{assignment.id}/",
            data={"answer": ACCEPTED},
            headers={"Host": JOURNEY_HOST},
        )
        db.session.refresh(assignment)
        assert assignment.status == ACCEPTED
        assert assignment.responded_at is not None

    def test_declining_then_changing_their_mind(
        self, db, journey, service, volunteer, member
    ):
        assignment = self._assign(db, journey, service, volunteer)
        member.post(
            f"/me/serve/{assignment.id}/",
            data={"answer": DECLINED},
            headers={"Host": JOURNEY_HOST},
        )
        member.post(
            f"/me/serve/{assignment.id}/",
            data={"answer": ACCEPTED},
            headers={"Host": JOURNEY_HOST},
        )
        db.session.refresh(assignment)
        assert assignment.status == ACCEPTED

    def test_nobody_can_answer_for_somebody_else(
        self, db, journey, service, volunteer, member
    ):
        """A name on a plan that never agreed to it is found out on Sunday."""
        other = Person(
            church_id=journey.id, first_name="Someone", last_name="Else", stage="member"
        )
        db.session.add(other)
        db.session.commit()
        assignment = self._assign(db, journey, service, other)

        r = member.post(
            f"/me/serve/{assignment.id}/",
            data={"answer": ACCEPTED},
            headers={"Host": JOURNEY_HOST},
        )
        assert r.status_code == 403
        db.session.refresh(assignment)
        assert assignment.status == INVITED

    def test_an_assignment_at_another_church_is_a_404(
        self, db, journey, volunteer, member
    ):
        riverbend = db.session.scalar(db.select(Church).where(Church.slug == "riverbend"))
        theirs = Service(
            church_id=riverbend.id, name="Theirs", starts_at=utcnow() + timedelta(days=2)
        )
        stranger = Person(
            church_id=riverbend.id, first_name="Not", last_name="Ours", stage="member"
        )
        db.session.add_all([theirs, stranger])
        db.session.flush()
        assignment = ServiceAssignment(
            church_id=riverbend.id, service_id=theirs.id,
            person_id=stranger.id, status=INVITED,
        )
        db.session.add(assignment)
        db.session.commit()

        r = member.post(
            f"/me/serve/{assignment.id}/",
            data={"answer": ACCEPTED},
            headers={"Host": JOURNEY_HOST},
        )
        assert r.status_code == 404

    def test_past_services_drop_off_the_list(
        self, db, journey, volunteer, member
    ):
        old = Service(
            church_id=journey.id, name="Last week",
            starts_at=utcnow() - timedelta(days=7),
        )
        db.session.add(old)
        db.session.flush()
        db.session.add(
            ServiceAssignment(
                church_id=journey.id, service_id=old.id,
                person_id=volunteer.id, position_name="Acoustic", status=ACCEPTED,
            )
        )
        db.session.commit()

        r = member.get("/me/serve/", headers={"Host": JOURNEY_HOST})
        assert b"not on a plan right now" in r.data


class TestSendingThePlan:
    def test_it_queues_one_email_per_person(self, db, journey, service, volunteer, staff):
        db.session.add(
            ServiceAssignment(
                church_id=journey.id, service_id=service.id,
                person_id=volunteer.id, position_name="Acoustic", status=INVITED,
            )
        )
        db.session.commit()

        staff.post(f"/services/{service.id}/send/", headers={"Host": JOURNEY_HOST})
        messages = db.session.scalars(db.select(OutboxMessage)).all()
        assert len(messages) == 1
        assert "Acoustic" in messages[0].body_text

    def test_the_plan_is_in_the_email(self, db, journey, service, volunteer, staff):
        db.session.add_all([
            ServiceItem(
                church_id=journey.id, service_id=service.id, position=1,
                kind="song", title="Known", key_override="A",
            ),
            ServiceAssignment(
                church_id=journey.id, service_id=service.id,
                person_id=volunteer.id, position_name="Acoustic", status=INVITED,
            ),
        ])
        db.session.commit()

        staff.post(f"/services/{service.id}/send/", headers={"Host": JOURNEY_HOST})
        body = db.session.scalars(db.select(OutboxMessage)).one().body_text
        assert "Known" in body
        assert "(A)" in body

    def test_sending_twice_on_the_same_day_does_not_email_twice(
        self, db, journey, service, volunteer, staff
    ):
        """A double-clicked button must not email a volunteer twice.

        Deduped per person per service per calendar day: a genuine resend next
        week, after the plan changed, still goes out.
        """
        db.session.add(
            ServiceAssignment(
                church_id=journey.id, service_id=service.id,
                person_id=volunteer.id, position_name="Acoustic", status=INVITED,
            )
        )
        db.session.commit()

        staff.post(f"/services/{service.id}/send/", headers={"Host": JOURNEY_HOST})
        first = len(db.session.scalars(db.select(OutboxMessage)).all())
        staff.post(f"/services/{service.id}/send/", headers={"Host": JOURNEY_HOST})
        assert len(db.session.scalars(db.select(OutboxMessage)).all()) == first

    def test_sending_with_nobody_on_the_plan_says_so(self, db, service, staff):
        r = staff.post(
            f"/services/{service.id}/send/",
            headers={"Host": JOURNEY_HOST},
            follow_redirects=True,
        )
        assert b"Nobody is on this plan" in r.data

    def test_somebody_with_no_email_is_skipped_not_crashed_on(
        self, db, journey, service, staff
    ):
        no_email = Person(
            church_id=journey.id, first_name="No", last_name="Email", stage="member"
        )
        db.session.add(no_email)
        db.session.flush()
        db.session.add(
            ServiceAssignment(
                church_id=journey.id, service_id=service.id,
                person_id=no_email.id, position_name="Acoustic", status=INVITED,
            )
        )
        db.session.commit()

        r = staff.post(
            f"/services/{service.id}/send/",
            headers={"Host": JOURNEY_HOST},
            follow_redirects=True,
        )
        assert r.status_code == 200
        assert db.session.scalars(db.select(OutboxMessage)).all() == []


class TestAccess:
    def test_a_member_cannot_reach_the_planner(self, member):
        for path in ("/services/", "/services/songs/", "/services/teams/"):
            assert member.get(path, headers={"Host": JOURNEY_HOST}).status_code == 403

    def test_a_leader_can(self, leader):
        assert leader.get("/services/", headers={"Host": JOURNEY_HOST}).status_code == 200

    def test_a_service_from_another_church_is_a_404(self, db, staff):
        riverbend = db.session.scalar(db.select(Church).where(Church.slug == "riverbend"))
        theirs = Service(
            church_id=riverbend.id, name="Theirs", starts_at=utcnow() + timedelta(days=1)
        )
        db.session.add(theirs)
        db.session.commit()

        assert staff.get(
            f"/services/{theirs.id}/", headers={"Host": JOURNEY_HOST}
        ).status_code == 404
