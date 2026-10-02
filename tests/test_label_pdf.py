"""Name tags as a file.

The check-in screens printed tags by calling `window.print()`. That works in a
desktop browser and does nothing at all in the iOS app the church runs check-in
on, because Apple's web view implements no print function. The button was
there, the tap did nothing, and nothing said so.

Two things are tested here. That a file comes back at all, and that it is the
size of the label in the printer, because a 62mm tag rendered at letter size
prints across four labels or not at all and looks like the same silent failure
from the other end.
"""

import pytest

from app.labels import BY_CODE, DEFAULT_SIZE, size_for
from app.models import Checkin, CheckinSession, Church, Household, Person
from app.models.base import utcnow
from tests.conftest import JOURNEY_HOST

H = {"Host": JOURNEY_HOST}


@pytest.fixture
def journey(db):
    return db.session.scalar(db.select(Church).where(Church.slug == "journey"))


@pytest.fixture
def family(db, journey):
    household = Household(church_id=journey.id, name="The Webbs")
    db.session.add(household)
    db.session.flush()
    for first in ("Eli", "Nora"):
        db.session.add(Person(
            church_id=journey.id, first_name=first, last_name="Webb",
            household_id=household.id, is_child=True, stage="visitor",
            approved_at=utcnow(),
        ))
    db.session.commit()
    return household


@pytest.fixture
def sunday(db, journey):
    checkin_session = CheckinSession(
        church_id=journey.id, name="Sunday 9:30", starts_at=utcnow(), is_open=True
    )
    db.session.add(checkin_session)
    db.session.commit()
    return checkin_session


def check_them_in(db, journey, family, sunday):
    code = sunday.issue_pickup_code(family.id)
    for person in family.members:
        db.session.add(Checkin(
            church_id=journey.id, session_id=sunday.id, person_id=person.id,
            household_id=family.id, household_name=family.name,
            pickup_code=code,
        ))
    db.session.commit()
    return code


class TestItIsActuallyAFile:
    def test_the_kiosk_gets_a_pdf(self, db, journey, staff, family, sunday):
        check_them_in(db, journey, family, sunday)

        page = staff.get(
            f"/kids/kiosk/labels/{sunday.id}/family/{family.id}/", headers=H
        )
        assert page.status_code == 200
        assert page.mimetype == "application/pdf"
        assert page.data.startswith(b"%PDF-")

    def test_it_opens_rather_than_downloads(self, db, journey, staff, family, sunday):
        """The point is a viewer with a print button, not a file in Files."""
        check_them_in(db, journey, family, sunday)

        page = staff.get(
            f"/kids/kiosk/labels/{sunday.id}/family/{family.id}/", headers=H
        )
        assert page.headers["Content-Disposition"].startswith("inline")

    def test_a_pickup_code_is_not_cached(self, db, journey, staff, family, sunday):
        """It is a live credential for collecting a child."""
        check_them_in(db, journey, family, sunday)

        page = staff.get(
            f"/kids/kiosk/labels/{sunday.id}/family/{family.id}/", headers=H
        )
        assert "no-store" in page.headers["Cache-Control"]

    def test_one_page_per_child_plus_a_pickup_tag(
        self, db, journey, staff, family, sunday
    ):
        from io import BytesIO

        from pypdf import PdfReader

        check_them_in(db, journey, family, sunday)

        page = staff.get(
            f"/kids/kiosk/labels/{sunday.id}/family/{family.id}/", headers=H
        )
        reader = PdfReader(BytesIO(page.data))
        # Two children, then one pickup tag for the adult.
        assert len(reader.pages) == 3

    def test_the_names_and_the_code_are_on_it(
        self, db, journey, staff, family, sunday
    ):
        from io import BytesIO

        from pypdf import PdfReader

        code = check_them_in(db, journey, family, sunday)

        page = staff.get(
            f"/kids/kiosk/labels/{sunday.id}/family/{family.id}/", headers=H
        )
        text = "".join(p.extract_text() or "" for p in PdfReader(BytesIO(page.data)).pages)
        assert "Eli" in text
        assert "Nora" in text
        assert code in text

    def test_nothing_is_checked_in_means_nothing_to_print(
        self, db, journey, staff, family, sunday
    ):
        assert staff.get(
            f"/kids/kiosk/labels/{sunday.id}/family/{family.id}/", headers=H
        ).status_code == 404


class TestItIsTheSizeOfTheLabel:
    """A tag rendered at the wrong size fails the same silent way the dead
    print button did: nothing useful comes out of the printer and nothing
    says why."""

    def _page_size(self, data):
        from io import BytesIO

        from pypdf import PdfReader

        box = PdfReader(BytesIO(data)).pages[0].mediabox
        return float(box.width), float(box.height)

    def test_the_default_is_the_common_brother_roll(self, db, journey, staff,
                                                    family, sunday):
        check_them_in(db, journey, family, sunday)

        page = staff.get(
            f"/kids/kiosk/labels/{sunday.id}/family/{family.id}/", headers=H
        )
        width, height = self._page_size(page.data)
        expected = BY_CODE[DEFAULT_SIZE]
        assert round(width) == round(expected.width)
        assert round(height) == round(expected.height)

    def test_the_church_setting_changes_it(self, db, journey, staff, family, sunday):
        check_them_in(db, journey, family, sunday)
        journey.kids_label_size = "dk1201"
        db.session.commit()

        page = staff.get(
            f"/kids/kiosk/labels/{sunday.id}/family/{family.id}/", headers=H
        )
        width, _ = self._page_size(page.data)
        assert round(width) == round(BY_CODE["dk1201"].width)

    def test_62mm_really_is_62mm(self):
        """The whole reason for a PDF rather than an HTML page: a real
        physical dimension the printer does not have to guess at."""
        size = BY_CODE["dk1202"]
        assert round(size.width_mm) == 62
        # 1pt is 1/72 inch, 1 inch is 25.4mm.
        assert abs(size.width - (62 * 72 / 25.4)) < 0.01

    def test_an_unknown_code_prints_the_default_rather_than_nothing(self):
        """A church whose setting predates a rename should get a tag."""
        assert size_for("dk9999").code == DEFAULT_SIZE
        assert size_for(None).code == DEFAULT_SIZE
        assert size_for("").code == DEFAULT_SIZE

    def test_a_long_name_does_not_run_off_the_label(self, db, journey, staff,
                                                    family, sunday):
        """A child called Konstantinos gets a smaller name, not a name that
        leaves the label."""
        from io import BytesIO

        from pypdf import PdfReader

        long_name = Person(
            church_id=journey.id, first_name="Konstantinos", last_name="Papadopoulos",
            household_id=family.id, is_child=True, stage="visitor",
            approved_at=utcnow(),
        )
        db.session.add(long_name)
        db.session.commit()
        check_them_in(db, journey, family, sunday)

        page = staff.get(
            f"/kids/kiosk/labels/{sunday.id}/family/{family.id}/", headers=H
        )
        assert page.status_code == 200
        text = "".join(p.extract_text() or "" for p in PdfReader(BytesIO(page.data)).pages)
        assert "Konstantinos" in text


class TestWhoCanPrintWhat:
    def test_a_reprint_is_still_logged(self, db, journey, staff, family, sunday):
        """A reprint hands out a second copy of a live pickup code, and the
        file version is no different from the page version in that respect."""
        from app.models.audit import TAG_REPRINTED, AuditEvent

        check_them_in(db, journey, family, sunday)
        staff.get(f"/kids/tags/{sunday.id}/family/{family.id}/pdf/", headers=H)

        assert db.session.scalar(
            db.select(db.func.count(AuditEvent.id)).where(
                AuditEvent.action == TAG_REPRINTED)
        ) == 1

    def test_the_first_print_is_not_logged_as_a_reprint(
        self, db, journey, staff, family, sunday
    ):
        """Printing the tag you just made is the check-in finishing, so the
        reprint log keeps meaning what it says."""
        from app.models.audit import TAG_REPRINTED, AuditEvent

        check_them_in(db, journey, family, sunday)
        staff.get(f"/kids/kiosk/labels/{sunday.id}/family/{family.id}/", headers=H)

        assert db.session.scalar(
            db.select(db.func.count(AuditEvent.id)).where(
                AuditEvent.action == TAG_REPRINTED)
        ) == 0

    def test_a_member_cannot_print_tags(self, member, db, journey, family, sunday):
        page = member.get(
            f"/kids/kiosk/labels/{sunday.id}/family/{family.id}/", headers=H
        )
        assert page.status_code == 403

    def test_another_churchs_family_is_not_found(self, db, staff, sunday):
        riverbend = db.session.scalar(
            db.select(Church).where(Church.slug == "riverbend")
        )
        theirs = Household(church_id=riverbend.id, name="Theirs")
        db.session.add(theirs)
        db.session.commit()

        assert staff.get(
            f"/kids/kiosk/labels/{sunday.id}/family/{theirs.id}/", headers=H
        ).status_code == 404


class TestTheSettingItself:
    def test_staff_can_change_the_roll(self, db, journey, staff):
        staff.post("/settings/kiosk/label/", headers=H,
                   data={"label_size": "dk2205"}, follow_redirects=True)
        assert journey.kids_label_size == "dk2205"

    def test_a_made_up_roll_is_refused(self, db, journey, staff):
        before = journey.kids_label_size
        assert staff.post("/settings/kiosk/label/", headers=H,
                          data={"label_size": "dk9999"}).status_code == 400
        assert journey.kids_label_size == before

    def test_a_leader_cannot_change_it(self, leader):
        assert leader.post("/settings/kiosk/label/", headers=H,
                           data={"label_size": "dk1201"}).status_code == 403
