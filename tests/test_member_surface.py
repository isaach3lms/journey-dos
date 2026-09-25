"""Two things a member sees on every screen: the greeting and the surface.

The greeting used to follow the server clock, which was wrong for anybody
in another timezone. The desktop page used to be bone while the phone was
white, so the same app looked like two products.
"""

from datetime import date, timedelta
from pathlib import Path

import pytest

from app.content import MEMBER
from app.models import Person, User
from app.models.base import utcnow
from tests.conftest import JOURNEY_HOST

H = {"Host": JOURNEY_HOST}
CSS = (Path(__file__).resolve().parent.parent / "app" / "static" / "css" / "app.css").read_text()


@pytest.fixture
def signed_in(db, client, sign_in):
    user = db.session.scalar(db.select(User).where(User.email == "member@journeychurchsemo.com"))
    person = Person(church_id=user.church_id, first_name="Isaac", last_name="Helms",
                    email=user.email, stage="member", approved_at=utcnow(),
                    first_seen_on=date.today() - timedelta(days=9))
    db.session.add(person)
    db.session.flush()
    user.person_id = person.id
    db.session.commit()
    sign_in("member@journeychurchsemo.com")
    return client


class TestTheGreeting:
    def test_it_says_welcome_back(self, db, signed_in):
        page = signed_in.get("/me/", headers=H).data
        assert b"Welcome back, Isaac" in page

    @pytest.mark.parametrize("hour", [3, 9, 13, 20, 23])
    def test_the_hour_does_not_change_it(self, db, signed_in, monkeypatch, hour):
        """No clock reading left anywhere: the same words at 3am and 8pm."""
        import app.blueprints.member as member
        real = member.datetime

        class Frozen(real):
            @classmethod
            def now(cls, tz=None):
                return real.now(tz).replace(hour=hour)

        monkeypatch.setattr(member, "datetime", Frozen)
        assert b"Welcome back, Isaac" in signed_in.get("/me/", headers=H).data

    def test_the_time_of_day_copy_is_gone(self):
        for stale in ("greeting_morning", "greeting_afternoon", "greeting_evening"):
            assert stale not in MEMBER

    def test_the_rest_of_the_line_is_untouched(self, db, signed_in):
        assert b"Day 10 with The Journey Church" in signed_in.get("/me/", headers=H).data

    def test_the_day_count_is_the_churchs_own_date(self, db, client, signed_in):
        """The greeting stopped reading a clock. This is the one that is left,
        and in UTC it rolled over at 7pm in Missouri."""
        from app.models import Church
        from app.timeutil import now_local

        church = db.session.scalar(db.select(Church).where(Church.slug == "journey"))
        for zone in ("America/Chicago", "Pacific/Honolulu", "Australia/Sydney"):
            church.timezone = zone
            db.session.commit()
            person = db.session.scalar(
                db.select(Person).where(Person.email == "member@journeychurchsemo.com")
            )
            expected = (now_local(church).date() - person.first_seen_on).days + 1
            page = client.get("/me/", headers=H).data.decode()
            assert f"Day {expected} with" in page, zone


class TestOneWhiteSurface:
    def desktop(self):
        block = CSS[CSS.index("@media (min-width:900px)"):]
        return block[:block.index("/* ---------- Reading ----------")]

    def test_the_phone_frame_is_white(self):
        """The base rule, which is what a member on a phone sees."""
        rule = CSS[CSS.index(".phone{"):]
        assert "background:var(--white)" in rule[:rule.index("}")]

    def test_the_desktop_page_is_white_too(self):
        desktop = self.desktop()
        frame = desktop[desktop.index(".memberfull .phone{"):]
        frame = frame[:frame.index("}")]
        assert "background:var(--white)" in frame
        assert "var(--bone)" not in frame

    def test_the_body_behind_it_is_white(self):
        block = self.desktop()
        rule = block[block.index("  .memberfull{"):]
        assert "background:var(--white)" in rule[:rule.index("}")]

    def test_no_bone_left_on_the_member_desktop(self):
        assert "var(--bone)" not in self.desktop()

    def test_the_chat_composer_matches_the_page(self):
        rule = CSS[CSS.index(".memberfull .composer{"):]
        assert "background:var(--white)" in rule[:rule.index("}")]

    def test_cards_keep_an_edge_against_it(self):
        """White on white needs a visible line or the cards disappear."""
        rule = self.desktop()
        rule = rule[rule.index(".memberfull .mcard{"):]
        assert "border-color:var(--line)" in rule[:rule.index("}")]

    def test_the_staff_preview_keeps_its_page_colour(self):
        """Staff see the app inside a phone frame, which needs something to
        sit against."""
        rule = CSS[CSS.index(".memberbody{"):]
        assert "background:var(--bone)" in rule[:rule.index("}")]
