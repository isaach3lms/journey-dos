"""Settings as rows: closed until opened, and open again where you left off.

Eleven panels open at once was a wall of forms. Every setting is now a row
that opens in place, the way the member You tab works. Nothing moved to its
own address, so every link, redirect and test that pointed at this screen
still lands on it.
"""

import pytest

from app.blueprints.settings import ROWS
from app.models import Church, User
from tests.conftest import JOURNEY_HOST

H = {"Host": JOURNEY_HOST}


def page(client, query=""):
    return client.get("/settings/" + query, headers=H).data.decode()


def rows(client, query=""):
    body = page(client, query)
    return body[body.index('class="card setrows"'):body.index("</section>")]


class TestEverySettingIsARow:
    def test_all_of_them_are_there(self, db, staff):
        markup = rows(staff)
        for key in ROWS:
            assert f'<details class="setrow" id="{key}"' in markup, key

    def test_they_start_closed(self, db, staff):
        assert " open>" not in rows(staff)

    def test_each_row_says_what_it_holds_without_opening(self, db, staff):
        """The line under the name is the point: staff read it and decide
        whether to open the row at all."""
        markup = rows(staff)
        assert "The Journey Church &middot; America/Chicago" in markup \
            or "The Journey Church · America/Chicago" in markup
        assert "Only the church creates accounts" in markup
        assert "No license number saved" in markup

    def test_the_forms_are_still_on_the_page_inside_their_rows(self, db, staff):
        """Closed is not absent. A row is markup the browser has already
        loaded, so opening one is instant and needs no request."""
        markup = rows(staff)
        assert 'action="/settings/brand/"' in markup
        assert 'action="/settings/ccli/"' in markup
        assert 'action="/settings/accounts/"' in markup
        assert 'name="accent_hex"' in markup

    def test_nothing_became_its_own_address(self, db, staff):
        for key in ROWS:
            # 404 for a name nothing uses, 405 where a POST handler lives.
            assert staff.get(f"/settings/{key}/", headers=H).status_code in (404, 405)


class TestOpeningOne:
    @pytest.mark.parametrize("key", ROWS)
    def test_a_link_can_ask_for_a_row(self, db, staff, key):
        body = page(staff, f"?open={key}")
        assert f'id="{key}" open>' in body
        assert body.count(" open>") == 1

    @pytest.mark.parametrize("bad", ["<script>", "brand'", "nonsense", ""])
    def test_nonsense_opens_nothing(self, db, staff, bad):
        body = page(staff, f"?open={bad}")
        assert " open>" not in body
        assert "<script>alert" not in body


class TestSavingLeavesTheRowOpen:
    def test_branding(self, db, staff):
        response = staff.post("/settings/brand/",
                              data={"name": "The Journey Church", "timezone": "America/Chicago"},
                              headers=H, follow_redirects=True)
        assert b'id="brand" open>' in response.data

    def test_a_refused_brand_save(self, db, staff):
        """A rejected colour has to come back to the field that holds it."""
        response = staff.post("/settings/brand/", data={"accent_hex": "#FFFFAA"},
                              headers=H, follow_redirects=True)
        assert b'id="brand" open>' in response.data

    def test_the_ccli_number(self, db, staff):
        response = staff.post("/settings/ccli/", data={"ccli_license_number": "5731696"},
                              headers=H, follow_redirects=True)
        assert b'id="ccli" open>' in response.data
        assert b"License 5731696" in response.data

    def test_a_refused_ccli_number(self, db, staff):
        response = staff.post("/settings/ccli/", data={"ccli_license_number": "abc"},
                              headers=H, follow_redirects=True)
        assert b'id="ccli" open>' in response.data

    def test_self_signup(self, db, staff):
        response = staff.post("/settings/signup/", headers=H, follow_redirects=True)
        assert b'id="signup" open>' in response.data

    def test_member_announcements(self, db, staff):
        response = staff.post("/settings/announcements/", headers=H, follow_redirects=True)
        assert b'id="announcements" open>' in response.data

    def test_creating_an_account(self, db, staff):
        response = staff.post("/settings/accounts/",
                              data={"name": "Dana Reed", "email": "dana@example.com", "role": "leader"},
                              headers=H, follow_redirects=True)
        assert b'id="accounts" open>' in response.data
        assert b"Dana Reed" in response.data

    def test_a_refused_account(self, db, staff):
        response = staff.post("/settings/accounts/", data={"name": "", "email": ""},
                              headers=H, follow_redirects=True)
        assert b'id="accounts" open>' in response.data

    def test_switching_an_account_off(self, db, staff):
        user = db.session.scalar(
            db.select(User).where(User.email == "leader@journeychurchsemo.com")
        )
        response = staff.post(f"/settings/accounts/{user.id}/toggle/",
                              headers=H, follow_redirects=True)
        assert b'id="accounts" open>' in response.data

    def test_sending_a_test_email(self, db, staff):
        response = staff.post("/settings/email/test/", headers=H, follow_redirects=True)
        assert b'id="email" open>' in response.data


class TestTheLogFilter:
    def test_filtering_keeps_the_log_open(self, db, staff):
        body = page(staff, "?open=audit&action=sign_in")
        assert b'id="audit" open>' in body.encode()

    def test_a_filter_on_its_own_still_opens_the_log(self, db, staff):
        """An old bookmark from before the rows existed."""
        body = page(staff, "?action=sign_in")
        assert 'id="audit" open>' in body
        assert body.count(" open>") == 1

    def test_the_filter_carries_the_row_with_it(self, db, staff):
        markup = rows(staff)
        form = markup[markup.index('<form method="get"'):]
        assert '<input type="hidden" name="open" value="audit">' in form[:300]


class TestTheCountsInTheSummaries:
    def test_the_account_count(self, db, staff):
        total = len(db.session.scalars(User.for_church(1)).all())
        assert f"{total} people can sign in" in page(staff)

    def test_one_account_is_not_1_people(self, db, staff):
        church = db.session.scalar(db.select(Church).where(Church.slug == "journey"))
        for user in db.session.scalars(User.for_church(church.id)).all():
            if user.email != "pastor@journeychurchsemo.com":
                db.session.delete(user)
        db.session.commit()
        assert "1 person can sign in" in page(staff)

    def test_the_signup_line_follows_the_switch(self, db, staff):
        assert "Only the church creates accounts" in page(staff)
        staff.post("/settings/signup/", headers=H)
        assert "Anyone with your address can create their own login" in page(staff)

    def test_what_staff_type_is_escaped(self, db, staff):
        staff.post("/settings/brand/", data={"name": "<script>alert(1)</script>"}, headers=H)
        body = page(staff)
        assert "<script>alert(1)</script>" not in body
        assert "&lt;script&gt;" in body


class TestWhoCanSeeIt:
    def test_a_leader_cannot(self, db, leader):
        assert leader.get("/settings/", headers=H).status_code == 403

    def test_a_member_cannot(self, db, member):
        assert member.get("/settings/", headers=H).status_code == 403


class TestTheRowItself:
    """Measured in a browser, guarded here."""

    from pathlib import Path as _Path

    CSS = (_Path(__file__).resolve().parent.parent
           / "app" / "static" / "css" / "app.css").read_text()

    def rule(self, selector):
        block = self.CSS[self.CSS.index(selector):]
        return block[:block.index("}")]

    def test_the_default_triangle_is_gone(self):
        summary = self.rule(".setrow > summary{")
        assert "list-style:none" in summary
        assert ".setrow > summary::-webkit-details-marker{display:none}" in self.CSS

    def test_the_chevron_turns_when_the_row_opens(self):
        assert '.setrow[open] > summary .chev{transform:rotate(90deg)}' in self.CSS

    def test_the_line_under_the_name_is_not_hidden_on_a_phone(self):
        """A staff-roster rule hides .pe under 520px, and that line is the
        whole point of a row you have not opened."""
        assert "display:block" in self.rule(".setrow .pe{")

    def test_a_pill_cannot_squeeze_the_name(self):
        """Flex let 'Staff, leaders, and members' push 'Who can post
        church-wide announcements' into a three-line stack."""
        assert 'grid-template-areas:"text pill chev"' in self.rule(".setrow > summary{")

    def test_on_a_phone_the_pill_drops_under_the_name(self):
        assert 'grid-template-areas:"text chev" "pill chev"' in self.CSS

    def test_the_body_does_not_inherit_the_card_paragraph_rules(self):
        """.card .bd p sets its own size and colour. The row body is not
        .bd, so it carries them itself."""
        assert "font-size:13px" in self.rule(".setbd p{")
