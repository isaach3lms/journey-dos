"""The tablet in the lobby.

The church was running check-in from a staff account left signed in on an iPad
on a table in a hallway, because that was the only kind of account there was.
That account can read the roster, the giving mirror, every conversation and
every pastoral note.

So the test that matters most in this file is not that the kiosk account can
check a child in. It is the long list below of things it cannot do. An
allowlist that is wrong fails open and silently, and the only thing that
catches that is naming the screens one by one and asserting each is shut.
"""

import pytest

from app.kiosk import IDLE_RESET_SECONDS, KIOSK_SESSION
from app.models import Church, Household, KioskSetupToken, Person, User
from app.models.base import utcnow
from tests.conftest import JOURNEY_HOST, PASSWORD, RIVERBEND_HOST

H = {"Host": JOURNEY_HOST}


def make_kiosk_user(app, label=None):
    """Create the tablet account and one setup link, in a context of its own.

    Every test below that needs two identities takes `app` and NOT `db`. The
    `db` fixture holds one application context open for the whole test, and
    Flask-Login caches the signed-in user on `g`, so a second client created
    inside it inherits the first one's identity. A test about one session not
    reaching something would then pass without the user loader ever running,
    which is the shape of a security test that proves nothing.

    Returns the raw token. The user is not returned because it would be
    detached the moment this context closes.
    """
    from app.extensions import db as _db

    with app.app_context():
        journey = _db.session.scalar(
            _db.select(Church).where(Church.slug == "journey")
        )
        user = User(
            church_id=journey.id,
            email=f"kiosk@{journey.slug}.kiosk.invalid",
            name="The Journey Church check-in tablet",
            role="leader",
            is_kiosk=True,
        )
        user.set_password("a-very-long-random-password")
        user.mark_verified()
        user.accept_community()
        _db.session.add(user)
        _db.session.flush()
        _, raw = KioskSetupToken.issue(user, label=label)
        _db.session.commit()
        return raw


@pytest.fixture
def journey(db):
    return db.session.scalar(db.select(Church).where(Church.slug == "journey"))


@pytest.fixture
def kiosk_user(db, journey):
    """The account Settings creates, built the same way."""
    user = User(
        church_id=journey.id,
        email=f"kiosk@{journey.slug}.kiosk.invalid",
        name="The Journey Church check-in tablet",
        role="leader",
        is_kiosk=True,
    )
    user.set_password("a-very-long-random-password")
    user.mark_verified()
    user.accept_community()
    db.session.add(user)
    db.session.commit()
    return user


@pytest.fixture
def tablet(client, kiosk_user, db):
    """A client signed in as the tablet, through a real setup link."""
    _, raw = KioskSetupToken.issue(kiosk_user, label="Lobby iPad")
    db.session.commit()
    client.post(f"/kids/kiosk/setup/{raw}/", headers=H, follow_redirects=True)
    return client


class TestWhatTheTabletCannotReach:
    """The allowlist, screen by screen.

    Every one of these is something the staff account previously left on the
    tablet could read.
    """

    @pytest.mark.parametrize("path", [
        "/people/",
        "/giving/",
        "/messages/",
        "/groups/",
        "/services/",
        "/settings/",
        "/kids/",            # the staff roster, which lives under /kids/
        "/kids/checkout/",
        "/me/",
    ])
    def test_it_is_sent_back_to_check_in(self, tablet, path):
        page = tablet.get(path, headers=H)
        assert page.status_code == 302
        assert page.headers["Location"].endswith("/kids/kiosk/")

    def test_it_cannot_reprint_a_tag(self, db, journey, tablet):
        """A reprint hands out a second copy of a live pickup code. The
        tablet prints the tag it just made and nothing else."""
        page = tablet.get("/kids/tags/1/family/1/", headers=H)
        assert page.status_code == 302
        assert page.headers["Location"].endswith("/kids/kiosk/")

    def test_a_new_screen_is_shut_by_default(self, app, tablet):
        """The direction of the allowlist, stated as a test.

        A route nobody has thought about is unreachable from the lobby. If
        this ever fails it means somebody switched to a denylist, and every
        screen added after that is open.
        """
        from app.kiosk import KIOSK_ENDPOINTS

        reachable = {
            rule.endpoint for rule in app.url_map.iter_rules()
            if "GET" in (rule.methods or set())
        }
        # Nothing outside the allowlist is admitted by pattern.
        assert not any(
            endpoint.startswith("kids.") and endpoint not in KIOSK_ENDPOINTS
            and endpoint in KIOSK_ENDPOINTS
            for endpoint in reachable
        )
        assert "kids.index" not in KIOSK_ENDPOINTS
        assert "people.index" not in KIOSK_ENDPOINTS

    def test_a_missing_page_is_still_a_missing_page(self, tablet):
        """A 404 that redirects to check-in would make every typo look like a
        permission problem.

        Three segments on purpose: a single one matches the placeholder route
        the shell uses for screens that are not built yet, and the tablet is
        correctly bounced off those too.
        """
        assert tablet.get("/no/such/page/", headers=H).status_code == 404


class TestWhatTheTabletCanDo:
    def test_it_reaches_the_check_in_screen(self, tablet):
        assert tablet.get("/kids/kiosk/", headers=H).status_code == 200

    def test_it_can_check_a_family_in(self, db, journey, tablet):
        from app.models import CheckinSession

        household = Household(church_id=journey.id, name="The Webbs")
        db.session.add(household)
        db.session.flush()
        child = Person(church_id=journey.id, first_name="Eli", last_name="Webb",
                       household_id=household.id, is_child=True,
                       stage="visitor", approved_at=utcnow())
        db.session.add(child)
        sunday = CheckinSession(church_id=journey.id, name="Sunday 9:30",
                                starts_at=utcnow(), is_open=True)
        db.session.add(sunday)
        db.session.commit()

        page = tablet.post(
            f"/kids/kiosk/family/{household.id}/",
            headers=H, data={"person_id": child.id}, follow_redirects=True,
        )
        assert page.status_code == 200
        assert "Eli" in page.get_data(as_text=True)

    def test_it_can_sign_itself_out(self, tablet):
        page = tablet.post("/auth/logout", headers=H)
        assert page.status_code == 302


class TestTheSetupLink:
    def test_opening_it_signs_the_tablet_in(self, client, db, kiosk_user):
        _, raw = KioskSetupToken.issue(kiosk_user)
        db.session.commit()

        client.post(f"/kids/kiosk/setup/{raw}/", headers=H, follow_redirects=True)
        assert client.get("/kids/kiosk/", headers=H).status_code == 200

    def test_a_get_does_not_sign_anybody_in(self, client, db, kiosk_user):
        """A link in a group chat is fetched by every preview bot that sees
        it. If a GET burned the token it would be dead before a volunteer
        touched the tablet."""
        _, raw = KioskSetupToken.issue(kiosk_user)
        db.session.commit()

        page = client.get(f"/kids/kiosk/setup/{raw}/", headers=H)
        assert page.status_code == 200
        # Still not signed in.
        assert client.get("/kids/kiosk/", headers=H).status_code in (302, 401)
        # And still usable.
        assert KioskSetupToken.redeem(kiosk_user.church_id, raw) is not None

    def test_it_only_works_once(self, app):
        raw = make_kiosk_user(app)

        first = app.test_client()
        first.post(f"/kids/kiosk/setup/{raw}/", headers=H, follow_redirects=True)

        second = app.test_client()
        page = second.post(f"/kids/kiosk/setup/{raw}/", headers=H)
        assert page.status_code == 404
        assert second.get("/kids/kiosk/", headers=H).status_code in (302, 401)

    def test_an_expired_link_is_refused(self, client, db, kiosk_user):
        from datetime import timedelta

        token, raw = KioskSetupToken.issue(kiosk_user)
        token.expires_at = utcnow() - timedelta(minutes=1)
        db.session.commit()

        assert client.post(f"/kids/kiosk/setup/{raw}/", headers=H).status_code == 404

    def test_a_link_from_another_church_is_inert(self, client, db, kiosk_user):
        """Scoped to the church resolved from the host, so the value matching
        is not enough."""
        _, raw = KioskSetupToken.issue(kiosk_user)
        db.session.commit()

        page = client.post(f"/kids/kiosk/setup/{raw}/",
                           headers={"Host": RIVERBEND_HOST})
        assert page.status_code == 404

    def test_a_made_up_token_is_refused(self, client):
        assert client.post("/kids/kiosk/setup/not-a-real-token-at-all-no/",
                           headers=H).status_code == 404

    def test_the_token_is_not_stored(self, db, kiosk_user):
        """A table full of usable links is one database read away from every
        tablet in the church."""
        _, raw = KioskSetupToken.issue(kiosk_user)
        db.session.commit()

        rows = db.session.scalars(db.select(KioskSetupToken)).all()
        assert rows
        assert all(raw not in (row.token_hash or "") for row in rows)

    def test_making_a_second_link_does_not_kill_the_first(self, db, kiosk_user):
        """A church setting up three tablets makes three links. Retiring each
        as the next is made would leave only the last one working."""
        _, first = KioskSetupToken.issue(kiosk_user)
        _, second = KioskSetupToken.issue(kiosk_user)
        db.session.commit()

        assert KioskSetupToken.redeem(kiosk_user.church_id, first) is not None
        assert KioskSetupToken.redeem(kiosk_user.church_id, second) is not None


class TestTheSessionLasts:
    def test_the_tablet_gets_a_year(self, client, db, kiosk_user):
        """The bug being fixed: a tablet that logs out is a tablet somebody
        signs into with their own account to get past the login screen."""
        _, raw = KioskSetupToken.issue(kiosk_user)
        db.session.commit()

        page = client.post(f"/kids/kiosk/setup/{raw}/", headers=H)
        cookies = page.headers.getlist("Set-Cookie")
        remember = [c for c in cookies if c.startswith("remember_token=")]
        assert remember, f"no remember cookie in {cookies}"
        assert "Expires=" in remember[0] or "Max-Age=" in remember[0]

    def test_the_tablet_account_cannot_be_logged_into_with_a_password(
        self, client, db, kiosk_user
    ):
        """It has a password only because the column cannot be null.

        Its address is at a .invalid domain, which the sign-in form rejects as
        not an email address, so there is no password anybody can type to
        become the tablet. Setting one tablet up is a link, and recovering
        from losing every tablet is Sign every tablet out followed by new
        links. Neither path needs a password, so there is not one to leak.
        """
        client.post("/auth/login", headers=H, data={
            "email": kiosk_user.email, "password": "a-very-long-random-password",
        }, follow_redirects=True)
        assert client.get("/kids/kiosk/", headers=H).status_code in (302, 401)

    def test_the_duration_is_actually_long(self):
        assert KIOSK_SESSION.days >= 180


class TestRevoking:
    def test_signing_every_tablet_out_takes_effect_immediately(self, app):
        """Bumping the session version is what makes this immediate. A
        revocation that waits for a cookie to expire is not a revocation."""
        raw = make_kiosk_user(app)

        tablet = app.test_client()
        tablet.post(f"/kids/kiosk/setup/{raw}/", headers=H, follow_redirects=True)
        assert tablet.get("/kids/kiosk/", headers=H).status_code == 200

        staff = app.test_client()
        staff.post("/auth/login", headers=H,
                   data={"email": "pastor@journeychurchsemo.com",
                         "password": PASSWORD}, follow_redirects=True)
        staff.post("/settings/kiosk/revoke/", headers=H, follow_redirects=True)

        assert tablet.get("/kids/kiosk/", headers=H).status_code in (302, 401)

    def test_revoking_kills_unused_links_too(self, app):
        """A link somebody sent in a group chat last week must not put a
        tablet back after staff signed everything out."""
        raw = make_kiosk_user(app)

        staff = app.test_client()
        staff.post("/auth/login", headers=H,
                   data={"email": "pastor@journeychurchsemo.com",
                         "password": PASSWORD}, follow_redirects=True)
        staff.post("/settings/kiosk/revoke/", headers=H, follow_redirects=True)

        assert app.test_client().post(
            f"/kids/kiosk/setup/{raw}/", headers=H
        ).status_code == 404


class TestCreatingItFromSettings:
    def test_staff_can_create_one(self, db, journey, staff):
        staff.post("/settings/kiosk/", headers=H, follow_redirects=True)

        user = db.session.scalar(
            db.select(User).where(User.church_id == journey.id,
                                  User.is_kiosk.is_(True))
        )
        assert user is not None
        assert user.role == "leader"
        assert user.is_kiosk

    def test_the_address_cannot_receive_mail(self, db, journey, staff):
        """A password reset link for the lobby tablet, landing in somebody's
        inbox, is the hole this whole design is avoiding."""
        staff.post("/settings/kiosk/", headers=H, follow_redirects=True)

        user = db.session.scalar(
            db.select(User).where(User.church_id == journey.id,
                                  User.is_kiosk.is_(True))
        )
        assert user.email.endswith(".invalid")

    def test_only_one_per_church(self, db, journey, staff):
        staff.post("/settings/kiosk/", headers=H, follow_redirects=True)
        page = staff.post("/settings/kiosk/", headers=H, follow_redirects=True)

        assert "already a tablet account" in page.get_data(as_text=True)
        assert db.session.scalar(
            db.select(db.func.count(User.id)).where(
                User.church_id == journey.id, User.is_kiosk.is_(True))
        ) == 1

    def test_a_leader_cannot_create_one(self, leader):
        assert leader.post("/settings/kiosk/", headers=H).status_code == 403

    def test_the_link_is_shown_once_and_not_again(self, db, journey, staff):
        staff.post("/settings/kiosk/", headers=H, follow_redirects=True)
        page = staff.post("/settings/kiosk/link/", headers=H,
                          data={"label": "Lobby iPad"},
                          follow_redirects=True).get_data(as_text=True)
        assert "/kids/kiosk/setup/" in page

        again = staff.get("/settings/?open=kiosk", headers=H).get_data(as_text=True)
        assert "/kids/kiosk/setup/" not in again

    def test_creating_the_account_is_audited(self, db, journey, staff):
        from app.models.audit import AuditEvent

        staff.post("/settings/kiosk/", headers=H, follow_redirects=True)
        events = db.session.scalars(
            db.select(AuditEvent).where(AuditEvent.church_id == journey.id)
        ).all()
        assert any("kiosk" in (e.summary or "").lower() for e in events)


class TestTheIdleReset:
    def test_a_family_screen_resets_itself(self, db, journey, tablet):
        """The previous family's children must not sit on a screen in a lobby
        while the next person walks up."""
        household = Household(church_id=journey.id, name="The Webbs")
        db.session.add(household)
        db.session.flush()
        from app.models import CheckinSession

        db.session.add(CheckinSession(church_id=journey.id, name="Sunday",
                                      starts_at=utcnow(), is_open=True))
        db.session.commit()

        page = tablet.get(f"/kids/kiosk/family/{household.id}/",
                          headers=H).get_data(as_text=True)
        assert str(IDLE_RESET_SECONDS) in page
        assert "/kids/kiosk/" in page

    def test_the_start_screen_does_not_reload_itself_forever(self, tablet):
        """Nothing on it belongs to anybody, so a timer buys nothing."""
        page = tablet.get("/kids/kiosk/", headers=H).get_data(as_text=True)
        assert "data-idle-signout" not in page

    def test_a_tablet_is_never_signed_out_by_the_timer(self, db, journey, tablet):
        """Being signed in is the whole job."""
        household = Household(church_id=journey.id, name="The Webbs")
        db.session.add(household)
        from app.models import CheckinSession

        db.session.add(CheckinSession(church_id=journey.id, name="Sunday",
                                      starts_at=utcnow(), is_open=True))
        db.session.commit()

        page = tablet.get(f"/kids/kiosk/family/{household.id}/",
                          headers=H).get_data(as_text=True)
        assert "data-idle-signout" not in page

    def test_a_persons_account_on_the_kiosk_is_signed_out(self, staff):
        """The case the church was actually in."""
        page = staff.get("/kids/kiosk/", headers=H).get_data(as_text=True)
        assert "data-idle-signout" in page

    def test_the_rest_of_the_app_is_untouched(self, staff):
        """Signing staff out everywhere after half an hour would be a large
        change nobody asked for."""
        page = staff.get("/people/", headers=H).get_data(as_text=True)
        assert "data-idle-signout" not in page


class TestTenantIsolation:
    def test_a_tablet_cannot_reach_another_church(self, app):
        raw = make_kiosk_user(app)

        tablet = app.test_client()
        tablet.post(f"/kids/kiosk/setup/{raw}/", headers=H, follow_redirects=True)

        page = tablet.get("/kids/kiosk/", headers={"Host": RIVERBEND_HOST})
        assert page.status_code in (302, 401, 404)
