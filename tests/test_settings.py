"""Increment 15: settings, support, and the audit surface.

The audit tests carry the weight. A log that can be edited is a story, and a
log containing a secret is worse than no log, because it takes the secret out
of the one column that protects it and puts it somewhere designed to be read
and exported.
"""

import pytest

from app.audit import REDACTED, record, scrub
from app.content import DOS_PRICE_CENTS, INCLUDED_NOT_SAVED, REPLACES
from app.models import AuditEvent, Church
from app.models.audit import (
    ACTIONS,
    BRAND_CHANGED,
    CHILD_CHECKED_OUT,
    CREDENTIAL_CHANGED,
    GIFT_MATCHED,
    MESSAGE_DELETED,
    PASSWORD_RESET,
    RETENTION_DAYS,
    SIGN_IN,
    SIGN_IN_FAILED,
)
from app.models.base import utcnow
from tests.conftest import JOURNEY_HOST, PASSWORD

STAFF = "pastor@journeychurchsemo.com"


@pytest.fixture
def journey(db):
    church = db.session.scalar(db.select(Church).where(Church.slug == "journey"))
    church.timezone = "America/Chicago"
    db.session.commit()
    return church


class TestCostComparison:
    def test_the_numbers_are_data_not_markup(self):
        """One edit, not a hunt through a template."""
        assert sum(item["cents"] for item in REPLACES) == 29400
        assert DOS_PRICE_CENTS == 10000

    def test_the_saving_is_what_the_spec_says(self):
        saving = sum(item["cents"] for item in REPLACES) - DOS_PRICE_CENTS
        assert saving == 19400

    def test_the_bible_is_not_counted_as_a_saving(self):
        """Most churches already use a free app. Claiming it is the kind of
        overstatement a pastor checks and remembers."""
        names = " ".join(item["name"].lower() for item in REPLACES)
        assert "bible" not in names
        assert any("bible" in item["name"].lower() for item in INCLUDED_NOT_SAVED)

    def test_giving_fees_are_shown_as_unchanged(self):
        """The pitch is 'keep Tithely and your rates', so claiming a saving
        here would contradict the giving screen."""
        fees = next(i for i in REPLACES if "giving" in i["name"].lower())
        assert fees["cents"] == 0

    def test_the_screen_renders_the_arithmetic(self, staff):
        r = staff.get("/settings/", headers={"Host": JOURNEY_HOST})
        assert b"$294 / mo" in r.data
        assert b"$100 / mo" in r.data
        assert b"$194 a month" in r.data
        assert b"$2,328 a year" in r.data or b"$2328 a year" in r.data


class TestBrandSettings:
    def test_staff_can_change_the_name_and_it_shows_immediately(
        self, db, journey, staff
    ):
        staff.post(
            "/settings/brand/",
            data={"name": "Journey Church", "timezone": "America/Chicago"},
            headers={"Host": JOURNEY_HOST},
        )
        db.session.refresh(journey)
        assert journey.name == "Journey Church"

    def test_a_new_accent_reaches_every_screen(self, db, journey, staff):
        staff.post(
            "/settings/brand/",
            data={"accent_hex": "#2F3E24", "timezone": "America/Chicago"},
            headers={"Host": JOURNEY_HOST},
        )
        r = staff.get("/", headers={"Host": JOURNEY_HOST})
        assert b"--accent:#2F3E24;" in r.data

    def test_an_unreadable_accent_is_refused_in_the_form(self, db, journey, staff):
        """The guard written in increment 0, finally wired to a form.

        A pastor pasting Journey gold is stopped here rather than discovered by
        a volunteer squinting at a button in a lobby.
        """
        before = journey.accent_hex
        r = staff.post(
            "/settings/brand/",
            data={"accent_hex": "#F6C14B", "timezone": "America/Chicago"},
            headers={"Host": JOURNEY_HOST},
            follow_redirects=True,
        )
        assert b"white text" in r.data
        db.session.refresh(journey)
        assert journey.accent_hex == before

    def test_a_bad_timezone_is_refused(self, db, journey, staff):
        r = staff.post(
            "/settings/brand/",
            data={"timezone": "Middle/Earth"},
            headers={"Host": JOURNEY_HOST},
            follow_redirects=True,
        )
        assert b"not a timezone" in r.data
        db.session.refresh(journey)
        assert journey.timezone == "America/Chicago"

    def test_a_leader_cannot_change_branding(self, leader):
        assert leader.get("/settings/", headers={"Host": JOURNEY_HOST}).status_code == 403
        r = leader.post(
            "/settings/brand/", data={"name": "Nope"}, headers={"Host": JOURNEY_HOST}
        )
        assert r.status_code == 403

    def test_a_member_cannot_reach_settings(self, member):
        assert member.get("/settings/", headers={"Host": JOURNEY_HOST}).status_code == 403


class TestAuditIsAppendOnly:
    def test_there_is_no_route_that_edits_or_deletes_an_entry(self, app):
        """A log a leader can edit is a story, not a log."""
        for rule in app.url_map.iter_rules():
            path = str(rule)
            if "audit" in path:
                assert "delete" not in path and "edit" not in path

    def test_the_model_has_no_soft_delete_or_edited_flag(self):
        columns = {c.name for c in AuditEvent.__table__.columns}
        for forbidden in ("is_deleted", "deleted_at", "edited", "redacted_by"):
            assert forbidden not in columns

    def test_the_actor_name_is_copied_so_it_survives_the_account(
        self, db, journey, staff
    ):
        """"Someone changed the giving keys" is not an audit trail."""
        from app.models import User

        staff.post(
            "/settings/brand/",
            data={"name": "Journey", "timezone": "America/Chicago"},
            headers={"Host": JOURNEY_HOST},
        )
        event = db.session.scalars(AuditEvent.recent(journey.id)).first()
        assert event.actor_name

        user = db.session.get(User, event.actor_user_id)
        db.session.delete(user)
        db.session.commit()
        db.session.refresh(event)
        assert event.actor_name


class TestAuditNeverHoldsASecret:
    @pytest.mark.parametrize(
        "text",
        [
            "key is sk_live_abcdefghijklmnop",
            "password: hunter2isnotgoodenough",
            "token=abc123def456ghi789jkl012mno345pq",
            "the api_key = sk_test_9f8e7d6c5b4a3210",
            "pin: 7039",
        ],
    )
    def test_secrets_are_scrubbed(self, text):
        assert REDACTED in scrub(text)

    def test_a_long_opaque_string_is_scrubbed(self):
        assert REDACTED in scrub("value " + "a" * 40)

    def test_ordinary_text_survives(self):
        assert scrub("Marcus Webb collected Ellie Webb") == (
            "Marcus Webb collected Ellie Webb"
        )

    def test_saving_provider_keys_never_records_the_key(self, db, journey, staff):
        staff.post(
            "/giving/keys/",
            data={"public_key": "pub_abc", "private_key": "sk_live_verysecretvalue123"},
            headers={"Host": JOURNEY_HOST},
        )
        event = db.session.scalars(
            AuditEvent.recent(journey.id, action=CREDENTIAL_CHANGED)
        ).first()
        assert event is not None
        blob = f"{event.summary} {event.detail} {event.subject_label}"
        assert "sk_live_verysecretvalue123" not in blob

    def test_a_check_out_never_records_the_pickup_code(self, db, journey, staff):
        from app.models import Checkin, CheckinSession, Household, Person

        household = Household(church_id=journey.id, name="The Webbs")
        db.session.add(household)
        db.session.flush()
        child = Person(
            church_id=journey.id, first_name="Ellie", last_name="Webb",
            stage="member", household_id=household.id, is_child=True,
        )
        session = CheckinSession(
            church_id=journey.id, name="Sunday", starts_at=utcnow()
        )
        db.session.add_all([child, session])
        db.session.flush()

        code = session.issue_pickup_code(household.id)
        db.session.add(
            Checkin(
                church_id=journey.id, session_id=session.id, person_id=child.id,
                household_id=household.id, pickup_code=code,
            )
        )
        db.session.commit()

        checkin = session.checkins[0]
        staff.post(
            "/kids/checkout/",
            data={"code": code, "checkin_id": [checkin.id], "collected_by": "Dana Webb"},
            headers={"Host": JOURNEY_HOST},
        )
        event = db.session.scalars(
            AuditEvent.recent(journey.id, action=CHILD_CHECKED_OUT)
        ).first()
        assert event is not None
        assert "Ellie Webb" in event.summary
        assert "Dana Webb" in event.summary
        assert code not in f"{event.summary} {event.detail} {event.subject_label}"

    def test_a_failed_sign_in_does_not_record_the_address(self, db, journey, client):
        """A log of attempted addresses is a list of who somebody thinks
        attends this church."""
        client.post(
            "/auth/login",
            data={"email": "guessing@nowhere-at-all.com", "password": "wrong"},
            headers={"Host": JOURNEY_HOST},
        )
        event = db.session.scalars(
            AuditEvent.recent(journey.id, action=SIGN_IN_FAILED)
        ).first()
        assert event is not None
        assert "guessing@nowhere-at-all.com" not in f"{event.summary} {event.detail}"


class TestWhatGetsRecorded:
    def test_a_sign_in(self, db, journey, sign_in):
        sign_in(STAFF)
        event = db.session.scalars(AuditEvent.recent(journey.id, action=SIGN_IN)).first()
        assert event is not None
        assert "signed in" in event.summary

    def test_a_password_reset(self, db, journey, client):
        import re

        from app.models import OutboxMessage

        client.post("/auth/forgot", data={"email": STAFF}, headers={"Host": JOURNEY_HOST})
        message = db.session.scalars(
            db.select(OutboxMessage).order_by(OutboxMessage.id.desc())
        ).first()
        link = re.search(r"(/auth/reset/[A-Za-z0-9_\-]+)", message.body_text).group(1)

        client.post(
            link,
            data={"password": "a-brand-new-passphrase", "confirm": "a-brand-new-passphrase"},
            headers={"Host": JOURNEY_HOST},
        )
        event = db.session.scalars(
            AuditEvent.recent(journey.id, action=PASSWORD_RESET)
        ).first()
        assert event is not None

    def test_a_deleted_message(self, db, journey, staff):
        from app.models import Conversation, Message, Person

        author = Person(
            church_id=journey.id, first_name="Marcus", last_name="Webb", stage="member"
        )
        conversation = Conversation(
            church_id=journey.id, kind="room", title="Worship team"
        )
        db.session.add_all([author, conversation])
        db.session.flush()
        message = Message.post(conversation, author, "Something regrettable")
        db.session.commit()

        staff.post(
            f"/messages/{conversation.id}/messages/{message.id}/delete/",
            headers={"Host": JOURNEY_HOST},
        )
        event = db.session.scalars(
            AuditEvent.recent(journey.id, action=MESSAGE_DELETED)
        ).first()
        assert event is not None
        assert "Marcus Webb" in event.summary
        # The words themselves are gone, and the log does not resurrect them.
        assert "regrettable" not in f"{event.summary} {event.detail}"

    def test_a_matched_gift(self, db, journey, staff):
        from datetime import date

        from app.models import ExternalGift, Person
        from app.models.giving_mirror import PROVIDER_TITHELY

        person = Person(
            church_id=journey.id, first_name="Chris", last_name="Vaughn", stage="member"
        )
        gift = ExternalGift(
            church_id=journey.id, provider=PROVIDER_TITHELY, provider_txn_id="t1",
            amount_cents=4000, received_on=date(2026, 8, 2),
        )
        db.session.add_all([person, gift])
        db.session.commit()

        staff.post(
            f"/giving/unmatched/{gift.id}/attach/",
            data={"person_id": person.id},
            headers={"Host": JOURNEY_HOST},
        )
        event = db.session.scalars(
            AuditEvent.recent(journey.id, action=GIFT_MATCHED)
        ).first()
        assert event is not None
        assert "$40.00" in event.summary
        assert "Chris Vaughn" in event.summary

    def test_a_branding_change(self, db, journey, staff):
        staff.post(
            "/settings/brand/",
            data={"name": "Journey", "timezone": "America/Chicago"},
            headers={"Host": JOURNEY_HOST},
        )
        event = db.session.scalars(
            AuditEvent.recent(journey.id, action=BRAND_CHANGED)
        ).first()
        assert event is not None

    def test_nothing_is_recorded_when_nothing_changed(self, db, journey, staff):
        """A log of every save is a log nobody reads."""
        staff.post(
            "/settings/brand/",
            data={
                "name": journey.name,
                "timezone": journey.timezone,
                "app_name": journey.app_name or "",
                "app_domain": journey.app_domain or "",
                "accent_hex": journey.accent_hex or "",
            },
            headers={"Host": JOURNEY_HOST},
        )
        assert db.session.scalars(
            AuditEvent.recent(journey.id, action=BRAND_CHANGED)
        ).all() == []

    def test_every_declared_action_has_a_label(self):
        from app.models.audit import ACTION_LABELS

        assert set(ACTIONS) <= set(ACTION_LABELS)


class TestAuditScoping:
    def test_entries_never_cross_churches(self, db, journey, staff):
        riverbend = db.session.scalar(db.select(Church).where(Church.slug == "riverbend"))
        record(BRAND_CHANGED, "Theirs", church_id=riverbend.id)
        db.session.commit()

        r = staff.get("/settings/", headers={"Host": JOURNEY_HOST})
        assert b"Theirs" not in r.data

    def test_recording_without_a_church_is_a_no_op_not_a_crash(self, app):
        """An audit failure must never be the reason a sign-in fails."""
        with app.app_context():
            assert record(SIGN_IN, "orphan") is None

    def test_filtering_by_action(self, db, journey, staff):
        record(BRAND_CHANGED, "Brand thing", church_id=journey.id)
        record(GIFT_MATCHED, "Gift thing", church_id=journey.id)
        db.session.commit()

        r = staff.get(
            f"/settings/?action={GIFT_MATCHED}", headers={"Host": JOURNEY_HOST}
        )
        assert b"Gift thing" in r.data
        assert b"Brand thing" not in r.data

    def test_an_unknown_filter_falls_back_to_everything(self, db, journey, staff):
        record(BRAND_CHANGED, "Brand thing", church_id=journey.id)
        db.session.commit()
        r = staff.get("/settings/?action=nonsense", headers={"Host": JOURNEY_HOST})
        assert b"Brand thing" in r.data


class TestRetention:
    def test_old_entries_are_purged(self, app, db, journey):
        from datetime import timedelta

        event = record(BRAND_CHANGED, "Ancient", church_id=journey.id)
        db.session.commit()
        event.occurred_at = utcnow() - timedelta(days=RETENTION_DAYS + 10)
        db.session.commit()

        result = app.test_cli_runner().invoke(args=["purge-audit"])
        assert result.exit_code == 0
        db.session.expire_all()
        assert db.session.scalars(AuditEvent.recent(journey.id)).all() == []

    def test_recent_entries_survive(self, app, db, journey):
        record(BRAND_CHANGED, "Recent", church_id=journey.id)
        db.session.commit()

        app.test_cli_runner().invoke(args=["purge-audit"])
        db.session.expire_all()
        assert db.session.scalars(AuditEvent.recent(journey.id)).all() != []


class TestAPartialFormDoesNotWipeData:
    """A field the browser did not send is not a field somebody cleared."""

    def test_omitting_a_field_leaves_it_alone(self, db, journey, staff):
        journey.app_name = "The Journey Church"
        journey.app_domain = "app.thejourneychurchsemo.com"
        db.session.commit()

        staff.post(
            "/settings/brand/",
            data={"name": "Journey Church", "timezone": "America/Chicago"},
            headers={"Host": JOURNEY_HOST},
        )
        db.session.refresh(journey)
        assert journey.app_name == "The Journey Church"
        assert journey.app_domain == "app.thejourneychurchsemo.com"

    def test_sending_an_empty_field_does_clear_it(self, db, journey, staff):
        journey.app_name = "The Journey Church"
        db.session.commit()

        staff.post(
            "/settings/brand/",
            data={"name": journey.name, "timezone": "America/Chicago", "app_name": ""},
            headers={"Host": JOURNEY_HOST},
        )
        db.session.refresh(journey)
        assert journey.app_name is None


class TestPointingADomainAtTheChurch:
    """`custom_domain` routes; `app_domain` is a display field.

    Settings exposed only the second one, labelled "Domain", so a pastor
    pointing a real domain at the app would type it into the field that does
    nothing and get a "no church is set up here" page with no way to tell why.
    """

    def _save(self, staff, journey, **extra):
        data = {"name": journey.name, "timezone": journey.timezone}
        data.update(extra)
        return staff.post(
            "/settings/brand/", data=data,
            headers={"Host": JOURNEY_HOST}, follow_redirects=True,
        )

    def test_setting_it_makes_that_address_resolve(self, db, journey, staff):
        self._save(staff, journey, custom_domain="app.thejourneychurchsemo.com")
        db.session.refresh(journey)
        assert journey.custom_domain == "app.thejourneychurchsemo.com"
        assert Church.by_custom_domain("app.thejourneychurchsemo.com").id == journey.id

    def test_the_app_answers_on_it(self, db, journey, staff, client):
        """Checked on a public page rather than the login screen.

        The `db` fixture holds one application context open and Flask-Login
        caches the signed-in user on `g`, so the second client inherits the
        first one's session and the login route redirects instead of
        rendering. Sixth time that has bitten.
        """
        self._save(staff, journey, custom_domain="app.thejourneychurchsemo.com")
        r = client.get("/privacy/", headers={"Host": "app.thejourneychurchsemo.com"})
        assert r.status_code == 200
        assert journey.name.encode() in r.data

    def test_an_unmapped_host_still_says_no_church_is_here(self, app, db, client):
        # The testing config allows a ?tenant= override and falls back to the
        # single church, which is what makes local work convenient. Turn both
        # off to see what production does with a host nobody mapped.
        app.config["ALLOW_TENANT_QUERY_OVERRIDE"] = False
        app.config["PLATFORM_DOMAIN"] = ""
        r = client.get("/privacy/", headers={"Host": "nobody.example.org"})
        assert r.status_code == 404

    @pytest.mark.parametrize(
        "typed,stored",
        [
            ("https://app.journey.org/", "app.journey.org"),
            ("http://app.journey.org", "app.journey.org"),
            ("APP.Journey.Org", "app.journey.org"),
            ("  app.journey.org  ", "app.journey.org"),
            ("app.journey.org/", "app.journey.org"),
        ],
    )
    def test_it_accepts_what_people_actually_paste(
        self, db, journey, staff, typed, stored
    ):
        """People paste what is in their address bar. Refusing that would be
        correct and useless; it is a hostname either way."""
        self._save(staff, journey, custom_domain=typed)
        db.session.refresh(journey)
        assert journey.custom_domain == stored

    def test_nonsense_is_refused(self, db, journey, staff):
        before = journey.custom_domain
        r = self._save(staff, journey, custom_domain="not a host")
        assert b"is not a hostname" in r.data
        db.session.refresh(journey)
        assert journey.custom_domain == before

    def test_a_bare_word_is_refused(self, db, journey, staff):
        r = self._save(staff, journey, custom_domain="journey")
        assert b"is not a hostname" in r.data

    def test_one_address_one_church(self, db, journey, staff):
        riverbend = db.session.scalar(
            db.select(Church).where(Church.slug == "riverbend")
        )
        riverbend.custom_domain = "shared.example.org"
        db.session.commit()

        r = self._save(staff, journey, custom_domain="shared.example.org")
        assert b"already points at another church" in r.data
        db.session.refresh(journey)
        assert journey.custom_domain != "shared.example.org"

    def test_it_can_be_cleared(self, db, journey, staff):
        self._save(staff, journey, custom_domain="app.journey.org")
        self._save(staff, journey, custom_domain="")
        db.session.refresh(journey)
        assert journey.custom_domain is None

    def test_the_change_is_audited(self, db, journey, staff):
        from app.models.audit import BRAND_CHANGED

        self._save(staff, journey, custom_domain="app.journey.org")
        event = db.session.scalars(
            AuditEvent.recent(journey.id, action=BRAND_CHANGED)
        ).first()
        assert "web address" in event.detail

    def test_the_two_domain_fields_are_labelled_differently(self, staff):
        """One routes and one does not. A pastor cannot be expected to guess
        which "Domain" means what."""
        r = staff.get("/settings/", headers={"Host": JOURNEY_HOST})
        body = r.get_data(as_text=True)
        assert 'name="custom_domain"' in body
        assert "Web address your people use" in body
        assert body.count(">Domain<") == 0

    def test_a_leader_cannot_repoint_the_church(self, leader):
        r = leader.post(
            "/settings/brand/",
            data={"custom_domain": "evil.example.org"},
            headers={"Host": JOURNEY_HOST},
        )
        assert r.status_code == 403
