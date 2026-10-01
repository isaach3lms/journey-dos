"""Printing a kids name tag, and reprinting one.

A name tag is not decoration. It carries the pickup code, and the pickup code
is the whole safety model: it is what somebody has to produce before a child
leaves a room. That makes a printed tag a credential, and it makes the screen
that prints a second one a screen that hands out a credential.

Three claims are tested here because getting any of them wrong is a child
safety problem rather than a layout problem:

1. **Printing never issues a new code.** The family is already carrying one.
   A reprint that generated a fresh code would leave the parent holding a code
   the desk no longer recognises, which is worse than losing the tag.
2. **Every reprint is logged, with a name.** Asking for a second copy of
   somebody else's pickup code is exactly what an attempt to take a child
   would look like from the outside.
3. **A tag carries nothing pastoral.** It is left on tables and handed to
   whoever is at the desk. A person's notes do not go on it.
"""

import pytest

from app.models import (
    AuditEvent,
    Checkin,
    CheckinSession,
    Church,
    Household,
    Person,
)
from app.models.audit import TAG_REPRINTED
from app.models.base import utcnow
from tests.conftest import JOURNEY_HOST, RIVERBEND_HOST

H = {"Host": JOURNEY_HOST}


@pytest.fixture
def journey(db):
    church = db.session.scalar(db.select(Church).where(Church.slug == "journey"))
    church.timezone = "America/Chicago"
    db.session.commit()
    return church


@pytest.fixture
def webbs(db, journey):
    household = Household(church_id=journey.id, name="Marcus and Dana Webb")
    db.session.add(household)
    db.session.flush()
    db.session.add_all([
        Person(church_id=journey.id, first_name="Marcus", last_name="Webb",
               email="marcus@example.com", stage="member",
               household_id=household.id),
        Person(church_id=journey.id, first_name="Ellie", last_name="Webb",
               stage="member", household_id=household.id, is_child=True,
               notes="Dad is not to collect her. Court order on file."),
        Person(church_id=journey.id, first_name="Nate", last_name="Webb",
               stage="member", household_id=household.id, is_child=True),
    ])
    household.ensure_checkin_pin()
    db.session.commit()
    return household


@pytest.fixture
def sunday(db, journey):
    row = CheckinSession(church_id=journey.id, name="Sunday 9:30",
                         starts_at=utcnow())
    db.session.add(row)
    db.session.commit()
    return row


def check_in(db, staff, webbs, sunday, names=("Ellie", "Nate")):
    """Put the children in a room through the kiosk, as a Sunday would."""
    people = [p for p in webbs.members if p.first_name in names]
    staff.post("/kids/kiosk/", data={"pin": webbs.checkin_pin}, headers=H,
               follow_redirects=True)
    page = staff.post(
        f"/kids/kiosk/family/{webbs.id}/",
        data={"person_id": [p.id for p in people]},
        headers=H,
    ).data.decode()
    db.session.refresh(sunday)
    return page


def codes(db, sunday):
    db.session.refresh(sunday)
    return {c.pickup_code for c in sunday.checkins}


class TestTheTagAtCheckIn:
    def test_the_tags_are_on_the_check_in_screen(self, db, staff, webbs, sunday):
        page = check_in(db, staff, webbs, sunday)
        assert "tagsheet" in page
        # Ellie, Nate, and the pickup tag for whoever brought them.
        assert page.count('class="nametag') == 3

    def test_each_child_gets_their_own_name(self, db, staff, webbs, sunday):
        page = check_in(db, staff, webbs, sunday)
        assert "Ellie" in page and "Nate" in page

    def test_there_is_a_pickup_tag_for_the_adult(self, db, staff, webbs, sunday):
        page = check_in(db, staff, webbs, sunday)
        assert "pickuptag" in page
        assert "For the adult who brought them" in page

    def test_one_pickup_tag_for_the_whole_family(self, db, staff, webbs, sunday):
        """Three identical stubs is how one gets lost and the others get
        handed to somebody else."""
        page = check_in(db, staff, webbs, sunday)
        assert page.count("pickuptag") == 1

    def test_the_pickup_code_is_on_the_tag(self, db, staff, webbs, sunday):
        check_in(db, staff, webbs, sunday)
        code = next(iter(codes(db, sunday)))
        page = check_in(db, staff, webbs, sunday, names=())
        # Re-render by reprinting: the code on paper is the stored one.
        page = staff.get(
            f"/kids/tags/{sunday.id}/family/{webbs.id}/", headers=H
        ).data.decode()
        assert code in page

    def test_there_is_a_print_button(self, db, staff, webbs, sunday):
        page = check_in(db, staff, webbs, sunday)
        assert "Print name tags" in page
        assert "data-print" in page

    def test_checking_in_does_not_log_a_reprint(self, db, staff, webbs, sunday):
        """The first print comes off this page, so the reprint log stays clean
        and a line in it always means somebody asked for a second copy."""
        check_in(db, staff, webbs, sunday)
        assert db.session.scalar(
            db.select(db.func.count(AuditEvent.id)).where(
                AuditEvent.action == TAG_REPRINTED)
        ) == 0


class TestReprintingTheFamily:
    def url(self, sunday, webbs):
        return f"/kids/tags/{sunday.id}/family/{webbs.id}/"

    def test_it_prints_every_child_who_was_checked_in(self, db, staff, webbs, sunday):
        check_in(db, staff, webbs, sunday)
        page = staff.get(self.url(sunday, webbs), headers=H).data.decode()
        assert "Ellie" in page and "Nate" in page
        assert page.count('class="nametag') == 3  # two children, one pickup

    def test_the_code_does_not_change(self, db, staff, webbs, sunday):
        """The family is carrying the old one. A new one strands them."""
        check_in(db, staff, webbs, sunday)
        before = codes(db, sunday)
        staff.get(self.url(sunday, webbs), headers=H)
        assert codes(db, sunday) == before

    def test_it_makes_no_new_check_in(self, db, staff, webbs, sunday):
        check_in(db, staff, webbs, sunday)
        before = len(sunday.checkins)
        staff.get(self.url(sunday, webbs), headers=H)
        db.session.refresh(sunday)
        assert len(sunday.checkins) == before

    def test_it_is_logged(self, db, staff, webbs, sunday):
        check_in(db, staff, webbs, sunday)
        staff.get(self.url(sunday, webbs), headers=H)
        event = db.session.scalar(
            db.select(AuditEvent).where(AuditEvent.action == TAG_REPRINTED)
        )
        assert event is not None
        assert event.subject_label == "Marcus and Dana Webb"

    def test_the_log_names_the_children(self, db, staff, webbs, sunday):
        """"A tag was reprinted" is not an answer to "for whom"."""
        check_in(db, staff, webbs, sunday)
        staff.get(self.url(sunday, webbs), headers=H)
        event = db.session.scalar(
            db.select(AuditEvent).where(AuditEvent.action == TAG_REPRINTED)
        )
        assert "Ellie Webb" in event.detail and "Nate Webb" in event.detail

    def test_a_child_already_collected_still_prints(self, db, staff, webbs, sunday):
        """Somebody is usually holding the wrong half of a torn tag."""
        check_in(db, staff, webbs, sunday)
        db.session.refresh(sunday)
        sunday.checkins[0].check_out("Marcus Webb")
        db.session.commit()
        page = staff.get(self.url(sunday, webbs), headers=H).data.decode()
        assert page.count('class="nametag') == 3

    def test_a_family_with_nobody_checked_in_says_so(self, db, staff, webbs, sunday):
        page = staff.get(self.url(sunday, webbs), headers=H,
                         follow_redirects=True).data.decode()
        assert "Nobody from this family is checked in" in page

    def test_that_case_logs_nothing(self, db, staff, webbs, sunday):
        staff.get(self.url(sunday, webbs), headers=H, follow_redirects=True)
        assert db.session.scalar(
            db.select(db.func.count(AuditEvent.id)).where(
                AuditEvent.action == TAG_REPRINTED)
        ) == 0


class TestReprintingOneChild:
    def test_it_prints_that_child_and_the_pickup_tag(self, db, staff, webbs, sunday):
        check_in(db, staff, webbs, sunday)
        db.session.refresh(sunday)
        ellie = next(c for c in sunday.checkins if c.person.first_name == "Ellie")
        page = staff.get(f"/kids/tags/child/{ellie.id}/", headers=H).data.decode()
        assert "Ellie" in page
        assert "Nate" not in page
        assert page.count('class="nametag') == 2

    def test_it_carries_the_same_code(self, db, staff, webbs, sunday):
        check_in(db, staff, webbs, sunday)
        db.session.refresh(sunday)
        ellie = next(c for c in sunday.checkins if c.person.first_name == "Ellie")
        page = staff.get(f"/kids/tags/child/{ellie.id}/", headers=H).data.decode()
        assert ellie.pickup_code in page

    def test_it_is_logged_against_the_child(self, db, staff, webbs, sunday):
        check_in(db, staff, webbs, sunday)
        db.session.refresh(sunday)
        ellie = next(c for c in sunday.checkins if c.person.first_name == "Ellie")
        staff.get(f"/kids/tags/child/{ellie.id}/", headers=H)
        event = db.session.scalar(
            db.select(AuditEvent).where(AuditEvent.action == TAG_REPRINTED)
        )
        assert event is not None
        assert event.subject_label == "Ellie Webb"

    def test_a_missing_check_in_is_a_404(self, db, staff, webbs, sunday):
        assert staff.get("/kids/tags/child/999999/", headers=H).status_code == 404


class TestWhatATagMustNotCarry:
    def test_it_does_not_carry_a_persons_notes(self, db, staff, webbs, sunday):
        """Notes are pastoral. A tag is left on a table."""
        check_in(db, staff, webbs, sunday)
        page = staff.get(f"/kids/tags/{sunday.id}/family/{webbs.id}/",
                         headers=H).data.decode()
        assert "Court order" not in page
        assert "not to collect" not in page

    def test_it_does_not_carry_the_household_check_in_pin(self, db, staff, webbs, sunday):
        """The PIN is the family's, every week. Printing it on something left
        in a hallway hands it to whoever picks the tag up."""
        check_in(db, staff, webbs, sunday)
        page = staff.get(f"/kids/tags/{sunday.id}/family/{webbs.id}/",
                         headers=H).data.decode()
        assert webbs.checkin_pin not in page


class TestWhoCanPrint:
    def test_a_signed_out_visitor_cannot(self, db, staff, webbs, sunday):
        """Same client throughout: the sign-out has to come after the
        check-in, or the check-in is the thing that fails."""
        check_in(db, staff, webbs, sunday)
        db.session.refresh(sunday)
        paths = (f"/kids/tags/{sunday.id}/family/{webbs.id}/",
                 f"/kids/tags/child/{sunday.checkins[0].id}/")
        staff.post("/auth/logout", headers=H, follow_redirects=True)
        for path in paths:
            r = staff.get(path, headers=H)
            assert r.status_code in (302, 401, 403)

    def test_a_member_cannot(self, member):
        """Refused before anything is looked up, so the ids here do not need
        to exist. No db fixture: it pins one application context open and a
        second sign-in on the same client would inherit the first identity."""
        assert member.get("/kids/tags/1/family/1/", headers=H).status_code == 403

    def test_a_member_cannot_reach_one_child(self, member):
        assert member.get("/kids/tags/child/1/", headers=H).status_code == 403

    def test_another_church_cannot(self, db, staff, webbs, sunday):
        """Session and check-in ids are guessable integers."""
        check_in(db, staff, webbs, sunday)
        r = staff.get(f"/kids/tags/{sunday.id}/family/{webbs.id}/",
                      headers={"Host": RIVERBEND_HOST})
        assert r.status_code in (302, 403, 404)


class TestTheReprintLinks:
    def test_the_staff_list_offers_a_reprint_per_child(self, db, staff, webbs, sunday):
        check_in(db, staff, webbs, sunday)
        page = staff.get("/kids/", headers=H).data.decode()
        assert page.count("Reprint tag") == 2
        db.session.refresh(sunday)
        for checkin in sunday.checkins:
            assert f"/kids/tags/child/{checkin.id}/" in page

    def test_the_desk_can_reprint_a_whole_family(self, db, staff, webbs, sunday):
        """Where "we lost the tag" is actually said."""
        check_in(db, staff, webbs, sunday)
        db.session.refresh(sunday)
        code = sunday.checkins[0].pickup_code
        page = staff.get(f"/kids/checkout/?code={code}", headers=H).data.decode()
        assert f"/kids/tags/{sunday.id}/family/{webbs.id}/" in page

    def test_a_reprint_opens_away_from_the_desk(self, db, staff, webbs, sunday):
        """A volunteer mid-collection should not lose the screen they are on."""
        check_in(db, staff, webbs, sunday)
        page = staff.get("/kids/", headers=H).data.decode()
        block = page[page.index("/kids/tags/child/") - 200:]
        assert 'target="_blank"' in block[:400]


class TestThePrintedPage:
    def test_the_controls_are_marked_not_to_print(self, db, staff, webbs, sunday):
        check_in(db, staff, webbs, sunday)
        page = staff.get(f"/kids/tags/{sunday.id}/family/{webbs.id}/",
                         headers=H).data.decode()
        assert "noprint" in page

    def test_a_label_printer_can_get_one_tag_per_page(self, db, staff, webbs, sunday):
        check_in(db, staff, webbs, sunday)
        page = staff.get(f"/kids/tags/{sunday.id}/family/{webbs.id}/",
                         headers=H).data.decode()
        assert "data-one-per-page" in page
        assert "One tag per page" in page

    def test_it_is_not_indexed(self, db, staff, webbs, sunday):
        check_in(db, staff, webbs, sunday)
        page = staff.get(f"/kids/tags/{sunday.id}/family/{webbs.id}/",
                         headers=H).data.decode()
        assert "noindex" in page


class TestTheStylesheet:
    from pathlib import Path as _Path

    CSS = (_Path(__file__).resolve().parent.parent / "app" / "static" / "css"
           / "app.css").read_text()

    def test_tags_are_hidden_on_screen(self):
        """A volunteer sees the code once, where it has always been. A second
        copy on the same screen is one more thing read over a shoulder."""
        assert ".tagsheet{display:none}" in self.CSS

    def test_a_tag_is_measured_in_inches(self):
        """Its output is judged by a printer, not a viewport."""
        block = self.CSS[self.CSS.index(".nametag{"):]
        assert "in;" in block[:block.index("}")]

    def test_a_tag_is_not_split_across_sheets(self):
        block = self.CSS[self.CSS.index(".nametag{"):]
        assert "break-inside:avoid" in block[:block.index("}")]

    def test_there_is_a_print_stylesheet(self):
        assert "@media print" in self.CSS
        assert ".noprint{display:none !important}" in self.CSS


class TestCheckingOutFromTheStaffList:
    """A second way out, for the rest of Sunday.

    The desk screen stays the normal path because it asks who is collecting.
    This one is for the parent who left before anybody reached a screen and
    the row still open at the end of the morning.
    """

    def url(self, checkin):
        return f"/kids/checkins/{checkin.id}/out/"

    def present(self, db, sunday, first="Ellie"):
        db.session.refresh(sunday)
        return next(c for c in sunday.checkins if c.person.first_name == first)

    def test_the_button_is_on_a_child_still_in_a_room(self, db, staff, webbs, sunday):
        check_in(db, staff, webbs, sunday)
        page = staff.get("/kids/", headers=H).data.decode()
        assert page.count("Check out") >= 2
        assert "softdanger" in page

    def test_it_checks_them_out(self, db, staff, webbs, sunday):
        check_in(db, staff, webbs, sunday)
        row = self.present(db, sunday)
        staff.post(self.url(row), headers=H)
        db.session.refresh(row)
        assert not row.is_present

    def test_it_leaves_the_sibling_alone(self, db, staff, webbs, sunday):
        check_in(db, staff, webbs, sunday)
        row = self.present(db, sunday)
        staff.post(self.url(row), headers=H)
        assert self.present(db, sunday, "Nate").is_present

    def test_it_does_not_invent_who_collected_them(self, db, staff, webbs, sunday):
        """The staff member did not collect the child. A record saying they
        did is worse than one that admits it does not know."""
        check_in(db, staff, webbs, sunday)
        row = self.present(db, sunday)
        staff.post(self.url(row), headers=H)
        db.session.refresh(row)
        assert row.collected_by is None

    def test_who_pressed_it_is_in_the_log(self, db, staff, webbs, sunday):
        from app.models.audit import CHILD_CHECKED_OUT

        check_in(db, staff, webbs, sunday)
        row = self.present(db, sunday)
        staff.post(self.url(row), headers=H)
        event = db.session.scalar(
            db.select(AuditEvent).where(AuditEvent.action == CHILD_CHECKED_OUT)
        )
        assert event is not None
        assert "staff list" in event.summary
        assert event.subject_label == "Ellie Webb"

    def test_the_log_says_nobody_was_named(self, db, staff, webbs, sunday):
        from app.models.audit import CHILD_CHECKED_OUT

        check_in(db, staff, webbs, sunday)
        staff.post(self.url(self.present(db, sunday)), headers=H)
        event = db.session.scalar(
            db.select(AuditEvent).where(AuditEvent.action == CHILD_CHECKED_OUT)
        )
        assert "Nobody was recorded as collecting them" in event.detail

    def test_it_says_who_went_home(self, db, staff, webbs, sunday):
        check_in(db, staff, webbs, sunday)
        page = staff.post(self.url(self.present(db, sunday)), headers=H,
                          follow_redirects=True).data.decode()
        assert "Ellie Webb checked out" in page

    def test_the_button_goes_away_once_they_are_out(self, db, staff, webbs, sunday):
        """A button beside a row that already says collected can only error."""
        check_in(db, staff, webbs, sunday)
        staff.post(self.url(self.present(db, sunday)), headers=H)
        page = staff.get("/kids/", headers=H).data.decode()
        assert page.count("Check out") == 1  # Nate only

    def test_checking_out_twice_changes_nothing(self, db, staff, webbs, sunday):
        """Two volunteers on two tablets, one child."""
        check_in(db, staff, webbs, sunday)
        row = self.present(db, sunday)
        staff.post(self.url(row), headers=H)
        db.session.refresh(row)
        first = row.checked_out_at

        page = staff.post(self.url(row), headers=H,
                          follow_redirects=True).data.decode()
        db.session.refresh(row)
        assert row.checked_out_at == first
        assert "was already collected" in page

    def test_a_missing_row_is_a_404(self, db, staff, webbs, sunday):
        assert staff.post("/kids/checkins/999999/out/", headers=H).status_code == 404

    def test_a_member_cannot(self, member):
        assert member.post("/kids/checkins/1/out/", headers=H).status_code == 403

    def test_another_church_cannot(self, db, staff, webbs, sunday):
        check_in(db, staff, webbs, sunday)
        row = self.present(db, sunday)
        staff.post(self.url(row), headers={"Host": RIVERBEND_HOST})
        db.session.refresh(row)
        assert row.is_present


class TestDeletingASession:
    """Clearing out a Sunday entered twice, or the sessions from setup.

    What this destroys is the record of which children were in a room and who
    took them home. Three guards, each tested: staff only, never the open
    session, and a tick box. The audit entry is the only thing left afterwards
    so it has to carry the names.
    """

    def url(self, sunday):
        return f"/kids/sessions/{sunday.id}/delete/"

    def closed(self, db, sunday):
        sunday.close()
        db.session.commit()
        return sunday

    def test_it_deletes_an_empty_session(self, db, staff, sunday):
        self.closed(db, sunday)
        staff.post(self.url(sunday), data={"confirm": "on"}, headers=H)
        assert db.session.get(CheckinSession, sunday.id) is None

    def test_it_takes_the_check_ins_with_it(self, db, staff, webbs, sunday):
        check_in(db, staff, webbs, sunday)
        ids = [c.id for c in sunday.checkins]
        assert ids
        self.closed(db, sunday)
        staff.post(self.url(sunday), data={"confirm": "on"}, headers=H)
        assert all(db.session.get(Checkin, i) is None for i in ids)

    def test_it_says_what_went(self, db, staff, sunday):
        self.closed(db, sunday)
        page = staff.post(self.url(sunday), data={"confirm": "on"}, headers=H,
                          follow_redirects=True).data.decode()
        assert "Sunday 9:30" in page and "is gone" in page

    def test_the_open_session_cannot_be_deleted(self, db, staff, webbs, sunday):
        """Children may be in a room right now, and their pickup codes would
        go with it."""
        check_in(db, staff, webbs, sunday)
        page = staff.post(self.url(sunday), data={"confirm": "on"}, headers=H,
                          follow_redirects=True).data.decode()
        assert db.session.get(CheckinSession, sunday.id) is not None
        assert "Close the session before deleting" in page

    def test_the_tick_box_is_required(self, db, staff, sunday):
        self.closed(db, sunday)
        page = staff.post(self.url(sunday), headers=H,
                          follow_redirects=True).data.decode()
        assert db.session.get(CheckinSession, sunday.id) is not None
        assert "Tick the box first" in page

    def test_the_log_keeps_the_children(self, db, staff, webbs, sunday):
        """After this runs the entry is the only thing left that says the
        morning happened."""
        from app.models.audit import CHECKIN_SESSION_DELETED

        check_in(db, staff, webbs, sunday)
        self.closed(db, sunday)
        staff.post(self.url(sunday), data={"confirm": "on"}, headers=H)

        event = db.session.scalar(
            db.select(AuditEvent).where(
                AuditEvent.action == CHECKIN_SESSION_DELETED)
        )
        assert event is not None
        assert "Ellie Webb" in event.detail and "Nate Webb" in event.detail
        assert "Sunday 9:30" in event.subject_label

    def test_the_log_survives_the_delete(self, db, staff, webbs, sunday):
        from app.models.audit import CHECKIN_SESSION_DELETED

        check_in(db, staff, webbs, sunday)
        self.closed(db, sunday)
        staff.post(self.url(sunday), data={"confirm": "on"}, headers=H)
        assert db.session.scalar(
            db.select(db.func.count(AuditEvent.id)).where(
                AuditEvent.action == CHECKIN_SESSION_DELETED)
        ) == 1

    def test_a_leader_cannot(self, db, leader, sunday):
        """Everything else in Kids is open to the volunteers at the desk."""
        assert leader.post(self.url(sunday), data={"confirm": "on"},
                           headers=H).status_code == 403

    def test_a_member_cannot(self, member, sunday):
        assert member.post("/kids/sessions/1/delete/", data={"confirm": "on"},
                           headers=H).status_code == 403

    def test_another_church_cannot(self, db, staff, sunday):
        self.closed(db, sunday)
        staff.post(self.url(sunday), data={"confirm": "on"},
                   headers={"Host": RIVERBEND_HOST})
        assert db.session.get(CheckinSession, sunday.id) is not None

    def test_a_missing_session_is_a_404(self, db, staff):
        r = staff.post("/kids/sessions/999999/delete/", data={"confirm": "on"},
                       headers=H)
        assert r.status_code == 404


class TestTheDeleteControl:
    def test_it_is_folded_away(self, db, staff, sunday):
        """Not a button sitting next to Reopen."""
        sunday.close()
        db.session.commit()
        page = staff.get("/kids/", headers=H).data.decode()
        assert "dangerbox" in page
        assert "Delete this session" in page

    def test_it_is_not_on_the_open_session(self, db, staff, sunday):
        page = staff.get("/kids/", headers=H).data.decode()
        assert "Delete this session" not in page

    def test_it_says_how_many_check_ins_go_with_it(self, db, staff, webbs, sunday):
        check_in(db, staff, webbs, sunday)
        sunday.close()
        db.session.commit()
        page = staff.get("/kids/", headers=H).data.decode()
        assert "2 check-ins go with it" in page

    def test_an_empty_session_says_so_instead(self, db, staff, sunday):
        sunday.close()
        db.session.commit()
        page = staff.get("/kids/", headers=H).data.decode()
        assert "Nobody checked in to this one" in page

    def test_a_leader_does_not_see_it(self, db, leader, sunday):
        sunday.close()
        db.session.commit()
        page = leader.get("/kids/", headers=H).data.decode()
        assert "Delete this session" not in page
