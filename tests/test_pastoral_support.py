"""Asking a church for help, and the church seeing it.

The most sensitive path in the app. Three things are load bearing and each
has tests of its own below: what somebody wrote never leaves the app, the
request is closed by a named person rather than by time, and staff are told
it exists rather than told what it says.
"""

from datetime import timedelta

import pytest

from app import dashboard
from app.models import (
    Church, OutboxMessage, Person, PersonEvent, SupportRequest, User,
)
from app.models.base import utcnow
from app.models.support import KIND_PRAYER, KIND_URGENT, STATUS_ANSWERED
from tests.conftest import JOURNEY_HOST

H = {"Host": JOURNEY_HOST}
SECRET = "My wife left in March and I have not told anybody at church."


def journey(db):
    return db.session.scalar(db.select(Church).where(Church.slug == "journey"))


@pytest.fixture
def person_record(db):
    """The roster record behind the member login. Nobody is signed in.

    Separate from `alicia` because the `db` fixture holds one application
    context and Flask-Login caches the signed-in user on it, so a test cannot
    be the member and then a pastor.
    """
    user = db.session.scalar(db.select(User).where(User.email == "member@journeychurchsemo.com"))
    person = Person(church_id=user.church_id, first_name="Alicia", last_name="Romero",
                    email=user.email, stage="member", approved_at=utcnow())
    db.session.add(person)
    db.session.flush()
    user.person_id = person.id
    db.session.commit()
    return person


@pytest.fixture
def alicia(db, client, sign_in, person_record):
    """That record, with her signed in."""
    sign_in("member@journeychurchsemo.com")
    return person_record


def ask(client, message=SECRET, kind="talk", contact="either"):
    return client.post("/me/support/",
                       data={"message": message, "kind": kind, "contact_pref": contact},
                       headers=H, follow_redirects=True)


def a_request(db, person, **fields):
    """Filed directly, for the staff-side tests."""
    data = {"kind": "talk", "message": SECRET, "contact_pref": "either"}
    data.update(fields)
    row = SupportRequest(church_id=person.church_id, person_id=person.id, **data)
    db.session.add(row)
    db.session.commit()
    return row


class TestTheButtonAndTheForm:
    def test_the_button_is_on_home(self, db, client, alicia):
        page = client.get("/me/", headers=H).data
        assert b"Request pastoral support" in page
        assert b'href="/me/support/"' in page

    def test_the_form_says_who_reads_it(self, db, client, alicia):
        page = client.get("/me/support/", headers=H).data.decode()
        assert "goes to the pastoral staff at The Journey Church" in page
        assert "Seen by: the pastoral staff." in page

    def test_the_emergency_numbers_come_before_the_box(self, db, client, alicia):
        """Somebody in real trouble should see these before they type, not
        after they send and wait."""
        page = client.get("/me/support/", headers=H).data.decode()
        assert "call 911" in page
        assert "988" in page
        assert page.index("988") < page.index('name="message"')

    def test_every_kind_is_offered(self, db, client, alicia):
        page = client.get("/me/support/", headers=H).data.decode()
        for label in ("Something urgent", "A visit", "A conversation", "Prayer"):
            assert label in page

    def test_a_login_with_no_record_is_told_why_not(self, db, client, sign_in):
        sign_in("pastor@journeychurchsemo.com")
        response = client.get("/me/support/", headers=H, follow_redirects=True)
        assert b"Email the church office" in response.data

    def test_signed_out_cannot_reach_it(self, db, client):
        response = client.get("/me/support/", headers=H)
        assert response.status_code in (302, 401)


class TestSendingOne:
    def test_it_is_filed(self, db, client, alicia):
        response = ask(client, kind=KIND_PRAYER, contact="phone")
        assert b"A pastor will be in touch." in response.data

        row = db.session.scalar(db.select(SupportRequest))
        assert row.person_id == alicia.id
        assert row.church_id == alicia.church_id
        assert row.message == SECRET
        assert row.kind == KIND_PRAYER
        assert row.contact_pref == "phone"
        assert row.is_open is True

    def test_it_lands_on_their_record(self, db, client, alicia):
        ask(client)
        events = db.session.scalars(
            PersonEvent.for_person(alicia.church_id, alicia.id)
        ).all()
        assert any("Asked for pastoral support" in e.summary for e in events)

    def test_what_they_wrote_is_not_in_the_timeline(self, db, client, alicia):
        """The words live in one place, so removing them removes them."""
        ask(client)
        events = db.session.scalars(
            PersonEvent.for_person(alicia.church_id, alicia.id)
        ).all()
        assert all(SECRET not in (e.detail or "") for e in events)

    def test_an_empty_message_is_refused(self, db, client, alicia):
        response = ask(client, message="   ")
        assert b"even one line" in response.data
        assert db.session.scalars(db.select(SupportRequest)).all() == []

    def test_a_kind_that_does_not_exist_falls_back(self, db, client, alicia):
        ask(client, kind="<script>")
        row = db.session.scalar(db.select(SupportRequest))
        assert row.kind == "talk"

    def test_a_very_long_message_is_cut_not_refused(self, db, client, alicia):
        ask(client, message="x" * 9000)
        row = db.session.scalar(db.select(SupportRequest))
        assert len(row.message) == 4000

    def test_what_they_type_is_escaped_on_the_staff_screen(self, db, client,
                                                           person_record, sign_in):
        a_request(db, person_record, message="<script>alert(1)</script>")
        sign_in("pastor@journeychurchsemo.com")
        page = client.get(f"/people/{person_record.id}/", headers=H).data
        assert b"<script>alert(1)</script>" not in page
        assert b"&lt;script&gt;" in page


class TestAskingTwice:
    def test_a_second_ask_joins_the_first(self, db, client, alicia):
        """Two rows would have two pastors calling about one thing."""
        ask(client, message="First thing")
        response = ask(client, message="One more thing")
        assert b"Added." in response.data

        rows = db.session.scalars(db.select(SupportRequest)).all()
        assert len(rows) == 1
        assert "First thing" in rows[0].message
        assert "One more thing" in rows[0].message

    def test_the_form_says_they_already_asked(self, db, client, alicia):
        ask(client)
        page = client.get("/me/support/", headers=H).data
        assert b"You already asked" in page

    def test_a_new_ask_after_one_is_dealt_with_is_a_new_request(self, db, client, alicia):
        ask(client, message="First thing")
        row = db.session.scalar(db.select(SupportRequest))
        row.answer(None)
        db.session.commit()

        ask(client, message="Something new")
        rows = db.session.scalars(db.select(SupportRequest)).all()
        assert len(rows) == 2


class TestWhatStaffAreTold:
    def test_they_get_an_email(self, db, client, alicia):
        ask(client)
        mail = db.session.scalars(
            db.select(OutboxMessage).where(OutboxMessage.category == "pastoral")
        ).all()
        assert mail
        assert all("asked for pastoral support" in m.subject for m in mail)

    def test_the_email_never_carries_what_they_wrote(self, db, client, alicia):
        """A copy of somebody's worst week sitting in forty inboxes cannot be
        taken back."""
        ask(client)
        for message in db.session.scalars(db.select(OutboxMessage)).all():
            assert SECRET not in (message.body_text or "")
            assert SECRET not in (message.subject or "")

    def test_the_email_links_into_the_app(self, db, client, alicia):
        ask(client)
        body = db.session.scalar(db.select(OutboxMessage)).body_text
        assert f"/people/{alicia.id}/" in body

    def test_it_cannot_be_opted_out_of(self, db):
        """A church does not get to not be told somebody asked for help."""
        from app.categories import is_transactional

        assert is_transactional("pastoral") is True

    def test_only_staff_are_emailed(self, db, client, alicia):
        """Not leaders, and not the rest of the church."""
        ask(client)
        recipients = {
            m.to_email for m in db.session.scalars(
                db.select(OutboxMessage).where(OutboxMessage.category == "pastoral"))
        }
        staff = {
            u.email for u in db.session.scalars(
                db.select(User).where(User.church_id == alicia.church_id,
                                      User.role == "staff"))
        }
        assert recipients and recipients <= staff
        assert "leader@journeychurchsemo.com" not in recipients


class TestOnTheDashboard:
    # Staff views: they take the record, not the signed-in member.
    def test_an_open_request_is_listed(self, db, person_record, client, sign_in):
        a_request(db, person_record)
        sign_in("pastor@journeychurchsemo.com")
        page = client.get("/", headers=H).data.decode()
        card = page[page.index("Asked for a pastor"):]
        assert "Alicia Romero" in card
        assert "A conversation" in card

    def test_it_links_to_their_profile(self, db, person_record, client, sign_in):
        a_request(db, person_record)
        sign_in("pastor@journeychurchsemo.com")
        page = client.get("/", headers=H).data.decode()
        assert f'href="/people/{person_record.id}/#support"' in page

    def test_only_the_first_line_is_shown(self, db, person_record, client, sign_in):
        """A dashboard is read over shoulders in an office."""
        a_request(db, person_record, message="Line one.\nSomething much more private.")
        sign_in("pastor@journeychurchsemo.com")
        page = client.get("/", headers=H).data.decode()
        assert "Line one." in page
        assert "Something much more private." not in page

    def test_urgent_comes_first(self, db, person_record, client, sign_in):
        church = journey(db)
        other = Person(church_id=church.id, first_name="Ben", last_name="Carter",
                       stage="member")
        db.session.add(other)
        db.session.commit()
        a_request(db, person_record, message="Ordinary ask")
        a_request(db, other, kind=KIND_URGENT, message="Urgent ask")

        listed = db.session.scalars(SupportRequest.open_for_church(church.id)).all()
        assert listed[0].kind == KIND_URGENT

    def test_the_build_carries_the_count(self, db, person_record):
        a_request(db, person_record)
        data = dashboard.build(journey(db))
        assert data["support_count"] == 1
        assert len(data["support_open"]) == 1

    def test_an_answered_one_drops_off(self, db, person_record, client, sign_in):
        row = a_request(db, person_record)
        row.answer(None)
        db.session.commit()
        sign_in("pastor@journeychurchsemo.com")
        page = client.get("/", headers=H).data.decode()
        assert "Asked for a pastor" not in page

    def test_the_card_still_reads_when_nobody_asked(self, db, client, sign_in):
        sign_in("pastor@journeychurchsemo.com")
        page = client.get("/", headers=H).data
        assert b"Nobody is stuck" in page or b"Needs a person" in page

    def test_a_member_never_sees_the_dashboard(self, db, client, alicia):
        response = client.get("/", headers=H, follow_redirects=False)
        assert response.status_code == 302
        assert "/me/" in response.headers["Location"]


class TestOnTheirProfile:
    @pytest.fixture
    def staff(self, db, person_record, client, sign_in):
        sign_in("pastor@journeychurchsemo.com")
        return client

    def test_the_full_message_is_there(self, db, person_record, staff):
        a_request(db, person_record)
        page = staff.get(f"/people/{person_record.id}/", headers=H).data.decode()
        assert SECRET in page
        assert 'id="support"' in page

    def test_it_says_what_it_is_and_how_to_reach_them(self, db, person_record, staff):
        a_request(db, person_record, kind="visit", contact_pref="phone")
        page = staff.get(f"/people/{person_record.id}/", headers=H).data.decode()
        assert "A visit" in page
        assert "a phone call" in page.lower()

    def test_marking_it_dealt_with(self, db, person_record, staff):
        row = a_request(db, person_record)
        response = staff.post(f"/people/support/{row.id}/answered/", headers=H,
                              follow_redirects=True)
        assert b"Marked as dealt with." in response.data

        db.session.refresh(row)
        assert row.status == STATUS_ANSWERED
        assert row.answered_by_name == "Pastor Reed"
        assert row.answered_at is not None

    def test_who_dealt_with_it_is_on_the_screen(self, db, person_record, staff):
        row = a_request(db, person_record)
        staff.post(f"/people/support/{row.id}/answered/", headers=H)
        page = staff.get(f"/people/{person_record.id}/", headers=H).data
        assert b"Pastor Reed marked this dealt with" in page

    def test_answering_is_logged_on_their_record(self, db, person_record, staff):
        row = a_request(db, person_record)
        staff.post(f"/people/support/{row.id}/answered/", headers=H)
        events = db.session.scalars(
            PersonEvent.for_person(person_record.church_id, person_record.id)
        ).all()
        assert any("Answered their pastoral support" in e.summary for e in events)

    def test_answering_twice_says_who_got_there_first(self, db, person_record, staff):
        row = a_request(db, person_record)
        staff.post(f"/people/support/{row.id}/answered/", headers=H)
        response = staff.post(f"/people/support/{row.id}/answered/", headers=H,
                              follow_redirects=True)
        assert b"already marked this dealt with" in response.data

    def test_it_is_never_deleted(self, db, person_record, staff):
        """The record of who asked and who replied is what a church needs if
        it is ever asked whether somebody was looked after."""
        row = a_request(db, person_record)
        staff.post(f"/people/support/{row.id}/answered/", headers=H)
        assert db.session.get(SupportRequest, row.id) is not None
        page = staff.get(f"/people/{person_record.id}/", headers=H).data.decode()
        assert SECRET in page

    def test_reopening_one(self, db, person_record, staff):
        row = a_request(db, person_record)
        staff.post(f"/people/support/{row.id}/answered/", headers=H)
        response = staff.post(f"/people/support/{row.id}/reopen/", headers=H,
                              follow_redirects=True)
        assert b"Back on the dashboard." in response.data
        db.session.refresh(row)
        assert row.is_open is True
        assert row.answered_by_name is None

    def test_no_card_when_they_never_asked(self, db, person_record, staff):
        page = staff.get(f"/people/{person_record.id}/", headers=H).data
        assert b'id="support"' not in page


class TestTheOpenList:
    def test_it_lists_what_is_waiting(self, db, person_record, client, sign_in):
        a_request(db, person_record)
        sign_in("pastor@journeychurchsemo.com")
        page = client.get("/people/support/", headers=H).data
        assert b"Alicia Romero" in page

    def test_it_says_so_when_nothing_is_waiting(self, db, client, sign_in):
        sign_in("pastor@journeychurchsemo.com")
        page = client.get("/people/support/", headers=H).data
        assert b"Nobody is waiting on a pastor right now." in page

    def test_answered_ones_are_not_in_the_waiting_list(self, db, person_record,
                                                       client, sign_in):
        row = a_request(db, person_record)
        row.answer(None)
        db.session.commit()
        sign_in("pastor@journeychurchsemo.com")
        page = client.get("/people/support/", headers=H).data.decode()
        waiting = page[page.index("Waiting"):page.index('id="done"')]
        assert "Alicia Romero" not in waiting
        assert "Nobody is waiting on a pastor right now." in waiting


class TestWhoCanSeeIt:
    def test_a_member_cannot_open_the_list(self, db, client, alicia):
        assert client.get("/people/support/", headers=H).status_code == 403

    def test_a_member_cannot_mark_one_dealt_with(self, db, client, alicia):
        row = a_request(db, alicia)
        assert client.post(f"/people/support/{row.id}/answered/",
                           headers=H).status_code == 403
        db.session.refresh(row)
        assert row.is_open is True

    def test_another_churchs_request_is_a_404(self, db, client, sign_in):
        other = db.session.scalar(db.select(Church).where(Church.slug == "riverbend"))
        theirs = Person(church_id=other.id, first_name="Someone", last_name="Else",
                        stage="member")
        db.session.add(theirs)
        db.session.flush()
        row = SupportRequest(church_id=other.id, person_id=theirs.id, kind="talk",
                             message="Private to them", contact_pref="either")
        db.session.add(row)
        db.session.commit()

        sign_in("pastor@journeychurchsemo.com")
        assert client.post(f"/people/support/{row.id}/answered/",
                           headers=H).status_code == 404

    def test_another_churchs_request_is_not_on_this_dashboard(self, db, client, sign_in):
        other = db.session.scalar(db.select(Church).where(Church.slug == "riverbend"))
        theirs = Person(church_id=other.id, first_name="Someone", last_name="Else",
                        stage="member")
        db.session.add(theirs)
        db.session.flush()
        db.session.add(SupportRequest(church_id=other.id, person_id=theirs.id,
                                      kind="talk", message="Private to them",
                                      contact_pref="either"))
        db.session.commit()

        sign_in("pastor@journeychurchsemo.com")
        page = client.get("/", headers=H).data
        assert b"Private to them" not in page
        assert dashboard.build(journey(db))["support_count"] == 0


class TestDeletingThePerson:
    def test_the_rows_are_set_to_go_with_them(self):
        """A request without a person is a message from nobody. Enforced by
        the database rather than the ORM, and exercised against a real
        Postgres, since SQLite does not turn foreign keys on by default."""
        column = SupportRequest.__table__.c.person_id
        [fk] = list(column.foreign_keys)
        assert fk.ondelete == "CASCADE"


class TestHowLongTheyHaveWaited:
    """Both sides of the sum have to be the same clock. Comparing the stored
    UTC date against the church's local date printed "Waiting -1 days" for
    anything asked late in the evening in Missouri."""

    def test_asked_just_now_is_zero_not_negative(self, db, person_record):
        church = journey(db)
        church.timezone = "America/Chicago"
        db.session.commit()
        row = a_request(db, person_record)
        assert row.days_waiting(church) == 0

    def test_a_week_ago(self, db, person_record):
        church = journey(db)
        row = a_request(db, person_record)
        row.created_at = utcnow() - timedelta(days=7)
        db.session.commit()
        assert row.days_waiting(church) == 7

    def test_it_never_goes_negative(self, db, person_record):
        """A clock skew is not something to print at a pastor."""
        church = journey(db)
        row = a_request(db, person_record)
        row.created_at = utcnow() + timedelta(days=2)
        db.session.commit()
        assert row.days_waiting(church) == 0

    def test_the_dashboard_never_says_a_negative_number(self, db, person_record,
                                                        client, sign_in):
        church = journey(db)
        church.timezone = "Pacific/Honolulu"  # well behind UTC
        db.session.commit()
        a_request(db, person_record)
        sign_in("pastor@journeychurchsemo.com")
        page = client.get("/", headers=H).data.decode()
        assert "-1 days" not in page
        assert "Asked today" in page


class TestPreviousRequests:
    """What was asked before. A church asked in March what somebody asked for
    last spring is a question the log has to be able to answer."""

    @pytest.fixture
    def staff(self, db, person_record, client, sign_in):
        sign_in("pastor@journeychurchsemo.com")
        return client

    def history(self, staff):
        page = staff.get("/people/support/", headers=H).data.decode()
        return page[page.index('id="done"'):]

    def test_an_answered_request_is_kept_and_listed(self, db, person_record, staff):
        row = a_request(db, person_record)
        staff.post(f"/people/support/{row.id}/answered/", headers=H)
        done = self.history(staff)
        assert "Alicia Romero" in done
        assert "Dealt with by Pastor Reed" in done

    def test_it_says_when_it_was_dealt_with(self, db, person_record, staff):
        row = a_request(db, person_record)
        staff.post(f"/people/support/{row.id}/answered/", headers=H)
        assert str(utcnow().year) in self.history(staff)

    def test_only_the_first_line_shows_here_too(self, db, person_record, staff):
        row = a_request(db, person_record,
                        message="Line one.\nSomething much more private.")
        staff.post(f"/people/support/{row.id}/answered/", headers=H)
        done = self.history(staff)
        assert "Line one." in done
        assert "Something much more private." not in done

    def test_the_whole_thing_is_still_on_their_page(self, db, person_record, staff):
        row = a_request(db, person_record)
        staff.post(f"/people/support/{row.id}/answered/", headers=H)
        page = staff.get(f"/people/{person_record.id}/", headers=H).data.decode()
        assert SECRET in page

    def test_nothing_answered_yet_says_so(self, db, person_record, staff):
        assert "Nothing has been marked dealt with yet." in self.history(staff)

    def test_open_ones_are_not_in_the_history(self, db, person_record, staff):
        a_request(db, person_record)
        assert "Alicia Romero" not in self.history(staff)

    def test_most_recently_answered_first(self, db, person_record, staff):
        church = journey(db)
        other = Person(church_id=church.id, first_name="Ben", last_name="Carter",
                       stage="member")
        db.session.add(other)
        db.session.commit()

        first = a_request(db, person_record, message="Older ask")
        second = a_request(db, other, message="Newer ask")
        staff.post(f"/people/support/{first.id}/answered/", headers=H)
        db.session.refresh(first)
        first.answered_at = utcnow() - timedelta(days=30)
        db.session.commit()
        staff.post(f"/people/support/{second.id}/answered/", headers=H)

        done = self.history(staff)
        assert done.index("Ben Carter") < done.index("Alicia Romero")

    def test_another_churchs_history_is_not_here(self, db, person_record, staff):
        other = db.session.scalar(db.select(Church).where(Church.slug == "riverbend"))
        theirs = Person(church_id=other.id, first_name="Someone", last_name="Else",
                        stage="member")
        db.session.add(theirs)
        db.session.flush()
        row = SupportRequest(church_id=other.id, person_id=theirs.id, kind="talk",
                             message="Private to them", contact_pref="either")
        row.answer(None)
        db.session.add(row)
        db.session.commit()
        assert "Private to them" not in self.history(staff)

    def test_a_member_cannot_read_the_history(self, db, client, alicia):
        assert client.get("/people/support/", headers=H).status_code == 403


class TestTheButtonOnTheDashboard:
    def test_it_is_there_when_somebody_is_waiting(self, db, person_record, client,
                                                  sign_in):
        a_request(db, person_record)
        sign_in("pastor@journeychurchsemo.com")
        page = client.get("/", headers=H).data.decode()
        assert "Previous requests" in page
        assert 'href="/people/support/#done"' in page

    def test_it_is_there_when_nobody_is_waiting(self, db, client, sign_in):
        """A quiet week is exactly when somebody goes looking for what was
        asked last month."""
        sign_in("pastor@journeychurchsemo.com")
        page = client.get("/", headers=H).data.decode()
        assert "Previous requests" in page
        assert 'href="/people/support/#done"' in page

    def test_it_lands_on_the_history(self, db, person_record, client, sign_in):
        row = a_request(db, person_record)
        row.answer(None)
        db.session.commit()
        sign_in("pastor@journeychurchsemo.com")
        page = client.get("/people/support/", headers=H).data.decode()
        assert 'id="done"' in page
        assert page.index('id="done"') > page.index("Waiting")


def a_church(db):
    from app.models import Church

    return db.session.scalar(db.select(Church).where(Church.slug == "journey"))


class TestWhoHearsAboutARequest:
    """A church setting, not a hardcoded list.

    The people who need telling are often not the people with staff accounts:
    a care team lead, somebody from outside the church helping, a pastor who
    never signs in. And the list changes, which must not need a deploy.
    """

    def addresses(self, db):
        from app.models import OutboxMessage

        return sorted(
            m.to_email for m in db.session.scalars(
                db.select(OutboxMessage).where(OutboxMessage.category == "pastoral")
            )
        )

    def test_a_named_list_is_used(self, db, client, alicia):
        a_church(db).pastoral_alert_emails = (
            "care@example.com\nsecond@example.com\nthird@example.com"
        )
        db.session.commit()
        ask(client)
        assert self.addresses(db) == [
            "care@example.com", "second@example.com", "third@example.com",
        ]

    def test_somebody_outside_the_staff_list_can_be_on_it(self, db, client, alicia):
        """The point of the setting. Isaac is not a Journey staff account."""
        a_church(db).pastoral_alert_emails = "outsider@elsewhere.org"
        db.session.commit()
        ask(client)
        assert self.addresses(db) == ["outsider@elsewhere.org"]

    def test_an_empty_list_falls_back_to_staff(self, db, client, alicia):
        """The right default for a church that has not set one: the people who
        can already read the request are the people told it exists."""
        a_church(db).pastoral_alert_emails = None
        db.session.commit()
        ask(client)
        assert self.addresses(db)
        assert all("@" in address for address in self.addresses(db))

    def test_the_list_is_cleaned_up(self, db, client, alicia):
        """Trailing spaces, blank lines, commas and a repeat. A box a human
        types into gets all of those."""
        a_church(db).pastoral_alert_emails = (
            "  Care@Example.com  \n\n, second@example.com,\ncare@example.com\n"
        )
        db.session.commit()
        ask(client)
        assert self.addresses(db) == ["care@example.com", "second@example.com"]

    def test_the_message_is_still_not_in_the_email(self, db, client, alicia):
        """Who it goes to changed. What it says did not, and will not: a copy
        of what somebody wrote, in three inboxes, cannot be taken back."""
        from app.models import OutboxMessage

        a_church(db).pastoral_alert_emails = "care@example.com"
        db.session.commit()
        ask(client, message="My marriage is falling apart.")
        queued = db.session.scalars(
            db.select(OutboxMessage).where(OutboxMessage.category == "pastoral")
        ).all()
        assert queued
        for message in queued:
            assert "marriage" not in message.body_text.lower()

    def test_each_address_is_told_once(self, db, client, alicia):
        a_church(db).pastoral_alert_emails = "care@example.com"
        db.session.commit()
        ask(client)
        assert len(self.addresses(db)) == 1


class TestChangingTheList:
    def test_staff_can_save_it(self, db, staff):
        staff.post("/settings/pastoral/",
                   data={"emails": "a@example.com\nb@example.com"},
                   headers={"Host": JOURNEY_HOST})
        church = a_church(db)
        db.session.refresh(church)
        assert church.pastoral_recipients == ["a@example.com", "b@example.com"]

    def test_clearing_it_goes_back_to_staff(self, db, staff):
        a_church(db).pastoral_alert_emails = "a@example.com"
        db.session.commit()
        staff.post("/settings/pastoral/", data={"emails": "  "},
                   headers={"Host": JOURNEY_HOST})
        church = a_church(db)
        db.session.refresh(church)
        assert church.pastoral_recipients == []

    def test_the_change_is_logged_with_the_addresses(self, db, staff):
        """"Who could see that somebody asked for help last March" is a
        question the log has to answer."""
        from app.models import AuditEvent

        staff.post("/settings/pastoral/", data={"emails": "care@example.com"},
                   headers={"Host": JOURNEY_HOST})
        event = db.session.scalar(
            db.select(AuditEvent).order_by(AuditEvent.id.desc())
        )
        assert "Pastoral alerts now go to" in event.summary
        assert "care@example.com" in event.detail

    def test_a_leader_cannot_change_it(self, db, leader):
        assert leader.post("/settings/pastoral/", data={"emails": "x@example.com"},
                           headers={"Host": JOURNEY_HOST}).status_code == 403

    def test_the_row_is_on_the_settings_screen(self, db, staff):
        page = staff.get("/settings/", headers={"Host": JOURNEY_HOST}).data.decode()
        assert "Pastoral requests go to" in page
