"""Getting a name tag out of the printer.

Written after check-in ran for weeks without tags. Three separate things were
wrong and only one of them was visible:

1. **Every tag carried the wrong date.** `format_local(value, church, fmt)`
   was called as `format_local(value, fmt)`, so the format landed in the
   church argument and the default format was used instead. It never raised,
   because `zone_for` falls back on a bad value rather than throwing, so a tag
   that should have read "Oct 8" read "Thursday 9:30am" and nobody connected
   that to a bug.
2. **The print button did nothing inside the app**, and said nothing. Apple's
   web view has no print function, the page handed the file to a browser
   plugin instead, and when the wrapper had no such plugin the tap was a
   silent no-op.
3. **The roll list read as a list of printers**, so a church with a QL-820NWB
   could not find its model and concluded the printer was unsupported.

The first is testable here and now is. The second is a device behaviour, so
what is tested is that the page carries the machinery to detect and report it
rather than failing silently. The third is a list and a sentence.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone

import pytest

from app.labels import BY_CODE, SIZES, size_for
from app.models import Checkin, CheckinSession, Church, Household, Person
from app.models.base import utcnow
from tests.conftest import JOURNEY_HOST

H = {"Host": JOURNEY_HOST}


@pytest.fixture
def journey(db):
    church = db.session.scalar(db.select(Church).where(Church.slug == "journey"))
    church.timezone = "America/Chicago"
    db.session.commit()
    return church


@pytest.fixture
def family(db, journey):
    household = Household(church_id=journey.id, name="The Webbs")
    db.session.add(household)
    db.session.flush()
    db.session.add_all([
        Person(church_id=journey.id, first_name="Marcus", last_name="Webb",
               stage="member", household_id=household.id, approved_at=utcnow()),
        Person(church_id=journey.id, first_name="Ellie", last_name="Webb",
               stage="member", household_id=household.id, is_child=True,
               birthdate=date(2019, 1, 1)),
    ])
    db.session.add(CheckinSession(church_id=journey.id, name="Sunday 9:30",
                                  starts_at=utcnow()))
    db.session.commit()
    return household


def check_in(staff, family, person):
    return staff.post(f"/kids/kiosk/family/{family.id}/",
                      data={"person_id": person.id}, headers=H,
                      follow_redirects=True)


def pdf_link(body: str) -> str | None:
    found = re.search(r'data-print-link href="([^"]+)"', body)
    return found.group(1) if found else None


def child(family, first="Ellie"):
    return next(p for p in family.members if p.first_name == first)


class TestTheDateOnTheTag:
    """The bug that printed on every tag this church ever produced."""

    def test_format_local_refuses_a_format_in_the_church_argument(self):
        """The guard that would have caught it on the first run.

        `zone_for` falls back rather than raising, which is right for a bad
        timezone in a column and exactly what let this through: the call
        worked, returned a plausible string, and was wrong.
        """
        from app.timeutil import format_local

        when = datetime(2026, 10, 8, 14, 30, tzinfo=timezone.utc)
        with pytest.raises(TypeError) as caught:
            format_local(when, "%b %-d")
        assert "second argument is the church" in str(caught.value)

    def test_none_is_still_allowed(self):
        """Outside a request there is no tenant, and that is not a bug."""
        from app.timeutil import format_local

        when = datetime(2026, 10, 8, 14, 30, tzinfo=timezone.utc)
        assert format_local(when, None, "%b %-d") == "Oct 8"

    def test_the_tag_route_asks_for_a_date_and_gets_one(
        self, app, db, journey, family, staff
    ):
        """End to end. Before the fix this produced a weekday and a time."""
        from app.labels import render_tags

        captured = {}
        real = render_tags

        def spy(**kwargs):
            captured.update(kwargs)
            return real(**kwargs)

        import app.labels as labels
        labels.render_tags = spy
        try:
            page = check_in(staff, family, child(family))
            url = pdf_link(page.get_data(as_text=True))
            assert url
            assert staff.get(url, headers=H).status_code == 200
        finally:
            labels.render_tags = real

        when = captured.get("when", "")
        assert when, "the tag was rendered with no date at all"
        # A date, not a weekday and a clock time.
        assert not re.match(r"^(Mon|Tues|Wednes|Thurs|Fri|Satur|Sun)day", when), (
            f"the tag says {when!r}, which is the default format rather than "
            "the date: the church argument is missing again"
        )
        assert re.match(r"^[A-Z][a-z]{2} \d{1,2}$", when), when


class TestTheTagItself:
    def test_a_check_in_produces_a_real_pdf(self, app, db, journey, family, staff):
        page = check_in(staff, family, child(family))
        url = pdf_link(page.get_data(as_text=True))
        assert url, "the label page offered no tag to print"

        tag = staff.get(url, headers=H)
        assert tag.status_code == 200
        assert tag.headers["Content-Type"].startswith("application/pdf")
        assert tag.data.startswith(b"%PDF")
        assert len(tag.data) > 500

    def test_the_tag_opens_without_the_app_session(
        self, app, db, journey, family, staff, client
    ):
        """The whole reason the link is signed. A browser opened from the app
        does not carry the app's cookies, so a login-protected tag would meet
        a login screen at the one moment nobody can stop to type a password.
        """
        page = check_in(staff, family, child(family))
        url = pdf_link(page.get_data(as_text=True))

        anonymous = client.get(url, headers=H)
        assert anonymous.status_code == 200
        assert anonymous.data.startswith(b"%PDF")

    def test_a_tag_is_never_cached(self, app, db, journey, family, staff):
        """It carries a live pickup code."""
        page = check_in(staff, family, child(family))
        tag = staff.get(pdf_link(page.get_data(as_text=True)), headers=H)
        assert "no-store" in tag.headers.get("Cache-Control", "")


class TestTheLabelScreenSaysWhenItCannotPrint:
    """The silent no-op, which is what actually stopped tags coming out.

    The device behaviour cannot be exercised here. What can be asserted is
    that the page ships the machinery to notice and report it, because the
    previous version shipped a comment claiming the note said what to do when
    the note said something else entirely.
    """

    def test_the_page_carries_a_message_for_a_tablet_that_cannot_print(
        self, app, db, journey, family, staff
    ):
        body = check_in(staff, family, child(family)).get_data(as_text=True)
        assert "data-print-cannot" in body
        assert "cannot print from inside the app" in body

    def test_that_message_starts_hidden(self, app, db, journey, family, staff):
        """Everybody else must not see a warning about a case they are not in,
        including a device with JavaScript off."""
        body = check_in(staff, family, child(family)).get_data(as_text=True)
        block = re.search(r"<div class=\"alert error center\" data-print-cannot([^>]*)>",
                          body)
        assert block, "the warning is not in the markup"
        assert "hidden" in block.group(1)

    def test_it_says_where_to_go_instead(self, app, db, journey, family, staff):
        """A warning with no next step is a warning that gets ignored."""
        body = check_in(staff, family, child(family)).get_data(as_text=True)
        assert "Safari" in body
        assert JOURNEY_HOST in body

    def test_the_plugin_is_found_by_pattern_not_one_fixed_name(
        self, app, db, journey, family, staff
    ):
        """Hardcoding one spelling is how a working install gets reported as
        broken. The push check learned this; this page inherits the lesson."""
        body = check_in(staff, family, child(family)).get_data(as_text=True)
        assert "browser|inappbrowser|safari" in body


class TestThePrintCheck:
    def test_a_volunteer_can_reach_it(self, app, db, journey, staff):
        page = staff.get("/kids/kiosk/print-check/", headers=H)
        assert page.status_code == 200
        assert "Can this tablet print?" in page.get_data(as_text=True)

    def test_it_is_linked_from_the_kiosk(self, app, db, journey, family, staff):
        """It has to be reachable from the tablet itself. The kiosk account is
        kept out of Settings by design, and the test only means anything on
        the device."""
        db.session.add(CheckinSession(church_id=journey.id, name="Sunday",
                                      starts_at=utcnow()))
        db.session.commit()
        body = staff.get("/kids/kiosk/", headers=H).get_data(as_text=True)
        assert "/kids/kiosk/print-check/" in body

    def test_it_names_the_roll_the_church_is_set_to(self, app, db, journey, staff):
        """A tag across four labels is a roll setting, not a printer fault,
        and this is where somebody finds that out."""
        journey.kids_label_size = "dk1201"
        db.session.commit()
        body = staff.get("/kids/kiosk/print-check/", headers=H).get_data(as_text=True)
        assert "DK-1201" in body

    def test_it_offers_a_sample_to_actually_print(self, app, db, journey, staff):
        body = staff.get("/kids/kiosk/print-check/", headers=H).get_data(as_text=True)
        assert "/kids/tags/sample/" in body


class TestTheSampleTag:
    def _sample_url(self, staff):
        body = staff.get("/kids/kiosk/print-check/", headers=H).get_data(as_text=True)
        found = re.search(r'href="(/kids/tags/sample/[^"]+)"', body)
        assert found, "no sample link on the check page"
        return found.group(1)

    def test_it_is_a_pdf(self, app, db, journey, staff):
        tag = staff.get(self._sample_url(staff), headers=H)
        assert tag.status_code == 200
        assert tag.data.startswith(b"%PDF")

    def test_it_opens_without_a_session(self, app, db, journey, staff, client):
        """Same reason as a real tag: the point is to open it outside the app."""
        url = self._sample_url(staff)
        assert client.get(url, headers=H).status_code == 200

    def test_a_forged_token_gets_nothing(self, app, db, journey, client):
        assert client.get("/kids/tags/sample/not-a-token/", headers=H).status_code == 404

    def test_a_sample_token_cannot_be_used_as_a_real_tag(
        self, app, db, journey, staff
    ):
        """Separate salts, so the two can never be swapped. One of them
        carries a live pickup code and the other does not."""
        token = self._sample_url(staff).rsplit("/", 2)[1]
        assert staff.get(f"/kids/tags/pdf/{token}/", headers=H).status_code == 404

    def test_a_real_token_cannot_be_used_as_a_sample(
        self, app, db, journey, family, staff
    ):
        page = check_in(staff, family, child(family))
        token = pdf_link(page.get_data(as_text=True)).rsplit("/", 2)[1]
        assert staff.get(f"/kids/tags/sample/{token}/", headers=H).status_code == 404

    def test_another_church_cannot_open_it(self, app, db, journey, staff):
        """The church in the payload is checked against the host."""
        from tests.conftest import RIVERBEND_HOST

        url = self._sample_url(staff)
        other = staff.get(url, headers={"Host": RIVERBEND_HOST})
        assert other.status_code in (404, 302)

    def test_it_carries_nothing_real(self, app, db, journey, family, staff):
        """No pickup code, no child, no family. A leaked sample link discloses
        that this church owns a label printer."""
        from app.content import KIDS

        assert KIDS["sample_code"] == "SAMPLE"
        assert "real" in KIDS["sample_last"].lower()


class TestTheRollList:
    def test_it_names_rolls_not_printers(self):
        """A church with a QL-820NWB looked for its model here, did not find
        it, and concluded the printer was unsupported. Every Brother QL takes
        every roll on this list."""
        for size in SIZES:
            assert "QL-" not in size.label, size.label
            assert "NWB" not in size.label, size.label

    def test_the_setting_says_roll_rather_than_printer(self):
        from app.content import SETTINGS

        hint = SETTINGS["kiosk_label_hint"]
        assert "not the printer" in hint
        # Named outright, so the next person with one stops looking.
        assert "QL-820NWB" in hint

    def test_the_two_colour_roll_is_there(self):
        """DK-2251 is the same size as DK-2205 and is listed anyway, because
        somebody holding a DK-2251 box needs to find DK-2251."""
        assert "dk2251" in BY_CODE
        assert BY_CODE["dk2251"].width_mm == 62

    def test_nothing_is_wider_than_a_brother_ql_can_take(self):
        """62mm is the limit on every QL in this range. A roll wider than that
        on this list is a tag that cannot physically print."""
        for size in SIZES:
            if size.code == "letter":
                continue
            assert size.width_mm <= 62, size.label

    def test_every_roll_renders_a_tag(self, app, db, journey, family, staff):
        """A size that produces a broken PDF is worse than one that is missing,
        because it fails at the desk on a Sunday rather than in this list."""
        ellie = child(family)
        page = check_in(staff, family, ellie)
        assert page.status_code == 200

        for size in SIZES:
            journey.kids_label_size = size.code
            db.session.commit()
            rows = db.session.scalars(db.select(Checkin)).all()
            assert rows
            session = rows[0].session
            from app.labels import render_tags
            with app.test_request_context(headers=H):
                pdf = render_tags(checkins=rows, church=journey,
                                  checkin_session=session, size=size,
                                  when="Oct 8")
            assert pdf.startswith(b"%PDF"), size.code
            assert len(pdf) > 400, size.code

    def test_an_unknown_code_falls_back_rather_than_failing(self):
        """A church whose setting predates a rename prints the default rather
        than printing nothing."""
        assert size_for("dk9999").code == "dk1202"
        assert size_for(None).code == "dk1202"
        assert size_for("").code == "dk1202"
