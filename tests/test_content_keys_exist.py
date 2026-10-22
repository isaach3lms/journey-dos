"""Every piece of copy the code asks for by name actually exists.

The bug that forced this: discarding a connect card worked, committed, and
then raised `KeyError: 'guest_discarded'` reaching for the flash message. The
copy had been there the whole time under `guests_discarded`, the plural
prefix every other string on that screen uses. A staff member clearing an old
card got "Something broke on our end" for an action that had already
succeeded, so the natural next move was to try it again.

**Why nothing caught it.** A missing key in a template is silent: Jinja
renders an undefined as an empty string, so the worst case is a blank line.
In Python it is a KeyError, which is a 500, and the only way to find it is to
execute that exact line. These two lines run when somebody discards or
restores a card, which no test did and which happens about once a month in a
real church.

So this reads the source instead. Every `SOMEDICT["literal"]` in the app,
checked against the dictionary. It costs a second and it covers every screen
at once, including the ones with no test of their own, which is precisely
where this class of bug survives.

What it cannot see: keys built at runtime, like
`KIDS[f"first_time_{reason}"]`. Those are deliberately written with a
`.get(..., fallback)` so a miss is a worse message rather than a 500, and
there is a test below that the fallback exists.
"""

import ast
import pathlib
import re

import pytest

import app.content as content
import app.content as content_module

APP = pathlib.Path("app")


def content_dicts():
    return {
        name: value for name, value in vars(content).items()
        if name.isupper() and isinstance(value, dict)
    }


def python_files():
    return [
        path for path in sorted(APP.rglob("*.py"))
        if "__pycache__" not in str(path)
    ]


class TestEveryKeyExists:
    def test_no_python_file_asks_for_copy_that_is_not_there(self):
        dicts = content_dicts()
        assert dicts, "no content dictionaries found, so this test proves nothing"

        missing = []
        for path in python_files():
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if not isinstance(node, ast.Subscript):
                    continue
                if not isinstance(node.value, ast.Name):
                    continue
                if node.value.id not in dicts:
                    continue
                if not isinstance(node.slice, ast.Constant):
                    continue
                if not isinstance(node.slice.value, str):
                    continue
                key = node.slice.value
                if key not in dicts[node.value.id]:
                    missing.append(
                        f"{path}:{node.lineno} asks for "
                        f"{node.value.id}[{key!r}], which does not exist"
                    )

        assert not missing, (
            "these lines raise KeyError the moment they run, which is a 500 "
            "on whatever screen they are on:\n  " + "\n  ".join(missing)
        )

    def test_the_two_that_started_this_are_there(self):
        """Named, so a rename that reintroduces the bug says so."""
        assert content.PEOPLE["guests_discarded"]
        assert content.PEOPLE["guests_restored"]

    def test_a_key_built_at_runtime_has_a_fallback(self):
        """`KIDS[f"first_time_{reason}"]` cannot be checked statically, so
        the code that does it uses `.get` with a message that exists."""
        assert content.KIDS["first_time_needs_more"]
        source = pathlib.Path("app/blueprints/kids.py").read_text()
        assert 'KIDS.get(f"first_time_{refused.reason}"' in source, (
            "the refusal message is looked up by a key built at run time; "
            "if that stops using .get, an unknown reason becomes a 500"
        )


class TestDiscardingACard:
    """The screen the missing key was on. It had no test at all, which is
    how a line that raises on every execution shipped."""

    @pytest.fixture
    def card(self, db):
        from app import guests
        from app.models import Church

        journey = db.session.scalar(
            db.select(Church).where(Church.slug == "journey"))
        made = guests.submit(journey.id, first_name="Marcus", last_name="Webb",
                             phone="5551234567")
        db.session.commit()
        return made

    def test_discarding_one(self, db, client, sign_in, card):
        from tests.conftest import JOURNEY_HOST

        sign_in("pastor@journeychurchsemo.com")
        response = client.post(f"/people/guests/{card.id}/discard/",
                               headers={"Host": JOURNEY_HOST})

        assert response.status_code == 302, "discarding a card 500s"
        db.session.refresh(card)
        assert card.is_discarded

    def test_it_says_so(self, db, client, sign_in, card):
        from tests.conftest import JOURNEY_HOST

        sign_in("pastor@journeychurchsemo.com")
        response = client.post(f"/people/guests/{card.id}/discard/",
                               headers={"Host": JOURNEY_HOST},
                               follow_redirects=True)
        assert "Marcus Webb" in response.get_data(as_text=True)

    def test_putting_it_back(self, db, client, sign_in, card):
        from tests.conftest import JOURNEY_HOST

        sign_in("pastor@journeychurchsemo.com")
        H = {"Host": JOURNEY_HOST}
        client.post(f"/people/guests/{card.id}/discard/", headers=H)

        response = client.post(f"/people/guests/{card.id}/discard/",
                               data={"restore": "1"}, headers=H)
        assert response.status_code == 302, "restoring a card 500s"
        db.session.refresh(card)
        assert not card.is_discarded

    def test_the_person_stays_on_the_roster(self, db, client, sign_in, card):
        """A card and a roster record are different things. Clearing junk
        off a list must not remove somebody from the church."""
        from tests.conftest import JOURNEY_HOST
        from app.models import Person

        person_id = card.person_id
        sign_in("pastor@journeychurchsemo.com")
        client.post(f"/people/guests/{card.id}/discard/",
                    headers={"Host": JOURNEY_HOST})

        assert db.session.get(Person, person_id) is not None

    def test_another_churchs_card_is_not_reachable(self, db, client, sign_in, card):
        from tests.conftest import RIVERBEND_HOST

        sign_in("pastor@journeychurchsemo.com")
        response = client.post(f"/people/guests/{card.id}/discard/",
                               headers={"Host": RIVERBEND_HOST})
        assert response.status_code in (302, 403, 404)
        db.session.refresh(card)
        assert not card.is_discarded


# ---------------------------------------------------------------------------
# The same question, asked of the templates
# ---------------------------------------------------------------------------
#
# The AST walk above only sees Python. A template reaches its copy through an
# attribute, `{{ content.item_save }}`, which Jinja resolves at render time
# and turns into an empty string when it misses. Nothing raises, nothing logs,
# and the screen simply has a blank where a label should be.
#
# That is not hypothetical. Adding the row editor to the plan screen, the save
# button was written as `content.flag_save`, which exists in PEOPLE and not in
# SERVICES, and shipped as a green button with no words on it. Running this
# check for the first time also turned up `content.headcount_heading`, missing
# since the attendance card was built, which had been rendering an empty <h2>
# above the box ever since.
#
# Every blueprint passes exactly one dictionary as `content`, and every
# template directory belongs to one blueprint, so the directory says which
# dictionary its `content` is. Partials are left out on purpose: they are
# included from several shells and their `content` is whichever one included
# them.

TEMPLATE_CONTENT = {
    "services": "SERVICES",
    "people": "PEOPLE",
    "member": "MEMBER",
    "settings": "SETTINGS",
    "kids": "KIDS",
    "giving": "GIVING",
    "groups": "GROUPS",
    "resources": "RESOURCES",
    "auth": "AUTH",
    "messages": "MESSAGES",
    "pwa": "PWA",
    "unsubscribe": "EMAIL",
    # The volunteer's two-tap answer page. Served by its own blueprint, but
    # the copy belongs to the plan that invited them.
    "invite": "SERVICES",
}

# Directories whose `content` genuinely cannot be known from the directory,
# with the reason. Anything not here and not above fails the check below, so
# a new screen cannot quietly opt out.
UNMAPPED = {
    # Four shells share this directory: the welcome page, the privacy page,
    # the community rules and the account-request form, each passing its own
    # dictionary.
    "public": "several blueprints, each with its own dictionary",
    # The staff dashboard renders with PEOPLE, SHELL and STUCK depending on
    # the panel.
    "shell": "three dictionaries across one screen",
    # Built by `flask build-error-pages` into static HTML with no context at
    # all. The 500 page must not touch the database, which is why.
    "errors": "rendered without a content dictionary",
}

USES_CONTENT = re.compile(r"\bcontent\.([a-z_][a-z0-9_]*)\b")


@pytest.mark.parametrize("folder,dict_name", sorted(TEMPLATE_CONTENT.items()))
def test_every_label_a_template_asks_for_exists(folder, dict_name):
    """A missing key here is a blank space on a real screen.

    Worth one assertion message per directory rather than one for the lot:
    the failure names the template, the key and the dictionary it should have
    been in, which is the whole of what somebody needs to fix it.
    """
    source = getattr(content_module, dict_name)
    root = pathlib.Path(__file__).resolve().parents[1] / "app" / "templates" / folder

    missing = []
    for path in sorted(root.rglob("*.html")):
        for name in sorted(set(USES_CONTENT.findall(path.read_text()))):
            if name not in source:
                missing.append(f"{path.name} asks for {dict_name}[{name!r}]")

    assert not missing, (
        "copy a template renders by name does not exist, so it renders as "
        "nothing at all:\n  " + "\n  ".join(missing)
    )


def test_the_check_knows_about_every_template_directory():
    """A new screen's directory must be added to the map above.

    Without this, somebody adding `app/templates/events/` gets no check at
    all on that screen's copy and does not find out, which is exactly the
    silence this file exists to end.
    """
    root = pathlib.Path(__file__).resolve().parents[1] / "app" / "templates"
    folders = {
        path.name for path in root.iterdir()
        if path.is_dir() and path.name != "partials"
        and any(path.rglob("*.html"))
    }
    unchecked = folders - set(TEMPLATE_CONTENT) - set(UNMAPPED)
    assert not unchecked, (
        f"template directories with no content dictionary mapped: "
        f"{sorted(unchecked)}. Add them to TEMPLATE_CONTENT, or to UNMAPPED "
        f"with the reason their `content` cannot be known from the directory."
    )
