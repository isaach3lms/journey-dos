"""Increment 6: resources, reader, progress.

The renderer tests carry the most weight. Everything a pastor types here is
displayed in a member's browser, so a mistake in `app/markup.py` is stored XSS
inside a church's own app.
"""

import pytest

from app.markup import plain, render
from app.models import (
    STATUS_ARCHIVED,
    STATUS_DRAFT,
    STATUS_PUBLISHED,
    Church,
    Person,
    Resource,
    ResourceSession,
    SessionCompletion,
    User,
)
from tests.conftest import JOURNEY_HOST

MEMBER_EMAIL = "member@journeychurchsemo.com"


@pytest.fixture
def plan(db):
    church = db.session.scalar(db.select(Church).where(Church.slug == "journey"))
    resource = Resource(
        church_id=church.id,
        title="Known: a five day plan",
        summary="Five short readings.",
        kind="reading_plan",
    )
    db.session.add(resource)
    db.session.flush()

    for i in range(1, 6):
        db.session.add(
            ResourceSession(
                church_id=church.id,
                resource_id=resource.id,
                position=i,
                title=f"Day {i}",
                passage_ref=f"Psalm 139:{i}",
                body=f"Read this on day {i}.",
                question="Where have you been trying to be known?",
            )
        )
    db.session.commit()
    return resource


@pytest.fixture
def reader(db):
    """The member login, linked to a roster record."""
    church = db.session.scalar(db.select(Church).where(Church.slug == "journey"))
    person = Person(
        church_id=church.id, first_name="Alicia", last_name="Romero",
        email=MEMBER_EMAIL, stage="attender",
    )
    db.session.add(person)
    db.session.flush()
    user = db.session.scalar(
        db.select(User).where(User.email == MEMBER_EMAIL, User.church_id == church.id)
    )
    user.person_id = person.id
    db.session.commit()
    return person


class TestRenderer:
    """Escape first, then format. Never the other way round."""

    def test_a_script_tag_is_escaped(self):
        out = str(render("<script>alert(1)</script>"))
        assert "<script>" not in out
        assert "&lt;script&gt;" in out

    def test_an_event_handler_attribute_is_escaped(self):
        out = str(render('<img src=x onerror="alert(1)">'))
        assert "onerror=\"alert" not in out
        assert "&lt;img" in out

    def test_a_javascript_url_cannot_become_a_link(self):
        """There is no link syntax at all, so there is nothing to abuse."""
        out = str(render("[click](javascript:alert(1))"))
        assert "javascript:" not in out or "<a" not in out
        assert "<a " not in out

    def test_quotes_and_ampersands_are_escaped(self):
        out = str(render('He said "hello" & left'))
        assert "&amp;" in out
        assert "&#34;" in out or "&quot;" in out

    def test_bold_and_italic_work(self):
        out = str(render("Read **this** and *that*."))
        assert "<strong>this</strong>" in out
        assert "<em>that</em>" in out

    def test_a_heading(self):
        assert "<h3>Day 1</h3>" in str(render("# Day 1"))

    def test_a_blockquote_for_scripture(self):
        out = str(render("&gt; ignored\n\n> For God so loved\n> the world"))
        assert "<blockquote>" in out
        assert "For God so loved<br>the world" in out

    def test_a_list(self):
        out = str(render("- one\n- two"))
        assert out.count("<li>") == 2

    def test_paragraphs_split_on_blank_lines(self):
        assert str(render("One.\n\nTwo.")).count("<p>") == 2

    def test_formatting_cannot_be_smuggled_through_escaped_text(self):
        """Escaping runs first, so the bold pattern can only match author text."""
        out = str(render("**<b>bold</b>**"))
        assert "<strong>&lt;b&gt;bold&lt;/b&gt;</strong>" in out

    def test_empty_input(self):
        assert str(render(None)) == ""
        assert str(render("")) == ""

    def test_absurdly_long_input_is_truncated(self):
        out = str(render("a" * 50_000))
        assert len(out) < 25_000

    def test_plain_strips_formatting(self):
        assert "#" not in plain("# Heading\n\nSome **text**")

    def test_plain_truncates(self):
        assert len(plain("word " * 200, limit=40)) <= 40


class TestPublishing:
    def test_a_new_resource_is_a_draft(self, db, plan):
        assert plan.status == STATUS_DRAFT

    def test_an_empty_resource_cannot_be_published(self, db):
        church = db.session.scalar(db.select(Church).where(Church.slug == "journey"))
        empty = Resource(church_id=church.id, title="Nothing in it", kind="study")
        db.session.add(empty)
        db.session.commit()

        with pytest.raises(ValueError, match="no days in it"):
            empty.publish()

    def test_publishing_records_a_time(self, db, plan):
        plan.publish()
        db.session.commit()
        assert plan.is_published
        assert plan.published_at is not None

    def test_unpublishing_clears_it(self, db, plan):
        plan.publish()
        db.session.commit()
        plan.unpublish()
        db.session.commit()
        assert plan.status == STATUS_DRAFT
        assert plan.published_at is None

    def test_positions_are_unique_within_a_resource(self, db, plan):
        db.session.add(
            ResourceSession(
                church_id=plan.church_id, resource_id=plan.id, position=1, title="Clash"
            )
        )
        with pytest.raises(Exception):
            db.session.commit()
        db.session.rollback()

    def test_next_position_follows_the_last(self, db, plan):
        assert plan.next_position() == 6

    def test_deleting_a_resource_takes_its_sessions(self, db, plan):
        session_ids = [s.id for s in plan.sessions]
        db.session.delete(plan)
        db.session.commit()
        for session_id in session_ids:
            assert db.session.get(ResourceSession, session_id) is None


class TestProgress:
    def test_marking_a_session_done(self, db, plan, reader):
        session = plan.sessions[0]
        SessionCompletion.mark(plan.church_id, reader.id, session)
        db.session.commit()

        done = SessionCompletion.completed_session_ids(
            plan.church_id, reader.id, plan.id
        )
        assert done == {session.id}

    def test_marking_twice_records_once(self, db, plan, reader):
        """A double tap on a phone must not inflate every count."""
        session = plan.sessions[0]
        assert SessionCompletion.mark(plan.church_id, reader.id, session) is not None
        db.session.commit()
        assert SessionCompletion.mark(plan.church_id, reader.id, session) is None
        db.session.commit()

        assert len(
            SessionCompletion.completed_session_ids(plan.church_id, reader.id, plan.id)
        ) == 1

    def test_unmarking(self, db, plan, reader):
        session = plan.sessions[0]
        SessionCompletion.mark(plan.church_id, reader.id, session)
        db.session.commit()

        assert SessionCompletion.unmark(plan.church_id, reader.id, session.id) is True
        db.session.commit()
        assert SessionCompletion.completed_session_ids(
            plan.church_id, reader.id, plan.id
        ) == set()

    def test_unmarking_something_never_done_is_harmless(self, db, plan, reader):
        assert SessionCompletion.unmark(plan.church_id, reader.id, plan.sessions[0].id) is False

    def test_started_counts_are_distinct_people(self, db, plan, reader):
        for session in plan.sessions[:3]:
            SessionCompletion.mark(plan.church_id, reader.id, session)
        db.session.commit()

        counts = SessionCompletion.started_counts(plan.church_id)
        assert counts[plan.id] == 1

    def test_progress_is_per_person(self, db, plan, reader):
        other = Person(
            church_id=plan.church_id, first_name="Dana", last_name="Webb", stage="member"
        )
        db.session.add(other)
        db.session.commit()

        SessionCompletion.mark(plan.church_id, reader.id, plan.sessions[0])
        db.session.commit()

        assert SessionCompletion.completed_session_ids(
            plan.church_id, other.id, plan.id
        ) == set()

    def test_completions_survive_the_plan_being_edited(self, db, plan, reader):
        """A completion is a fact about a moment, not a flag on a join row."""
        session = plan.sessions[0]
        SessionCompletion.mark(plan.church_id, reader.id, session)
        db.session.commit()

        session.title = "Day 1, rewritten"
        session.body = "Different text entirely."
        db.session.commit()

        assert SessionCompletion.completed_session_ids(
            plan.church_id, reader.id, plan.id
        ) == {session.id}


class TestTenantIsolation:
    def test_a_resource_from_another_church_is_a_404(self, db, staff):
        riverbend = db.session.scalar(db.select(Church).where(Church.slug == "riverbend"))
        theirs = Resource(church_id=riverbend.id, title="Theirs", kind="study")
        db.session.add(theirs)
        db.session.commit()

        r = staff.get(f"/resources/{theirs.id}/", headers={"Host": JOURNEY_HOST})
        assert r.status_code == 404

    def test_the_list_only_shows_this_church(self, db, plan, staff):
        riverbend = db.session.scalar(db.select(Church).where(Church.slug == "riverbend"))
        db.session.add(Resource(church_id=riverbend.id, title="Theirs", kind="study"))
        db.session.commit()

        r = staff.get("/resources/", headers={"Host": JOURNEY_HOST})
        assert b"Known" in r.data
        assert b"Theirs" not in r.data

    def test_a_session_cannot_be_added_across_churches(self, db, staff):
        riverbend = db.session.scalar(db.select(Church).where(Church.slug == "riverbend"))
        theirs = Resource(church_id=riverbend.id, title="Theirs", kind="study")
        db.session.add(theirs)
        db.session.commit()

        r = staff.post(
            f"/resources/{theirs.id}/sessions/",
            data={"title": "Sneaked in"},
            headers={"Host": JOURNEY_HOST},
        )
        assert r.status_code == 404
        assert theirs.sessions == []


class TestStaffRoutes:
    def test_creating_a_resource(self, db, staff):
        staff.post(
            "/resources/",
            data={"title": "First plan", "kind": "reading_plan", "summary": "x"},
            headers={"Host": JOURNEY_HOST},
        )
        church = db.session.scalar(db.select(Church).where(Church.slug == "journey"))
        found = db.session.scalars(Resource.for_church(church.id)).all()
        assert [r.title for r in found] == ["First plan"]

    def test_an_unknown_kind_is_refused(self, staff):
        r = staff.post(
            "/resources/",
            data={"title": "x", "kind": "sermon-series"},
            headers={"Host": JOURNEY_HOST},
        )
        assert r.status_code == 400

    def test_adding_a_day(self, db, plan, staff):
        staff.post(
            f"/resources/{plan.id}/sessions/",
            data={"title": "Day 6", "passage_ref": "Psalm 139:7", "body": "More."},
            headers={"Host": JOURNEY_HOST},
        )
        db.session.refresh(plan)
        assert plan.session_count == 6
        assert plan.sessions[-1].position == 6

    def test_publishing_an_empty_plan_is_refused_with_a_message(self, db, staff):
        church = db.session.scalar(db.select(Church).where(Church.slug == "journey"))
        empty = Resource(church_id=church.id, title="Empty", kind="study")
        db.session.add(empty)
        db.session.commit()

        r = staff.post(
            f"/resources/{empty.id}/publish/",
            headers={"Host": JOURNEY_HOST},
            follow_redirects=True,
        )
        assert b"at least one day" in r.data
        db.session.refresh(empty)
        assert empty.status == STATUS_DRAFT

    def test_archiving_keeps_the_row(self, db, plan, staff):
        staff.post(f"/resources/{plan.id}/archive/", headers={"Host": JOURNEY_HOST})
        db.session.refresh(plan)
        assert plan.status == STATUS_ARCHIVED
        assert db.session.get(Resource, plan.id) is not None

    def test_a_member_cannot_author(self, plan, member):
        assert member.get("/resources/", headers={"Host": JOURNEY_HOST}).status_code == 403
        r = member.post(
            f"/resources/{plan.id}/sessions/",
            data={"title": "x"},
            headers={"Host": JOURNEY_HOST},
        )
        assert r.status_code == 403


class TestMemberReading:
    def test_a_draft_is_invisible(self, db, plan, reader, member):
        r = member.get("/me/read/", headers={"Host": JOURNEY_HOST})
        assert b"Known" not in r.data

    def test_guessing_a_draft_id_reveals_nothing(self, db, plan, reader, member):
        r = member.get(f"/me/read/{plan.id}/", headers={"Host": JOURNEY_HOST})
        assert r.status_code == 404

    def test_a_published_plan_appears(self, db, plan, reader, member):
        plan.publish()
        db.session.commit()
        r = member.get("/me/read/", headers={"Host": JOURNEY_HOST})
        assert b"Known" in r.data

    def test_reading_a_day(self, db, plan, reader, member):
        plan.publish()
        db.session.commit()
        session = plan.sessions[0]
        r = member.get(
            f"/me/read/{plan.id}/{session.id}/", headers={"Host": JOURNEY_HOST}
        )
        assert r.status_code == 200
        assert b"Read this on day 1" in r.data
        assert b"Psalm 139:1" in r.data

    def test_marking_done_moves_progress(self, db, plan, reader, member):
        plan.publish()
        db.session.commit()
        session = plan.sessions[0]

        member.post(
            f"/me/read/{plan.id}/{session.id}/done/", headers={"Host": JOURNEY_HOST}
        )
        r = member.get("/me/read/", headers={"Host": JOURNEY_HOST})
        assert b"Day 1 of 5" in r.data

    def test_undoing_a_completion(self, db, plan, reader, member):
        plan.publish()
        db.session.commit()
        session = plan.sessions[0]

        member.post(
            f"/me/read/{plan.id}/{session.id}/done/", headers={"Host": JOURNEY_HOST}
        )
        member.post(
            f"/me/read/{plan.id}/{session.id}/done/",
            data={"undo": "1"},
            headers={"Host": JOURNEY_HOST},
        )
        assert SessionCompletion.completed_session_ids(
            plan.church_id, reader.id, plan.id
        ) == set()

    def test_finishing_every_day_says_so(self, db, plan, reader, member):
        plan.publish()
        db.session.commit()
        for session in plan.sessions:
            member.post(
                f"/me/read/{plan.id}/{session.id}/done/", headers={"Host": JOURNEY_HOST}
            )
        r = member.get(f"/me/read/{plan.id}/", headers={"Host": JOURNEY_HOST})
        assert b"You finished it" in r.data

    def test_a_session_from_another_plan_is_refused(self, db, plan, reader, member):
        plan.publish()
        church = plan.church_id
        other = Resource(church_id=church, title="Other", kind="study")
        db.session.add(other)
        db.session.flush()
        stray = ResourceSession(
            church_id=church, resource_id=other.id, position=1, title="Stray"
        )
        db.session.add(stray)
        db.session.commit()

        r = member.get(
            f"/me/read/{plan.id}/{stray.id}/", headers={"Host": JOURNEY_HOST}
        )
        assert r.status_code == 404

    def test_authored_content_is_escaped_in_the_member_view(
        self, db, plan, reader, member
    ):
        """A leader account is one weak password from an outsider."""
        plan.sessions[0].body = "<script>alert('xss')</script>"
        plan.publish()
        db.session.commit()

        r = member.get(
            f"/me/read/{plan.id}/{plan.sessions[0].id}/",
            headers={"Host": JOURNEY_HOST},
        )
        assert b"<script>alert" not in r.data
        assert b"&lt;script&gt;" in r.data

    def test_a_signed_out_visitor_cannot_read(self, db, plan, client):
        plan.publish()
        db.session.commit()
        r = client.get("/me/read/", headers={"Host": JOURNEY_HOST})
        assert r.status_code == 302
        assert "/auth/login" in r.headers["Location"]
