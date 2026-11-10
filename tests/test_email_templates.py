"""Saved emails: write it once, send it by hand every time after.

A church that turns the welcome series off is not sending fewer emails, it is
sending the same emails by hand. The thing that makes that survive past week
three is not having to type "Thanks for being with us on Sunday" from scratch
for the fortieth person.

Two rules carry the feature:

- The person's own name comes back out when it is saved and goes back in when
  it is loaded, so nobody is greeted by somebody else's name.
- It is filled when it is loaded, not when it is sent, so the words staff read
  in the box are the words that arrive.
"""

import pytest

from app.models import Church, EmailTemplate, OutboxMessage, Person
from app.models.email_template import fill
from tests.conftest import JOURNEY_HOST

H = {"Host": JOURNEY_HOST}

# The select itself. Not the `data-template-pick` attribute the script looks
# for: that string is in the script on every render, picker or no picker.
PICKER = 'id="tplpick"'


@pytest.fixture
def journey(db):
    return db.session.scalar(db.select(Church).where(Church.slug == "journey"))


def add_person(db, church, first, last="Vance", email=None):
    person = Person(
        church_id=church.id, first_name=first, last_name=last,
        email=email or f"{first.lower()}@example.com", stage="visitor",
    )
    db.session.add(person)
    db.session.commit()
    return person


@pytest.fixture
def amy(db, journey):
    return add_person(db, journey, "Amy")


@pytest.fixture
def rob(db, journey):
    return add_person(db, journey, "Rob")


def send(client, person, subject, body, name="", category="welcome", **extra):
    data = {"category": category, "subject": subject, "message_body": body,
            "template_name": name}
    data.update(extra)
    return client.post(f"/people/{person.id}/email/", data=data,
                       headers=H, follow_redirects=True)


def templates(db, church):
    return db.session.scalars(EmailTemplate.for_church(church.id)).all()


class TestSavingIsNamingIt:
    def test_naming_an_email_keeps_it(self, db, journey, amy, staff):
        send(staff, amy, "Glad you were here", "Hello Amy,", name="First visit")
        saved = templates(db, journey)
        assert [t.name for t in saved] == ["First visit"]

    def test_leaving_the_name_blank_keeps_nothing(self, db, journey, amy, staff):
        """Most sends are one-offs. Saving every one of them would leave the
        picker full of emails nobody will send again."""
        send(staff, amy, "Glad you were here", "Hello Amy,")
        assert templates(db, journey) == []

    def test_whitespace_is_not_a_name(self, db, journey, amy, staff):
        send(staff, amy, "Subject", "Body", name="   ")
        assert templates(db, journey) == []

    def test_the_email_is_still_queued(self, db, journey, amy, staff):
        """Saving is a side effect of sending, not an alternative to it."""
        send(staff, amy, "Glad you were here", "Hello Amy,", name="First visit")
        queued = db.session.scalars(
            OutboxMessage.for_person(journey.id, amy.id)
        ).all()
        assert len(queued) == 1

    def test_what_was_sent_keeps_the_real_name(self, db, journey, amy, staff):
        """Only the stored copy is generalised. Amy's own email says Amy."""
        send(staff, amy, "Glad you were here, Amy", "Hello Amy,", name="First visit")
        message = db.session.scalars(
            OutboxMessage.for_person(journey.id, amy.id)
        ).all()[0]
        assert "Amy" in message.subject
        assert "{first_name}" not in message.subject

    def test_the_category_is_saved_with_it(self, db, journey, amy, staff):
        """Picking a saved email should set the whole send, not most of it."""
        send(staff, amy, "Subject", "Body", name="First visit",
             category="next_step")
        assert templates(db, journey)[0].category == "next_step"

    def test_who_wrote_it_is_recorded(self, db, journey, amy, staff):
        send(staff, amy, "Subject", "Body", name="First visit")
        assert templates(db, journey)[0].created_by_id is not None


class TestTheNameComesBackOut:
    def test_out_of_the_body(self, db, journey, amy, staff):
        send(staff, amy, "Subject", "Hello Amy, good to meet you.",
             name="First visit")
        assert templates(db, journey)[0].body == \
            "Hello {first_name}, good to meet you."

    def test_and_out_of_the_subject(self, db, journey, amy, staff):
        """The bug this was written for: the body was generalised and the
        subject was not, so a template saved from an email to Amy greeted Rob
        as Amy in the one line he reads before opening it."""
        send(staff, amy, "Glad you were with us, Amy", "Hello Amy,",
             name="First visit")
        saved = templates(db, journey)[0]
        assert saved.subject == "Glad you were with us, {first_name}"

    def test_a_very_short_name_is_left_alone(self, db, journey, staff):
        """A church with an Al or a Jo would otherwise find the letters cut
        out of the middle of unrelated words."""
        al = add_person(db, journey, "Al")
        send(staff, al, "Subject", "Always glad to see you, Al.",
             name="First visit")
        assert templates(db, journey)[0].body == "Always glad to see you, Al."

    def test_the_church_name_is_left_alone(self, db, journey, amy, staff):
        """Swapping it out would rewrite sentences that meant to say it."""
        send(staff, amy, "Subject", "Thanks for being at The Journey Church.",
             name="First visit")
        assert "The Journey Church" in templates(db, journey)[0].body


class TestUsingOne:
    def test_the_picker_offers_it(self, db, journey, amy, rob, staff):
        send(staff, amy, "Subject", "Hello Amy,", name="First visit")
        body = staff.get(f"/people/{rob.id}/", headers=H).data.decode()
        assert "First visit" in body

    def test_nothing_is_offered_before_anything_is_saved(
        self, db, journey, rob, staff
    ):
        body = staff.get(f"/people/{rob.id}/", headers=H).data.decode()
        assert PICKER not in body

    def test_picking_one_fills_the_form_with_this_person(
        self, db, journey, amy, rob, staff
    ):
        send(staff, amy, "Glad you were with us, Amy", "Hello Amy,",
             name="First visit")
        saved = templates(db, journey)[0]

        body = staff.get(f"/people/{rob.id}/?template={saved.id}",
                         headers=H).data.decode()
        # The field values themselves, not just the page: the hint under the
        # box explains `{first_name}` and says it on every render.
        assert 'value="Glad you were with us, Rob"' in body
        assert ">Hello Rob,</textarea>" in body

    def test_the_name_is_filled_in_so_an_edit_saves_over_it(
        self, db, journey, amy, rob, staff
    ):
        """Opening a template, changing a line and sending is how somebody
        edits one. The name coming back prefilled is what makes that replace
        rather than duplicate."""
        send(staff, amy, "Subject", "Hello Amy,", name="First visit")
        saved = templates(db, journey)[0]
        body = staff.get(f"/people/{rob.id}/?template={saved.id}",
                         headers=H).data.decode()
        assert 'name="template_name"' in body
        assert 'value="First visit"' in body

    def test_a_template_from_another_church_fills_nothing(
        self, db, journey, rob, staff
    ):
        """Tenancy. Silently blank rather than a 404, because the id is in a
        link somebody may have had open since before it was archived."""
        riverbend = db.session.scalar(
            db.select(Church).where(Church.slug == "riverbend")
        )
        theirs = EmailTemplate(
            church_id=riverbend.id, name="Theirs", category="welcome",
            subject="Their subject", body="Their body",
        )
        db.session.add(theirs)
        db.session.commit()

        body = staff.get(f"/people/{rob.id}/?template={theirs.id}",
                         headers=H).data.decode()
        assert "Their subject" not in body

    def test_an_id_that_does_not_exist_fills_nothing(self, db, rob, staff):
        response = staff.get(f"/people/{rob.id}/?template=99999", headers=H)
        assert response.status_code == 200

    def test_sending_from_one_counts_as_a_use(self, db, journey, amy, rob, staff):
        """The picker puts the most used first, which is only true if
        something increments it."""
        send(staff, amy, "Subject", "Hello Amy,", name="First visit")
        saved = templates(db, journey)[0]
        assert saved.used_count == 0

        send(staff, rob, "Subject", "Hello Rob,", name="First visit",
             from_template=saved.id)
        db.session.refresh(saved)
        assert saved.used_count == 1
        assert saved.last_used_at is not None

    def test_the_ones_a_church_actually_sends_come_first(
        self, db, journey, amy, rob, staff
    ):
        """Alphabetical order puts the two emails a church sends every week
        wherever their first letters happen to fall."""
        send(staff, amy, "Subject", "Body", name="Zeta note")
        send(staff, amy, "Subject", "Body", name="Alpha note")
        workhorse = [t for t in templates(db, journey) if t.name == "Zeta note"][0]

        send(staff, rob, "Subject", "Body", name="Zeta note",
             from_template=workhorse.id)

        assert [t.name for t in templates(db, journey)] == \
            ["Zeta note", "Alpha note"]

    def test_a_use_count_cannot_be_borrowed_from_another_church(
        self, db, journey, rob, staff
    ):
        riverbend = db.session.scalar(
            db.select(Church).where(Church.slug == "riverbend")
        )
        theirs = EmailTemplate(
            church_id=riverbend.id, name="Theirs", category="welcome",
            subject="S", body="B",
        )
        db.session.add(theirs)
        db.session.commit()

        send(staff, rob, "Subject", "Body", from_template=theirs.id)
        db.session.refresh(theirs)
        assert theirs.used_count == 0


class TestSavingOverOne:
    def test_the_same_name_replaces_rather_than_duplicates(
        self, db, journey, amy, staff
    ):
        send(staff, amy, "First try", "Hello Amy,", name="First visit")
        send(staff, amy, "Second try", "Hi Amy,", name="First visit")

        saved = templates(db, journey)
        assert len(saved) == 1
        assert saved[0].subject == "Second try"

    def test_case_does_not_make_a_second_one(self, db, journey, amy, staff):
        send(staff, amy, "Subject", "Body", name="First visit")
        send(staff, amy, "Subject", "Body", name="first VISIT")
        assert len(templates(db, journey)) == 1

    def test_the_original_author_is_kept(self, db, journey, amy, staff):
        """Who wrote it, not who last touched it.

        Written with the first author put in directly rather than by signing
        a second person in. Two clients in one test share an application
        context, and Flask-Login caches the signed-in user on it, so the
        second one inherits the first one's identity: the test would pass
        because one person sent both emails.
        """
        from app.models import User

        dana = db.session.scalar(
            db.select(User).where(
                User.church_id == journey.id,
                User.email == "leader@journeychurchsemo.com",
            )
        )
        saved = EmailTemplate(
            church_id=journey.id, name="First visit", category="welcome",
            subject="S", body="B", created_by_id=dana.id,
        )
        db.session.add(saved)
        db.session.commit()

        send(staff, amy, "Subject", "Body two", name="First visit")
        db.session.refresh(saved)
        assert saved.created_by_id == dana.id

    def test_another_church_may_use_the_same_name(self, db, journey, amy, staff):
        """Names are per church, like everything else."""
        riverbend = db.session.scalar(
            db.select(Church).where(Church.slug == "riverbend")
        )
        db.session.add(EmailTemplate(
            church_id=riverbend.id, name="First visit", category="welcome",
            subject="Theirs", body="Theirs",
        ))
        db.session.commit()

        send(staff, amy, "Ours", "Ours", name="First visit")
        assert len(templates(db, journey)) == 1
        assert templates(db, journey)[0].subject == "Ours"


class TestRemovingOne:
    def test_it_leaves_the_picker(self, db, journey, amy, rob, staff):
        send(staff, amy, "Subject", "Hello Amy,", name="First visit")
        saved = templates(db, journey)[0]

        staff.post(f"/people/{amy.id}/templates/{saved.id}/delete/",
                   headers=H, follow_redirects=True)

        assert templates(db, journey) == []
        body = staff.get(f"/people/{rob.id}/", headers=H).data.decode()
        assert PICKER not in body

    def test_the_emails_already_sent_from_it_are_untouched(
        self, db, journey, amy, staff
    ):
        """A church asking what it sent new visitors last spring should not
        find the answer gone because somebody tidied the list."""
        send(staff, amy, "Glad you were here", "Hello Amy,", name="First visit")
        saved = templates(db, journey)[0]

        staff.post(f"/people/{amy.id}/templates/{saved.id}/delete/",
                   headers=H, follow_redirects=True)

        queued = db.session.scalars(
            OutboxMessage.for_person(journey.id, amy.id)
        ).all()
        assert len(queued) == 1
        assert queued[0].subject == "Glad you were here"

    def test_it_is_archived_rather_than_deleted(self, db, journey, amy, staff):
        send(staff, amy, "Subject", "Body", name="First visit")
        saved_id = templates(db, journey)[0].id

        staff.post(f"/people/{amy.id}/templates/{saved_id}/delete/",
                   headers=H, follow_redirects=True)

        row = db.session.get(EmailTemplate, saved_id)
        assert row is not None
        assert row.is_archived is True

    def test_saving_the_name_again_brings_it_back(self, db, journey, amy, staff):
        """Rather than leaving a church unable to use a name it already used
        and cannot see."""
        send(staff, amy, "Subject", "Body", name="First visit")
        saved_id = templates(db, journey)[0].id
        staff.post(f"/people/{amy.id}/templates/{saved_id}/delete/",
                   headers=H, follow_redirects=True)

        send(staff, amy, "Subject again", "Body", name="First visit")
        rows = templates(db, journey)
        assert [t.id for t in rows] == [saved_id]

    def test_another_church_cannot_remove_ours(self, db, journey, amy, staff):
        riverbend = db.session.scalar(
            db.select(Church).where(Church.slug == "riverbend")
        )
        theirs = EmailTemplate(
            church_id=riverbend.id, name="Theirs", category="welcome",
            subject="S", body="B",
        )
        db.session.add(theirs)
        db.session.commit()

        response = staff.post(
            f"/people/{amy.id}/templates/{theirs.id}/delete/", headers=H
        )
        assert response.status_code == 404
        db.session.refresh(theirs)
        assert theirs.is_archived is False

    def test_a_member_cannot_remove_one(self, db, journey, amy, member):
        """Saved directly rather than sent by staff first: signing two people
        in across one test leaves the second inheriting the first one's
        identity, and the member's request would run as the pastor."""
        saved = EmailTemplate(
            church_id=journey.id, name="First visit", category="welcome",
            subject="S", body="B",
        )
        db.session.add(saved)
        db.session.commit()

        response = member.post(
            f"/people/{amy.id}/templates/{saved.id}/delete/", headers=H
        )
        assert response.status_code in (302, 403)
        db.session.refresh(saved)
        assert saved.is_archived is False


class TestFillingIn:
    def test_every_placeholder_is_replaced(self, db, journey, amy):
        text = "{first_name} {last_name} {full_name} {church}"
        assert fill(text, amy, journey) == \
            f"Amy Vance {amy.full_name} The Journey Church"

    def test_a_brace_that_is_not_ours_survives(self, db, journey, amy):
        """Staff type these. "Bring {whatever you like}" is a sentence
        somebody might reasonably write, and `str.format` raises on it,
        losing the email at the moment they press send."""
        text = "Bring {whatever you like} and {first_name} will be there."
        assert fill(text, amy, journey) == \
            "Bring {whatever you like} and Amy will be there."

    def test_nothing_can_be_reached_through_a_placeholder(self, db, journey, amy):
        """A format string can read attributes of whatever it is given. This
        is literal replacement, so there is nothing to reach through."""
        text = "{person.church.id} {0} {}"
        assert fill(text, amy, journey) == text

    def test_a_missing_last_name_leaves_a_gap_not_a_none(self, db, journey):
        person = Person(
            church_id=journey.id, first_name="Mo", last_name="",
            email="mo@example.com", stage="visitor",
        )
        db.session.add(person)
        db.session.commit()
        assert "None" not in fill("Hi {first_name} {last_name}", person, journey)

    def test_empty_text_is_handled(self, db, journey, amy):
        assert fill("", amy, journey) == ""
        assert fill(None, amy, journey) is None
