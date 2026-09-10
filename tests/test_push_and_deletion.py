"""Account deletion, the privacy policy, and push notifications.

The store requires the first two. The third is the reason a native app is worth
having at all. All three touch things that are hard to undo, so the tests lean
on what must *not* happen.
"""

import pytest

from app.models import (
    AuditEvent,
    Checkin,
    CheckinSession,
    Church,
    Household,
    Person,
    PushSubscription,
    User,
)
from app.models.base import utcnow
from app.push import MemoryPushTransport, PushMessage, PushFailed, SubscriptionGone, notify
from app.push.transport import MAX_BODY, MAX_TITLE, WebPushTransport, build_push_transport
from tests.conftest import JOURNEY_HOST, PASSWORD

MEMBER_EMAIL = "member@journeychurchsemo.com"


@pytest.fixture
def journey(db):
    church = db.session.scalar(db.select(Church).where(Church.slug == "journey"))
    church.timezone = "America/Chicago"
    db.session.commit()
    return church


@pytest.fixture
def linked(db, journey):
    person = Person(
        church_id=journey.id, first_name="Alicia", last_name="Romero",
        email=MEMBER_EMAIL, stage="attender",
    )
    db.session.add(person)
    db.session.flush()
    user = db.session.scalar(
        db.select(User).where(User.email == MEMBER_EMAIL, User.church_id == journey.id)
    )
    user.person_id = person.id
    db.session.commit()
    return person


def subscribe(db, journey, user, endpoint="https://push.example.com/abc", label="Phone"):
    subscription = PushSubscription.register(
        journey.id, user, endpoint=endpoint, p256dh="p" * 40, auth="a" * 20, label=label
    )
    db.session.commit()
    return subscription


class TestPrivacyPolicy:
    def test_it_is_public(self, client, journey):
        """A store review team reads it without an account."""
        r = client.get("/privacy/", headers={"Host": JOURNEY_HOST})
        assert r.status_code == 200

    def test_it_names_the_church(self, client, journey):
        r = client.get("/privacy/", headers={"Host": JOURNEY_HOST})
        assert b"The Journey Church" in r.data

    def test_it_declares_children_data(self, client, journey):
        """Apple asks about this specifically, and the app does collect it."""
        r = client.get("/privacy/", headers={"Host": JOURNEY_HOST})
        body = r.get_data(as_text=True).lower()
        assert "child" in body
        assert "check" in body

    def test_it_declares_giving_data(self, client, journey):
        r = client.get("/privacy/", headers={"Host": JOURNEY_HOST})
        assert b"Giving" in r.data

    def test_it_states_what_is_never_collected(self, client, journey):
        """The claims the rest of the codebase is built to keep."""
        body = client.get("/privacy/", headers={"Host": JOURNEY_HOST}).get_data(as_text=True)
        lowered = body.lower()
        assert "card" in lowered
        assert "location" in lowered
        assert "advertis" in lowered

    def test_it_tells_people_how_to_delete_their_account(self, client, journey):
        """Required alongside the deletion feature itself."""
        body = client.get("/privacy/", headers={"Host": JOURNEY_HOST}).get_data(as_text=True)
        assert "delete your account yourself" in body.lower()

    def test_it_carries_a_date(self, client, journey):
        assert b"Last updated" in client.get(
            "/privacy/", headers={"Host": JOURNEY_HOST}
        ).data

    def test_the_you_tab_links_to_it(self, db, linked, member):
        r = member.get("/me/you/", headers={"Host": JOURNEY_HOST})
        assert b"/privacy/" in r.data

    def test_it_is_scoped_per_church(self, db, client, journey):
        from tests.conftest import RIVERBEND_HOST

        mine = client.get("/privacy/", headers={"Host": JOURNEY_HOST}).data
        theirs = client.get("/privacy/", headers={"Host": RIVERBEND_HOST}).data
        assert mine != theirs


class TestAccountDeletion:
    def test_a_member_can_delete_their_own_account(self, db, journey, linked, member):
        user_id = db.session.scalar(
            db.select(User.id).where(User.email == MEMBER_EMAIL, User.church_id == journey.id)
        )
        r = member.post(
            "/me/you/delete/",
            data={"password": PASSWORD},
            headers={"Host": JOURNEY_HOST},
        )
        assert r.status_code == 200
        assert b"Your account is deleted" in r.data
        assert db.session.get(User, user_id) is None

    def test_it_needs_the_password(self, db, journey, linked, member):
        """A phone left unlocked on a table is the normal case, and this is
        not undoable."""
        r = member.post(
            "/me/you/delete/",
            data={"password": "not-the-password"},
            headers={"Host": JOURNEY_HOST},
            follow_redirects=True,
        )
        assert b"not right" in r.data
        assert db.session.scalar(
            db.select(User).where(User.email == MEMBER_EMAIL, User.church_id == journey.id)
        ) is not None

    def test_the_roster_record_survives(self, db, journey, linked, member):
        """The record belongs to the church, like a paper roll would."""
        member.post(
            "/me/you/delete/", data={"password": PASSWORD}, headers={"Host": JOURNEY_HOST}
        )
        db.session.expire_all()
        assert Person.get_for_church(journey.id, linked.id) is not None

    def test_a_childs_check_in_history_survives(self, db, journey, linked, member):
        """Deleting it would erase a safety record the church has to keep."""
        household = Household(church_id=journey.id, name="The Romeros")
        db.session.add(household)
        db.session.flush()
        child = Person(
            church_id=journey.id, first_name="Mateo", last_name="Romero",
            stage="member", household_id=household.id, is_child=True,
        )
        session = CheckinSession(
            church_id=journey.id, name="Sunday", starts_at=utcnow()
        )
        db.session.add_all([child, session])
        db.session.flush()
        db.session.add(
            Checkin(
                church_id=journey.id, session_id=session.id, person_id=child.id,
                household_id=household.id, pickup_code="ABCD",
            )
        )
        db.session.commit()

        member.post(
            "/me/you/delete/", data={"password": PASSWORD}, headers={"Host": JOURNEY_HOST}
        )
        db.session.expire_all()
        assert db.session.scalars(db.select(Checkin)).all() != []

    def test_it_is_audited(self, db, journey, linked, member):
        member.post(
            "/me/you/delete/", data={"password": PASSWORD}, headers={"Host": JOURNEY_HOST}
        )
        db.session.expire_all()
        events = db.session.scalars(AuditEvent.recent(journey.id)).all()
        assert any("deleted their own account" in e.summary for e in events)

    def test_their_devices_stop_receiving_notifications(self, db, journey, linked, member):
        user = db.session.scalar(
            db.select(User).where(User.email == MEMBER_EMAIL, User.church_id == journey.id)
        )
        subscribe(db, journey, user)

        member.post(
            "/me/you/delete/", data={"password": PASSWORD}, headers={"Host": JOURNEY_HOST}
        )
        db.session.expire_all()
        assert db.session.scalars(db.select(PushSubscription)).all() == []

    def test_they_are_signed_out(self, db, journey, linked, member):
        member.post(
            "/me/you/delete/", data={"password": PASSWORD}, headers={"Host": JOURNEY_HOST}
        )
        r = member.get("/me/", headers={"Host": JOURNEY_HOST})
        assert r.status_code == 302
        assert "/auth/login" in r.headers["Location"]

    def test_the_last_staff_account_cannot_delete_itself(self, db, journey, staff):
        """It would lock the church out of its own data with nobody able to
        undo it."""
        for user in db.session.scalars(
            db.select(User).where(User.church_id == journey.id, User.role == "staff")
        ):
            if user.email != "pastor@journeychurchsemo.com":
                user.is_active_account = False
        db.session.commit()

        r = staff.post(
            "/me/you/delete/",
            data={"password": PASSWORD},
            headers={"Host": JOURNEY_HOST},
            follow_redirects=True,
        )
        assert b"only staff account" in r.data
        assert db.session.scalar(
            db.select(User).where(User.email == "pastor@journeychurchsemo.com")
        ) is not None

    def test_a_second_staff_account_makes_deletion_possible(self, db, journey, staff):
        second = User(
            church_id=journey.id, email="second@journeychurchsemo.com",
            name="Second Staff", role="staff",
        )
        second.set_password(PASSWORD)
        second.mark_verified()
        db.session.add(second)
        db.session.commit()

        r = staff.post(
            "/me/you/delete/", data={"password": PASSWORD}, headers={"Host": JOURNEY_HOST}
        )
        assert b"Your account is deleted" in r.data

    def test_a_signed_out_visitor_cannot_delete_anything(self, client, journey):
        r = client.post("/me/you/delete/", headers={"Host": JOURNEY_HOST})
        assert r.status_code == 302
        assert "/auth/login" in r.headers["Location"]

    def test_the_screen_says_what_it_does_and_does_not_remove(self, db, linked, member):
        """The alternative is implying the church forgets them, which it does
        not."""
        r = member.get("/me/you/", headers={"Host": JOURNEY_HOST})
        body = r.get_data(as_text=True)
        assert "Delete your account" in body
        assert "belongs to the church" in body


class TestPushSubscriptions:
    def test_registering_a_device(self, db, journey, linked, member):
        r = member.post(
            "/me/you/push/",
            json={
                "endpoint": "https://push.example.com/abc",
                "keys": {"p256dh": "p" * 40, "auth": "a" * 20},
                "label": "iPhone",
            },
            headers={"Host": JOURNEY_HOST},
        )
        assert r.status_code == 200
        assert db.session.scalars(db.select(PushSubscription)).one().label == "iPhone"

    def test_a_subscription_without_keys_is_refused(self, db, linked, member):
        """Without both keys a payload cannot be encrypted, so the row would
        be one that can never be used."""
        r = member.post(
            "/me/you/push/",
            json={"endpoint": "https://push.example.com/abc", "keys": {}},
            headers={"Host": JOURNEY_HOST},
        )
        assert r.status_code == 400
        assert db.session.scalars(db.select(PushSubscription)).all() == []

    def test_resubscribing_the_same_browser_updates_rather_than_duplicates(
        self, db, journey, linked, member
    ):
        payload = {
            "endpoint": "https://push.example.com/abc",
            "keys": {"p256dh": "p" * 40, "auth": "a" * 20},
            "label": "iPhone",
        }
        member.post("/me/you/push/", json=payload, headers={"Host": JOURNEY_HOST})
        payload["keys"]["p256dh"] = "q" * 40
        member.post("/me/you/push/", json=payload, headers={"Host": JOURNEY_HOST})

        subscriptions = db.session.scalars(db.select(PushSubscription)).all()
        assert len(subscriptions) == 1
        assert subscriptions[0].p256dh == "q" * 40

    def test_one_person_may_have_several_devices(self, db, journey, linked, member):
        for index, label in enumerate(("Phone", "Tablet", "Laptop")):
            member.post(
                "/me/you/push/",
                json={
                    "endpoint": f"https://push.example.com/{index}",
                    "keys": {"p256dh": "p" * 40, "auth": "a" * 20},
                    "label": label,
                },
                headers={"Host": JOURNEY_HOST},
            )
        assert len(db.session.scalars(db.select(PushSubscription)).all()) == 3

    def test_turning_them_off_removes_only_that_device(
        self, db, journey, linked, member
    ):
        for index in range(2):
            member.post(
                "/me/you/push/",
                json={
                    "endpoint": f"https://push.example.com/{index}",
                    "keys": {"p256dh": "p" * 40, "auth": "a" * 20},
                },
                headers={"Host": JOURNEY_HOST},
            )
        member.post(
            "/me/you/push/off/",
            json={"endpoint": "https://push.example.com/0"},
            headers={"Host": JOURNEY_HOST},
        )
        remaining = db.session.scalars(db.select(PushSubscription)).all()
        assert len(remaining) == 1
        assert remaining[0].endpoint.endswith("/1")

    def test_nobody_can_unsubscribe_somebody_elses_device(
        self, db, journey, linked, staff):
        """Deliberately does not request the `member` client.

        The `db` fixture holds one application context open and Flask-Login
        caches the signed-in user on `g`, so a test using two clients finds
        the second inheriting the first one's identity. Same trap as the
        password reset session tests.
        """
        user = db.session.scalar(
            db.select(User).where(User.email == MEMBER_EMAIL, User.church_id == journey.id)
        )
        subscribe(db, journey, user, endpoint="https://push.example.com/theirs")

        staff.post(
            "/me/you/push/off/",
            json={"endpoint": "https://push.example.com/theirs"},
            headers={"Host": JOURNEY_HOST},
        )
        assert db.session.scalars(db.select(PushSubscription)).all() != []

    def test_a_signed_out_visitor_cannot_subscribe(self, client, journey):
        r = client.post(
            "/me/you/push/",
            json={"endpoint": "x", "keys": {"p256dh": "p", "auth": "a"}},
            headers={"Host": JOURNEY_HOST},
        )
        assert r.status_code == 302


class TestSendingAPush:
    def _user(self, db, journey):
        return db.session.scalar(
            db.select(User).where(User.email == MEMBER_EMAIL, User.church_id == journey.id)
        )

    def test_it_reaches_every_device(self, db, journey, linked):
        user = self._user(db, journey)
        for index in range(3):
            subscribe(db, journey, user, endpoint=f"https://push.example.com/{index}")

        transport = MemoryPushTransport()
        counts = notify(linked, "On the plan", "Sunday at 9:30", "group",
                        transport=transport)
        db.session.commit()
        assert counts["sent"] == 3
        assert len(transport.sent) == 3

    def test_an_opted_out_category_is_suppressed(self, db, journey, linked):
        """The same check email makes. Somebody who turned off next steps
        turned off next steps, not email specifically."""
        user = self._user(db, journey)
        subscribe(db, journey, user)
        linked.set_preference("next_step", False)
        db.session.commit()

        transport = MemoryPushTransport()
        counts = notify(linked, "A next step", "Have a look", "next_step",
                        transport=transport)
        assert counts["suppressed"] == 1
        assert transport.sent == []

    def test_a_transactional_category_still_sends(self, db, journey, linked):
        from app.mail import opt_out

        user = self._user(db, journey)
        subscribe(db, journey, user)
        opt_out(linked)
        db.session.commit()

        transport = MemoryPushTransport()
        counts = notify(linked, "Pickup code", "Tap to open", "kids_checkin",
                        transport=transport)
        assert counts["sent"] == 1

    def test_a_revoked_endpoint_is_deleted_not_retried(self, db, journey, linked):
        """A push service answers 410 forever. Retrying accumulates garbage
        until the worker spends its time talking to nothing."""
        user = self._user(db, journey)
        subscribe(db, journey, user)

        transport = MemoryPushTransport()
        transport.fail_with = SubscriptionGone("Endpoint gone (410).")
        counts = notify(linked, "Hello", "There", "group", transport=transport)
        db.session.commit()

        assert counts["gone"] == 1
        assert db.session.scalars(db.select(PushSubscription)).all() == []

    def test_a_temporary_failure_keeps_the_subscription(self, db, journey, linked):
        user = self._user(db, journey)
        subscription = subscribe(db, journey, user)

        transport = MemoryPushTransport()
        transport.fail_with = PushFailed("Push refused (503).")
        counts = notify(linked, "Hello", "There", "group", transport=transport)
        db.session.commit()

        assert counts["failed"] == 1
        db.session.refresh(subscription)
        assert subscription.failure_count == 1
        assert db.session.scalars(db.select(PushSubscription)).all() != []

    def test_a_person_with_no_devices_is_not_an_error(self, db, journey, linked):
        counts = notify(linked, "Hello", "There", "group",
                        transport=MemoryPushTransport())
        assert counts == {"sent": 0, "failed": 0, "gone": 0, "suppressed": 0}

    def test_a_dead_subscription_is_purged(self, db, journey, linked):
        from app.models.push import MAX_FAILURES

        user = self._user(db, journey)
        subscription = subscribe(db, journey, user)
        subscription.failure_count = MAX_FAILURES
        db.session.commit()

        assert PushSubscription.purge_dead() == 1
        db.session.commit()
        assert db.session.scalars(db.select(PushSubscription)).all() == []

    def test_a_healthy_subscription_survives_a_purge(self, db, journey, linked):
        user = self._user(db, journey)
        subscription = subscribe(db, journey, user)
        subscription.record_success()
        db.session.commit()

        assert PushSubscription.purge_dead() == 0


class TestPayloadsSayLittle:
    """A lock screen is readable by whoever is standing nearby."""

    def test_the_payload_is_truncated(self):
        message = PushMessage(title="t" * 200, body="b" * 400)
        import json

        payload = json.loads(message.to_json())
        assert len(payload["title"]) <= MAX_TITLE
        assert len(payload["body"]) <= MAX_BODY

    def test_it_carries_a_tag_so_reminders_replace_rather_than_stack(self):
        import json

        assert json.loads(PushMessage("a", "b").to_json())["tag"]

    def test_the_repr_of_a_subscription_holds_no_keys(self, db, journey, linked):
        """A repr with an endpoint or keys in it ends up in a log."""
        user = db.session.scalar(
            db.select(User).where(User.email == MEMBER_EMAIL, User.church_id == journey.id)
        )
        subscription = subscribe(db, journey, user)
        text = repr(subscription)
        assert "push.example.com" not in text
        assert "p" * 40 not in text


class TestConfiguration:
    def test_production_without_a_vapid_key_refuses_to_boot(self):
        from app.config import ProductionConfig

        class FakeApp:
            config = {
                "SQLALCHEMY_DATABASE_URI": "postgresql+psycopg2://u@h/d",
                "SECRET_KEY": "real-secret",
                "MAIL_TRANSPORT": "console",
                "PUSH_TRANSPORT": "webpush",
                "VAPID_PRIVATE_KEY": "",
            }

        with pytest.raises(RuntimeError, match="VAPID_PRIVATE_KEY"):
            ProductionConfig.init_app(FakeApp())

    def test_the_transport_refuses_an_empty_key(self):
        with pytest.raises(ValueError):
            WebPushTransport("", "mailto:x@example.com")

    def test_development_defaults_to_printing(self):
        assert build_push_transport({"PUSH_TRANSPORT": "null"}).name == "null"


class TestServiceWorkerHandlesPush:
    def test_it_shows_a_notification(self, client, journey):
        body = client.get("/sw.js", headers={"Host": JOURNEY_HOST}).get_data(as_text=True)
        assert "addEventListener('push'" in body
        assert "showNotification" in body

    def test_clicking_focuses_an_open_tab_rather_than_piling_up_new_ones(
        self, client, journey
    ):
        body = client.get("/sw.js", headers={"Host": JOURNEY_HOST}).get_data(as_text=True)
        assert "notificationclick" in body
        assert "matchAll" in body

    def test_the_cache_version_moved_so_devices_pick_it_up(self, client, journey):
        from app.blueprints.pwa import CACHE_VERSION

        assert CACHE_VERSION != "dos-v1"
        body = client.get("/sw.js", headers={"Host": JOURNEY_HOST}).get_data(as_text=True)
        assert CACHE_VERSION in body
