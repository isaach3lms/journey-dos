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
