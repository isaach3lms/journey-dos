"""The public help page.

Required by both app stores, and the only page a member can reach when the
thing they need help with is signing in. A help page behind a sign-in is the
one page guaranteed to be useless to the people who need it.
"""

import pytest

from app.models import Church
from tests.conftest import JOURNEY_HOST, RIVERBEND_HOST


@pytest.fixture
def journey(db):
    return db.session.scalar(db.select(Church).where(Church.slug == "journey"))


class TestItIsReachableWhenLockedOut:
    def test_it_needs_no_sign_in(self, client, journey):
        r = client.get("/support/", headers={"Host": JOURNEY_HOST})
        assert r.status_code == 200

    def test_the_sign_in_screen_links_to_it(self, client, journey):
        """Where somebody who cannot get in is actually standing."""
        r = client.get("/auth/login", headers={"Host": JOURNEY_HOST})
        assert b"/support/" in r.data

    def test_it_answers_the_sign_in_question_first(self, client, journey):
        """The most common reason anybody opens it."""
        body = client.get("/support/", headers={"Host": JOURNEY_HOST}).get_data(as_text=True)
        assert "I cannot sign in" in body
        assert "forgotten password" in body

    def test_it_links_back_to_sign_in(self, client, journey):
        r = client.get("/support/", headers={"Host": JOURNEY_HOST})
        assert b"/auth/login" in r.data


class TestItAnswersWhatPeopleActuallyAsk:
    def _body(self, client):
        return client.get("/support/", headers={"Host": JOURNEY_HOST}).get_data(as_text=True)

    def test_getting_an_account(self, client, journey):
        assert "I do not have an account" in self._body(client)

    def test_stopping_the_emails(self, client, journey):
        assert "fewer emails" in self._body(client)

    def test_deleting_an_account(self, client, journey):
        """Apple requires the app to offer it, so the help page has to say
        where it is."""
        body = self._body(client)
        assert "delete my account" in body
        assert "You screen" in body

    def test_the_kids_check_in_code(self, client, journey):
        assert "check-in code" in self._body(client)

    def test_giving_points_at_the_church_not_us(self, client, journey):
        """This app links out and never handles the payment."""
        body = self._body(client)
        assert "never handles the payment" in body


class TestContact:
    def test_it_names_a_real_address(self, client, journey):
        """An app store reviewer checks that support exists and is reachable."""
        r = client.get("/support/", headers={"Host": JOURNEY_HOST})
        assert b"isaac@betweensundaysconsulting.com" in r.data

    def test_it_sends_people_to_their_church_first(self, client, journey):
        """They hold the record and can change anything about it."""
        body = client.get("/support/", headers={"Host": JOURNEY_HOST}).get_data(as_text=True)
        assert f"Ask {journey.name} first" in body


class TestItBelongsToTheChurch:
    def test_it_carries_the_church_name(self, client, journey):
        r = client.get("/support/", headers={"Host": JOURNEY_HOST})
        assert journey.name.encode() in r.data

    def test_two_churches_get_two_pages(self, client, journey):
        """A page that says another church's name is a page a member does not
        trust."""
        mine = client.get("/support/", headers={"Host": JOURNEY_HOST}).data
        theirs = client.get("/support/", headers={"Host": RIVERBEND_HOST}).data
        assert mine != theirs

    def test_it_carries_the_church_brand(self, client, journey):
        r = client.get("/support/", headers={"Host": JOURNEY_HOST})
        assert b"--accent:#485B38;" in r.data

    def test_an_unmapped_host_gets_nothing(self, app, client):
        app.config["ALLOW_TENANT_QUERY_OVERRIDE"] = False
        app.config["PLATFORM_DOMAIN"] = ""
        r = client.get("/support/", headers={"Host": "nobody.example.org"})
        assert r.status_code == 404


class TestItPairsWithThePolicy:
    def test_support_links_to_privacy(self, client, journey):
        r = client.get("/support/", headers={"Host": JOURNEY_HOST})
        assert b"/privacy/" in r.data

    def test_privacy_links_to_support(self, client, journey):
        r = client.get("/privacy/", headers={"Host": JOURNEY_HOST})
        assert b"/support/" in r.data

    def test_the_policy_url_did_not_move(self, client, journey):
        """It is submitted to Apple and printed in emails. Renaming the
        blueprint must not change the address."""
        assert client.get("/privacy/", headers={"Host": JOURNEY_HOST}).status_code == 200


class TestNoColoursInTheMarkup:
    def test_the_public_templates_use_tokens(self):
        import re
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent / "app" / "templates" / "public"
        for path in root.rglob("*.html"):
            assert not re.search(r"#[0-9A-Fa-f]{6}\b", path.read_text()), path.name
