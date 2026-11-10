"""Emailing the church from the Messages tab.

The thing worth testing here is not that an email gets queued. It is who it
gets queued for. A send-to-everyone button is the one feature in this system
where being slightly wrong is expensive and invisible: nobody notices that a
nine year old and his mother both got the same email, or that somebody still
waiting for approval was emailed as though they were a member, because the
only evidence is in three hundred inboxes nobody here can read.

So most of this file is about the recipient list, and the audience count shown
on the screen is tested as the same number that actually goes out. A count
that is right on the screen and wrong in the outbox would be worse than no
count, because staff would trust it.
"""

import pytest

from app.broadcast import EVERYONE, audiences, resolve
from app.models import (
    Church,
    Group,
    GroupMembership,
    OutboxMessage,
    Person,
    PushSubscription,
    Team,
    TeamMembership,
    User,
)
from app.models.base import utcnow
from tests.conftest import JOURNEY_HOST, PASSWORD, RIVERBEND_HOST

H = {"Host": JOURNEY_HOST}


@pytest.fixture
def journey(db):
    return db.session.scalar(db.select(Church).where(Church.slug == "journey"))


def a_person(db, church, first="Kaela", email="kaela@example.com", **kwargs):
    fields = {
        "church_id": church.id,
        "first_name": first,
        "last_name": "Menz",
        "email": email,
        "stage": "member",
        "approved_at": utcnow(),
    }
    fields.update(kwargs)
    person = Person(**fields)
    db.session.add(person)
    db.session.commit()
    return person


def queued_for(db, church):
    return db.session.scalars(
        db.select(OutboxMessage).where(
            OutboxMessage.church_id == church.id,
            OutboxMessage.category == "announcement",
        )
    ).all()


def addresses_queued(db, church):
    return sorted(m.to_email for m in queued_for(db, church))


def by_value(items, value):
    for item in items:
        if item.value == value:
            return item
    raise AssertionError(f"no audience {value!r} in {[i.value for i in items]}")


class TestWhoGetsIt:
    """The recipient list, which is the whole feature."""

    def test_a_member_with_an_address_is_included(self, db, journey):
        person = a_person(db, journey)
        assert person in resolve(journey.id, EVERYONE)

    def test_somebody_with_no_address_is_not(self, db, journey):
        person = a_person(db, journey, email=None)
        assert person not in resolve(journey.id, EVERYONE)

    def test_children_are_left_out(self, db, journey):
        """A child's row usually carries a parent's address.

        Including them sends the parent two copies and makes the count a lie.
        """
        child = a_person(db, journey, first="Eli", email="mum@example.com",
                         is_child=True)
        assert child not in resolve(journey.id, EVERYONE)

    def test_archived_people_are_left_out(self, db, journey):
        person = a_person(db, journey, is_archived=True)
        assert person not in resolve(journey.id, EVERYONE)

    def test_somebody_waiting_for_approval_is_left_out(self, db, journey):
        """Approval is already the gate on seeing church-wide posts, and an
        inbox is more public than a screen."""
        waiting = a_person(db, journey, first="Unknown",
                           email="stranger@example.com",
                           self_registered=True, approved_at=None)
        assert waiting.is_waiting_for_approval
        assert waiting not in resolve(journey.id, EVERYONE)

    def test_turning_announcements_off_leaves_somebody_out(self, db, journey):
        from app.models import NotificationPreference

        person = a_person(db, journey)
        db.session.add(NotificationPreference(
            church_id=journey.id, person_id=person.id,
            category="announcement", allowed=False,
        ))
        db.session.commit()
        assert person not in resolve(journey.id, EVERYONE)

    def test_unsubscribing_from_everything_leaves_somebody_out(self, db, journey):
        from app.mail import opt_out

        person = a_person(db, journey)
        opt_out(person)
        db.session.commit()
        assert person not in resolve(journey.id, EVERYONE)

    def test_a_shared_address_gets_one_email_not_two(self, db, journey):
        """A household on one inbox is one recipient. Counting them twice
        would overstate the audience and double the email."""
        a_person(db, journey, first="Ben", email="hollands@example.com")
        a_person(db, journey, first="Mara", email="Hollands@Example.com")

        recipients = resolve(journey.id, EVERYONE)
        shared = [p for p in recipients if (p.email or "").lower() == "hollands@example.com"]
        assert len(shared) == 1

    def test_another_church_is_never_in_the_list(self, db):
        """The one failure nobody could ever un-send."""
        journey = db.session.scalar(db.select(Church).where(Church.slug == "journey"))
        riverbend = db.session.scalar(db.select(Church).where(Church.slug == "riverbend"))

        theirs = a_person(db, riverbend, first="Theirs", email="theirs@example.com")
        assert theirs not in resolve(journey.id, EVERYONE)


class TestAudiences:
    def test_everyone_is_the_first_choice(self, db, journey):
        assert audiences(journey.id)[0].value == EVERYONE

    def test_a_team_audience_is_the_people_on_it(self, db, journey):
        team = Team(church_id=journey.id, name="Worship")
        db.session.add(team)
        on_it = a_person(db, journey, first="Jo", email="jo@example.com")
        off_it = a_person(db, journey, first="Sam", email="sam@example.com")
        db.session.flush()
        db.session.add(TeamMembership(church_id=journey.id, team_id=team.id,
                                      person_id=on_it.id))
        db.session.commit()

        people = resolve(journey.id, f"team:{team.id}")
        assert on_it in people
        assert off_it not in people

    def test_a_group_audience_is_the_people_in_it(self, db, journey):
        group = Group(church_id=journey.id, name="Tuesday study")
        db.session.add(group)
        inside = a_person(db, journey, first="Pat", email="pat@example.com")
        outside = a_person(db, journey, first="Rhys", email="rhys@example.com")
        db.session.flush()
        db.session.add(GroupMembership(church_id=journey.id, group_id=group.id,
                                       person_id=inside.id))
        db.session.commit()

        people = resolve(journey.id, f"group:{group.id}")
        assert inside in people
        assert outside not in people

    def test_a_stage_audience_is_that_stage_only(self, db, journey):
        visitor = a_person(db, journey, first="Nico", email="nico@example.com",
                           stage="visitor")
        member = a_person(db, journey, first="Ada", email="ada@example.com",
                          stage="member")

        people = resolve(journey.id, "stage:visitor")
        assert visitor in people
        assert member not in people

    def test_a_team_from_another_church_resolves_to_nobody(self, db):
        """Not an error, and not somebody else's team either."""
        journey = db.session.scalar(db.select(Church).where(Church.slug == "journey"))
        riverbend = db.session.scalar(db.select(Church).where(Church.slug == "riverbend"))
        team = Team(church_id=riverbend.id, name="Theirs")
        db.session.add(team)
        db.session.commit()

        assert resolve(journey.id, f"team:{team.id}") == ()

    def test_a_nonsense_audience_resolves_to_nobody(self, db, journey):
        assert resolve(journey.id, "wat:9") == ()
        assert resolve(journey.id, "team:notanumber") == ()
        assert resolve(journey.id, "") == ()


class TestSending:
    def test_staff_can_send_and_everybody_is_queued(self, db, journey, staff):
        a_person(db, journey, first="Kaela", email="kaela@example.com")
        before = len(queued_for(db, journey))

        page = staff.post("/messages/email/", headers=H, data={
            "audience": EVERYONE,
            "subject": "Sunday starts at 9",
            "body": "We are starting an hour early this week.",
        }, follow_redirects=True)

        assert page.status_code == 200
        after = queued_for(db, journey)
        assert len(after) > before
        assert "kaela@example.com" in [m.to_email for m in after]

    def test_the_count_on_the_screen_is_the_count_that_goes_out(
        self, db, journey, staff
    ):
        """The number staff read before clicking has to be the number of
        emails. A count that is right on screen and wrong in the outbox is
        worse than no count, because it gets trusted."""
        a_person(db, journey, first="Kaela", email="kaela@example.com")
        a_person(db, journey, first="Eli", email="kaela@example.com", is_child=True)

        expected = by_value(audiences(journey.id), EVERYONE).count
        # Otherwise zero emails matching a zero count would pass this.
        assert expected > 0
        before = len(queued_for(db, journey))

        staff.post("/messages/email/", headers=H, data={
            "audience": EVERYONE, "subject": "Counted", "body": "Body.",
        }, follow_redirects=True)

        assert len(queued_for(db, journey)) - before == expected

    def test_sending_twice_in_a_minute_does_not_double_the_email(
        self, db, journey, staff
    ):
        """A double-clicked Send on a church-wide email is the worst possible
        place for an at-least-once guarantee."""
        a_person(db, journey, first="Kaela", email="kaela@example.com")
        payload = {"audience": EVERYONE, "subject": "Twice", "body": "Same words."}

        staff.post("/messages/email/", headers=H, data=payload, follow_redirects=True)
        after_one = len(queued_for(db, journey))
        page = staff.post("/messages/email/", headers=H, data=payload,
                          follow_redirects=True)

        assert len(queued_for(db, journey)) == after_one
        assert "already went out" in page.get_data(as_text=True)

    def test_a_different_subject_sends_again(self, db, journey, staff):
        a_person(db, journey, first="Kaela", email="kaela@example.com")
        staff.post("/messages/email/", headers=H, data={
            "audience": EVERYONE, "subject": "First", "body": "Words.",
        }, follow_redirects=True)
        after_one = len(queued_for(db, journey))

        staff.post("/messages/email/", headers=H, data={
            "audience": EVERYONE, "subject": "Second", "body": "Words.",
        }, follow_redirects=True)

        assert len(queued_for(db, journey)) > after_one

    def test_an_empty_subject_or_body_is_refused(self, db, journey, staff):
        before = len(queued_for(db, journey))

        staff.post("/messages/email/", headers=H, data={
            "audience": EVERYONE, "subject": "", "body": "Words.",
        }, follow_redirects=True)
        staff.post("/messages/email/", headers=H, data={
            "audience": EVERYONE, "subject": "Subject", "body": "",
        }, follow_redirects=True)

        assert len(queued_for(db, journey)) == before

    def test_an_audience_with_nobody_in_it_is_refused_plainly(
        self, db, journey, staff
    ):
        team = Team(church_id=journey.id, name="Nobody yet")
        db.session.add(team)
        db.session.commit()

        page = staff.post("/messages/email/", headers=H, data={
            "audience": f"team:{team.id}", "subject": "Hello", "body": "Words.",
        }, follow_redirects=True)

        assert "Nobody in that audience" in page.get_data(as_text=True)

    def test_the_word_filter_applies_here_too(self, db, journey, staff):
        """The last place something should reach three hundred inboxes
        unread by a human."""
        from app.moderation import BLOCKED_STEMS, objectionable_terms

        a_person(db, journey, first="Kaela", email="kaela@example.com")
        bad = f"Sunday is going to be {BLOCKED_STEMS[0]}."
        assert objectionable_terms(bad)

        before = len(queued_for(db, journey))
        staff.post("/messages/email/", headers=H, data={
            "audience": EVERYONE, "subject": "Hello", "body": bad,
        }, follow_redirects=True)

        assert len(queued_for(db, journey)) == before

    def test_the_filter_reads_the_subject_too(self, db, journey, staff):
        from app.moderation import BLOCKED_STEMS

        a_person(db, journey, first="Kaela", email="kaela@example.com")
        before = len(queued_for(db, journey))

        staff.post("/messages/email/", headers=H, data={
            "audience": EVERYONE,
            "subject": f"A {BLOCKED_STEMS[0]} of a week",
            "body": "Nothing wrong down here.",
        }, follow_redirects=True)

        assert len(queued_for(db, journey)) == before

    def test_the_send_is_recorded_in_the_audit_log(self, db, journey, staff):
        from app.models.audit import CHURCH_EMAIL_SENT, AuditEvent

        a_person(db, journey, first="Kaela", email="kaela@example.com")
        staff.post("/messages/email/", headers=H, data={
            "audience": EVERYONE, "subject": "Audited", "body": "Words.",
        }, follow_redirects=True)

        event = db.session.scalar(
            db.select(AuditEvent).where(
                AuditEvent.church_id == journey.id,
                AuditEvent.action == CHURCH_EMAIL_SENT,
            )
        )
        assert event is not None
        assert "Audited" in event.summary


class TestAppNotification:
    def _with_a_device(self, db, journey, person):
        user = User(church_id=journey.id, email=person.email,
                    name=person.full_name, role="member", person_id=person.id)
        user.set_password(PASSWORD)
        user.mark_verified()
        user.accept_community()
        db.session.add(user)
        db.session.flush()
        PushSubscription.register(
            journey.id, user,
            endpoint=f"https://push.example.com/{person.id}",
            p256dh="x" * 60, auth="y" * 20, label="Phone",
        )
        db.session.commit()
        return user

    def test_the_email_also_pushes_by_default(self, db, journey):
        from app.broadcast import send

        person = a_person(db, journey, email="kaela@example.com")
        self._with_a_device(db, journey, person)

        sent = send(church_id=journey.id, audience=EVERYONE, subject="Sunday",
                    body_text="Starting at nine.", reference="t1")
        db.session.commit()

        assert sent.emailed >= 1
        assert sent.pushed >= 1

    def test_email_only_sends_no_notification(self, db, journey):
        """Blanking the push text would not have done it: an empty
        notification is still a notification saying nothing."""
        from app.broadcast import send

        person = a_person(db, journey, email="kaela@example.com")
        self._with_a_device(db, journey, person)

        sent = send(church_id=journey.id, audience=EVERYONE, subject="Long one",
                    body_text="Several paragraphs.", push=False, reference="t2")
        db.session.commit()

        assert sent.emailed >= 1
        assert sent.pushed == 0

    def test_a_long_body_is_cut_for_the_notification(self):
        from app.broadcast import _push_body

        body = "word " * 100
        assert len(_push_body(body)) <= 140
        assert _push_body(body).endswith("...")

    def test_a_short_body_is_left_alone(self):
        from app.broadcast import _push_body

        assert _push_body("Starting at nine.\n\nSee you there.") == (
            "Starting at nine. See you there."
        )


class TestWhoMaySend:
    def test_a_leader_cannot_reach_the_screen(self, leader):
        """A leader runs a room. Reaching the whole church in their inbox is
        a different level of authority."""
        assert leader.get("/messages/email/", headers=H).status_code == 403

    def test_a_member_cannot_reach_the_screen(self, member):
        assert member.get("/messages/email/", headers=H).status_code == 403

    def test_a_leader_cannot_post_to_it_either(self, leader):
        page = leader.post("/messages/email/", headers=H, data={
            "audience": EVERYONE, "subject": "No", "body": "No.",
        })
        assert page.status_code == 403

    def test_signed_out_is_sent_to_sign_in(self, client):
        page = client.get("/messages/email/", headers=H)
        assert page.status_code in (302, 401)

    def test_the_button_is_on_the_messages_tab_for_staff(self, staff):
        page = staff.get("/messages/", headers=H)
        assert "Send email" in page.get_data(as_text=True)
        assert "/messages/email/" in page.get_data(as_text=True)

    def test_the_button_is_not_there_for_a_leader(self, leader):
        page = leader.get("/messages/", headers=H)
        assert "/messages/email/" not in page.get_data(as_text=True)


class TestTenantIsolation:
    def test_staff_at_one_church_cannot_email_another(self, app):
        """Deliberately without the `db` fixture: two sign-ins on one
        application context inherit each other's identity."""
        with app.app_context():
            from app.extensions import db as _db

            riverbend = _db.session.scalar(
                _db.select(Church).where(Church.slug == "riverbend")
            )
            theirs = Person(church_id=riverbend.id, first_name="Theirs",
                            last_name="Member", email="theirs@example.com",
                            stage="member", approved_at=utcnow())
            _db.session.add(theirs)
            _db.session.commit()

        client = app.test_client()
        client.post("/auth/login",
                    data={"email": "pastor@journeychurchsemo.com",
                          "password": PASSWORD},
                    headers={"Host": JOURNEY_HOST}, follow_redirects=True)
        client.post("/messages/email/", headers={"Host": JOURNEY_HOST}, data={
            "audience": EVERYONE, "subject": "Ours", "body": "Our words.",
        }, follow_redirects=True)

        with app.app_context():
            from app.extensions import db as _db

            sent_to_them = _db.session.scalars(
                _db.select(OutboxMessage).where(
                    OutboxMessage.to_email == "theirs@example.com"
                )
            ).all()
            assert sent_to_them == []

    def test_the_other_churchs_staff_sees_their_own_count(self, app):
        """Two churches with deliberately different sizes, so the number on
        the screen can only be right for one of them."""
        with app.app_context():
            from app.extensions import db as _db

            journey = _db.session.scalar(
                _db.select(Church).where(Church.slug == "journey")
            )
            riverbend = _db.session.scalar(
                _db.select(Church).where(Church.slug == "riverbend")
            )
            for n in range(3):
                _db.session.add(Person(
                    church_id=riverbend.id, first_name=f"Theirs{n}",
                    last_name="Member", email=f"theirs{n}@example.com",
                    stage="member", approved_at=utcnow(),
                ))
            _db.session.commit()

            mine = by_value(audiences(journey.id), EVERYONE).count
            theirs = by_value(audiences(riverbend.id), EVERYONE).count
            assert theirs >= 3
            assert theirs != mine

        client = app.test_client()
        client.post("/auth/login",
                    data={"email": "pastor@journeychurchsemo.com",
                          "password": PASSWORD},
                    headers={"Host": RIVERBEND_HOST}, follow_redirects=True)
        page = client.get("/messages/email/", headers={"Host": RIVERBEND_HOST})

        assert page.status_code == 200
        body = page.get_data(as_text=True)
        assert f"Everyone at the church ({theirs})" in body
        assert f"Everyone at the church ({mine})" not in body


class TestTheAnnouncementPathAgrees:
    """Posting an announcement with "Email it too" and this screen have to
    mean the same thing by "everyone".

    They did not before: the announcement loop checked only for an address
    and consent, so it emailed children, archived people, and self-registered
    people still waiting for approval.
    """

    def test_a_child_is_not_emailed_an_announcement(self, db, journey, staff):
        from app.models import Conversation

        a_person(db, journey, first="Eli", email="parent@example.com",
                 is_child=True)
        conversation = Conversation(church_id=journey.id, kind="announcement",
                                    title="Sunday")
        db.session.add(conversation)
        db.session.commit()

        staff.post(f"/messages/{conversation.id}/post/", headers=H, data={
            "body": "Starting at nine.", "also_email": "on",
        }, follow_redirects=True)

        assert "parent@example.com" not in addresses_queued(db, journey)

    def test_somebody_waiting_for_approval_is_not_emailed_an_announcement(
        self, db, journey, staff
    ):
        from app.models import Conversation

        a_person(db, journey, first="Unknown", email="stranger@example.com",
                 self_registered=True, approved_at=None)
        conversation = Conversation(church_id=journey.id, kind="announcement",
                                    title="Sunday two")
        db.session.add(conversation)
        db.session.commit()

        staff.post(f"/messages/{conversation.id}/post/", headers=H, data={
            "body": "Starting at nine.", "also_email": "on",
        }, follow_redirects=True)

        assert "stranger@example.com" not in addresses_queued(db, journey)
