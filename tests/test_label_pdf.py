"""Name tags as a file.

The check-in screens printed tags by calling `window.print()`. That works in a
desktop browser and does nothing at all in the iOS app the church runs check-in
on, because Apple's web view implements no print function. The button was
there, the tap did nothing, and nothing said so.

Three things are tested here. That a file comes back at all; that it is the
size of the label in the printer, because a 62mm tag rendered at letter size
prints across four labels or not at all and looks like the same silent failure
from the other end; and that the signed link which replaces the session cannot
be forged, widened, replayed at another church, or used after it expires.

That last group is the one to read carefully. The link exists because the file
has to open in a browser that does not share the app's cookies, so it carries
its own authorization, and the thing it authorizes is a live pickup code for
collecting a child.
"""

import pytest

from app.labels import BY_CODE, DEFAULT_SIZE, size_for
from app.models import Checkin, CheckinSession, Church, Household, Person
from app.models.base import utcnow
from tests.conftest import JOURNEY_HOST, RIVERBEND_HOST

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


def pdf_link(client, sunday, family):
    """The link exactly as the kiosk renders it after a check-in.

    Taken off the real page rather than minted in the test, so these tests
    break if the page stops producing a usable link, which is the failure the
    church would actually see.
    """
    import re

    page = client.post(
        f"/kids/kiosk/family/{family.id}/", headers=H,
        data={"person_id": [p.id for p in family.members]},
        follow_redirects=True,
    ).get_data(as_text=True)
    found = re.search(r'href="(/kids/tags/pdf/[^"]+)"', page)
    assert found, "the check-in screen rendered no printable link"
    return found.group(1)


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
        page = staff.get(pdf_link(staff, sunday, family), headers=H)
        assert page.status_code == 200
        assert page.mimetype == "application/pdf"
        assert page.data.startswith(b"%PDF-")

    def test_it_opens_rather_than_downloads(self, db, journey, staff, family, sunday):
        """The point is a viewer with a print button, not a file in Files."""
        page = staff.get(pdf_link(staff, sunday, family), headers=H)
        assert page.headers["Content-Disposition"].startswith("inline")

    def test_a_pickup_code_is_not_cached(self, db, journey, staff, family, sunday):
        """It is a live credential for collecting a child."""
        page = staff.get(pdf_link(staff, sunday, family), headers=H)
        assert "no-store" in page.headers["Cache-Control"]

    def test_one_page_per_child_plus_a_pickup_tag(
        self, db, journey, staff, family, sunday
    ):
        from io import BytesIO

        from pypdf import PdfReader

        page = staff.get(pdf_link(staff, sunday, family), headers=H)
        reader = PdfReader(BytesIO(page.data))
        # Two children, then one pickup tag for the adult.
        assert len(reader.pages) == 3

    def test_the_names_and_the_code_are_on_it(
        self, db, journey, staff, family, sunday
    ):
        from io import BytesIO

        from pypdf import PdfReader

        page = staff.get(pdf_link(staff, sunday, family), headers=H)
        text = "".join(p.extract_text() or "" for p in PdfReader(BytesIO(page.data)).pages)
        assert "Eli" in text
        assert "Nora" in text
        code = sunday.checkins_for_household(family.id)[0].pickup_code
        assert code in text

    def test_a_link_to_rows_that_are_gone_is_refused(
        self, db, journey, staff, family, sunday
    ):
        """A signature that is still valid is not enough; the rows it names
        have to still be there, in this church, in that session."""
        link = pdf_link(staff, sunday, family)
        for row in sunday.checkins_for_household(family.id):
            db.session.delete(row)
        db.session.commit()

        assert staff.get(link, headers=H).status_code == 404


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
        page = staff.get(pdf_link(staff, sunday, family), headers=H)
        width, height = self._page_size(page.data)
        expected = BY_CODE[DEFAULT_SIZE]
        assert round(width) == round(expected.width)
        assert round(height) == round(expected.height)

    def test_the_church_setting_changes_it(self, db, journey, staff, family, sunday):
        journey.kids_label_size = "dk1201"
        db.session.commit()

        page = staff.get(pdf_link(staff, sunday, family), headers=H)
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
        page = staff.get(pdf_link(staff, sunday, family), headers=H)
        assert page.status_code == 200
        text = "".join(p.extract_text() or "" for p in PdfReader(BytesIO(page.data)).pages)
        assert "Konstantinos" in text


class TestWhoCanPrintWhat:
    def test_a_reprint_is_logged_once_not_twice(
        self, db, journey, staff, family, sunday
    ):
        """A reprint hands out a second copy of a live pickup code, so it is
        logged. It is logged where staff ask for it, and NOT again when the
        file is fetched: counting one reprint twice would make the log useless
        for the only question it exists to answer."""
        from app.models.audit import TAG_REPRINTED, AuditEvent

        check_them_in(db, journey, family, sunday)
        page = staff.get(f"/kids/tags/{sunday.id}/family/{family.id}/",
                         headers=H).get_data(as_text=True)

        import re
        link = re.search(r'href="(/kids/tags/pdf/[^"]+)"', page)
        assert link, "the reprint page rendered no printable link"
        staff.get(link.group(1), headers=H)

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

        staff.get(pdf_link(staff, sunday, family), headers=H)

        assert db.session.scalar(
            db.select(db.func.count(AuditEvent.id)).where(
                AuditEvent.action == TAG_REPRINTED)
        ) == 0

    def test_a_member_cannot_reach_the_screens_that_mint_a_link(
        self, member, db, journey, family, sunday
    ):
        """The file itself is reached by a signed link rather than a role, so
        what has to be closed to a member is the screens that hand one out."""
        assert member.post(
            f"/kids/kiosk/family/{family.id}/", headers=H,
            data={"person_id": family.members[0].id},
        ).status_code == 403
        assert member.get(
            f"/kids/tags/{sunday.id}/family/{family.id}/", headers=H
        ).status_code == 403

    def test_another_churchs_family_is_not_found(self, db, staff, sunday):
        riverbend = db.session.scalar(
            db.select(Church).where(Church.slug == "riverbend")
        )
        theirs = Household(church_id=riverbend.id, name="Theirs")
        db.session.add(theirs)
        db.session.commit()

        assert staff.get(
            f"/kids/tags/{sunday.id}/family/{theirs.id}/", headers=H
        ).status_code == 404


class TestTheSignedLink:
    """What replaces the session, and what it is not allowed to become.

    The link exists because the browser that opens the file does not share the
    app's cookies. It is therefore the only thing standing between a URL and a
    live pickup code for somebody's children.
    """

    def test_it_needs_no_session_at_all(self, app, db, journey, staff,
                                        family, sunday):
        """The whole point. A browser opened from the app arrives signed out,
        and a login screen at that moment is a dead print button."""
        link = pdf_link(staff, sunday, family)

        anonymous = app.test_client()
        page = anonymous.get(link, headers=H)
        assert page.status_code == 200
        assert page.data.startswith(b"%PDF-")

    def test_a_made_up_token_is_refused(self, client):
        assert client.get("/kids/tags/pdf/not-a-real-token/",
                          headers=H).status_code == 404

    def test_a_tampered_token_is_refused(self, db, journey, staff, family, sunday):
        link = pdf_link(staff, sunday, family)
        token = link.rstrip("/").rsplit("/", 1)[-1]
        # Flip one character of the payload. The signature no longer matches.
        broken = ("A" if token[5] != "A" else "B").join([token[:5], token[6:]])

        assert staff.get(f"/kids/tags/pdf/{broken}/",
                         headers=H).status_code == 404

    def test_a_token_from_another_church_is_inert(self, db, journey, staff,
                                                  family, sunday):
        """Scoped to the church resolved from the host, so a valid signature
        is not enough on its own.

        Asserted at two levels on purpose. The route refuses it because the
        rows do not belong to the other church, and `verify` refuses it
        because the church in the payload does not match. Either one alone
        would hold today; a test that only drove the route passed unchanged
        when the check inside `verify` was deleted, which is exactly the kind
        of silent single point of failure this is guarding.
        """
        from app.labeltoken import verify

        link = pdf_link(staff, sunday, family)
        assert staff.get(link, headers={"Host": RIVERBEND_HOST}).status_code == 404

        riverbend = db.session.scalar(
            db.select(Church).where(Church.slug == "riverbend")
        )
        token = link.rstrip("/").rsplit("/", 1)[-1]
        assert verify(token, journey.id) is not None
        assert verify(token, riverbend.id) is None

    def test_it_expires(self, app, db, journey, staff, family, sunday):
        """A tag carries a live pickup code. A URL in a browser history must
        not stay a key to somebody's children."""
        import time
        from itsdangerous import URLSafeTimedSerializer

        from app.labeltoken import MAX_AGE_SECONDS, SALT, verify

        check_them_in(db, journey, family, sunday)
        rows = sunday.checkins_for_household(family.id)

        with app.app_context():
            signer = URLSafeTimedSerializer(
                app.config["SECRET_KEY"], salt=SALT)
            payload = {"c": journey.id, "s": sunday.id,
                       "k": sorted(r.id for r in rows)}
            # Signed as of long enough ago to be past the window.
            stale = signer.dumps(payload)
            assert verify(stale, journey.id) is not None

        assert MAX_AGE_SECONDS <= 15 * 60, "a tag link should be short lived"

        old = time.time
        try:
            time.time = lambda: old() + MAX_AGE_SECONDS + 60
            with app.app_context():
                assert verify(stale, journey.id) is None
        finally:
            time.time = old

    def test_one_childs_link_does_not_widen_to_the_family(
        self, db, journey, staff, family, sunday
    ):
        """A staff reprint of one child's tag names that row. It must not be
        editable into the whole household's."""
        from io import BytesIO

        from pypdf import PdfReader

        check_them_in(db, journey, family, sunday)
        rows = sunday.checkins_for_household(family.id)
        one = next(r for r in rows if r.person.first_name == "Eli")

        import re
        page = staff.get(f"/kids/tags/child/{one.id}/",
                         headers=H).get_data(as_text=True)
        link = re.search(r'href="(/kids/tags/pdf/[^"]+)"', page)
        assert link

        data = staff.get(link.group(1), headers=H).data
        text = "".join(p.extract_text() or "" for p in PdfReader(BytesIO(data)).pages)
        assert "Eli" in text
        assert "Nora" not in text


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


class TestGettingItInFrontOfAPrintSheet:
    """The link has to survive three different environments.

    A real browser opens a new tab. Apple's web view does not implement
    `target="_blank"` unless the app embedding it wrote the delegate for it,
    so depending on the app the tap does nothing or it loads the PDF over the
    top of the check-in screen and strands a volunteer on a document with no
    way back.

    Verified by driving both: a plain browser opens a tab and never calls the
    plugin, and a stubbed Capacitor shell is asked to open an absolute URL
    while the kiosk screen stays where it was. These assertions hold the
    shape of that in place.
    """

    def _label_page(self, staff, family, sunday):
        return staff.post(
            f"/kids/kiosk/family/{family.id}/", headers=H,
            data={"person_id": [p.id for p in family.members]},
            follow_redirects=True,
        ).get_data(as_text=True)

    def test_the_plain_link_still_targets_a_new_tab(self, db, journey, staff,
                                                    family, sunday):
        """It has to work with JavaScript off, which is the desk running
        check-in from a laptop."""
        page = self._label_page(staff, family, sunday)
        assert 'target="_blank"' in page
        assert 'rel="noopener"' in page

    def test_it_asks_the_wrapper_rather_than_navigating(self, db, journey, staff,
                                                        family, sunday):
        page = self._label_page(staff, family, sunday)
        assert "window.Capacitor" in page
        assert "Plugins.Browser" in page
        assert "preventDefault" in page

    def test_it_hands_the_wrapper_an_absolute_url(self, db, journey, staff,
                                                  family, sunday):
        """A path relative to the app's own origin means nothing once it has
        been handed to the system."""
        page = self._label_page(staff, family, sunday)
        assert "new URL(" in page

    def test_nothing_is_guessed_at_when_the_plugin_is_missing(
        self, db, journey, staff, family, sunday
    ):
        """A wrapper without the browser plugin leaves the plain link alone
        rather than inventing a path that does not exist."""
        page = self._label_page(staff, family, sunday)
        assert 'typeof browser.open !== "function"' in page
        assert "return; }" in page
