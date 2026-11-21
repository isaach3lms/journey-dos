"""Running a provider and Web Push at the same time.

**The failure this exists to prevent.** A church can be running two apps built
on two different things at once: an App Store app wrapped around a provider's
SDK, and an Android wrapper around the live site that has no SDK in it at all.
Those reach different phones and neither reaches both. The provider addresses
a person by id and knows nothing about a browser; Web Push addresses a device
row and the native app never creates one.

Until this existed, `PUSH_TRANSPORT` picked one and silently dropped the other
half of the congregation. Nothing on any screen said so. Every staff member
testing on their own phone saw notifications working, and the half who could
not receive them had no symptom to report beyond "I don't get those".

So the tests below are mostly about the composite refusing to be quiet. It
holds parts instead of sending, which means a caller that has not been taught
about parts raises rather than delivering to one platform and dropping the
other.
"""

import pytest

from app.models import Church, Person, PushSubscription, User
from app.models.base import utcnow
from app.push import queue as push_queue
from app.push.health import transport_summary
from app.push.send import parts_of, send_to_person
from app.push.transport import (
    BothPushTransport,
    MemoryPushTransport,
    PushMessage,
    build_push_transport,
)
from tests.conftest import JOURNEY_HOST

BOTH = {
    "PUSH_TRANSPORT": "both",
    "ONESIGNAL_APP_ID": "app-id",
    "ONESIGNAL_API_KEY": "secret",
    "VAPID_PUBLIC_KEY": "pub",
    "VAPID_PRIVATE_KEY": "priv",
}


@pytest.fixture
def journey(db):
    return db.session.scalar(db.select(Church).where(Church.slug == "journey"))


@pytest.fixture
def linked(db, journey):
    """A member with an account, which is what a provider addresses."""
    user = db.session.scalar(db.select(User).where(
        User.email == "member@journeychurchsemo.com"))
    person = Person(church_id=journey.id, first_name="Alicia",
                    last_name="Romero", email=user.email, stage="member",
                    approved_at=utcnow())
    db.session.add(person)
    db.session.flush()
    user.person_id = person.id
    user.ensure_push_external_id()
    db.session.commit()
    return user


def both_fakes():
    """The composite, built from two fakes that record what they were given.

    Two separate recorders rather than one shared, because the whole question
    these tests ask is whether each pipe was used, and a single list could not
    tell "both were called" from "one was called twice".
    """
    provider = MemoryPushTransport(name="fake-provider", addresses_people=True)
    devices = MemoryPushTransport(name="fake-devices", addresses_people=False)
    return BothPushTransport(provider, devices), provider, devices


class TestItRefusesToSendOnItsOwn:
    """The composite holds parts. It must never look like a transport."""

    def test_calling_send_on_it_raises_rather_than_dropping_a_platform(self):
        """The important one. If this returned quietly, a caller that had not
        been taught about parts would deliver to nobody and report success,
        which is the exact silent half-delivery this class exists to stop."""
        both, _, _ = both_fakes()

        with pytest.raises(NotImplementedError):
            both.send(object(), PushMessage(title="t", body="b"))

    def test_addressing_a_person_on_it_raises_too(self):
        both, _, _ = both_fakes()

        with pytest.raises(NotImplementedError):
            both.send_to_person("ext-1", PushMessage(title="t", body="b"))

    def test_it_does_not_claim_to_address_people(self):
        """`addresses_people` is the old either/or flag. Answering True would
        send every caller down the provider branch alone."""
        both, _, _ = both_fakes()

        assert both.addresses_people is False

    def test_one_transport_alone_is_refused(self):
        """Wrapping a single transport would hide which one is running behind
        a name that promises two."""
        with pytest.raises(ValueError):
            BothPushTransport(MemoryPushTransport())


class TestWhatASendWalks:
    def test_an_ordinary_transport_is_its_own_only_part(self):
        single = MemoryPushTransport()

        assert parts_of(single) == (single,)

    def test_the_composite_hands_back_both(self):
        both, provider, devices = both_fakes()

        assert parts_of(both) == (provider, devices)

    def test_the_provider_goes_first(self):
        """So a provider outage lands before the row deletions and failure
        counts the device fan-out writes, rather than after them."""
        both, provider, devices = both_fakes()

        assert parts_of(both)[0] is provider


class TestOneSendReachesBoth:
    def test_a_person_with_an_account_and_a_device_gets_both(
            self, db, journey, linked):
        """The case the whole change is for: an iPhone on the native app and
        an Android wrapper on the same roster record."""
        PushSubscription.register(
            journey.id, linked, endpoint="https://push.example/abc",
            p256dh="p", auth="a", label="Pixel",
        )
        db.session.commit()
        both, provider, devices = both_fakes()

        counts = send_to_person(
            linked.person, PushMessage(title="Hi", body="There"),
            "announcement", transport=both,
        )

        assert [sent[0] for sent in provider.sent] == [linked.push_external_id]
        assert [sent[0] for sent in devices.sent] == ["https://push.example/abc"]
        assert counts["sent"] == 2

    def test_a_person_with_only_an_account_still_gets_the_provider(
            self, db, journey, linked):
        """Somebody who has the native app and has never opened a browser.
        The device fan-out finds nothing, which is not a failure."""
        both, provider, devices = both_fakes()

        counts = send_to_person(
            linked.person, PushMessage(title="Hi", body="There"),
            "announcement", transport=both,
        )

        assert len(provider.sent) == 1
        assert devices.sent == []
        assert counts["sent"] == 1

    def test_counts_from_both_pipes_are_added_up(self, db, journey, linked):
        """One number for one send. Two phones is two deliveries however many
        different systems carried them."""
        for n in range(2):
            PushSubscription.register(
                journey.id, linked, endpoint=f"https://push.example/{n}",
                p256dh="p", auth="a", label=f"Device {n}",
            )
        db.session.commit()
        both, _, _ = both_fakes()

        counts = send_to_person(
            linked.person, PushMessage(title="Hi", body="There"),
            "announcement", transport=both,
        )

        assert counts["sent"] == 3

    def test_an_opt_out_still_stops_everything(self, db, journey, linked):
        """Checked once, before any part runs. A person who turned a category
        off turned it off, not "off on iPhone"."""
        PushSubscription.register(
            journey.id, linked, endpoint="https://push.example/abc",
            p256dh="p", auth="a", label="Pixel",
        )
        linked.person.set_preference("announcement", False)
        db.session.commit()
        both, provider, devices = both_fakes()

        counts = send_to_person(
            linked.person, PushMessage(title="Hi", body="There"),
            "announcement", transport=both,
        )

        assert provider.sent == []
        assert devices.sent == []
        assert counts["suppressed"] == 1


class TestWhoCountsAsReachable:
    def test_an_account_with_no_device_row_is_still_reachable(
            self, app, db, journey, linked):
        """The regression that would drop every iPhone. Requiring a
        subscription row under a composite would queue nothing for the people
        the provider was about to reach."""
        app.config.update(BOTH)

        assert push_queue.reachable(linked.person) is True

    def test_a_device_row_with_no_provider_identity_is_reachable(
            self, app, db, journey, linked):
        """And the mirror: the Android wrapper, which has a row and no
        identity with the provider."""
        linked.push_external_id = None
        PushSubscription.register(
            journey.id, linked, endpoint="https://push.example/abc",
            p256dh="p", auth="a", label="Pixel",
        )
        db.session.commit()
        app.config.update(BOTH)

        assert push_queue.reachable(linked.person) is True

    def test_neither_means_unreachable(self, app, db, journey, linked):
        """Still a real answer. The queue holds work rather than rows that
        exist to be thrown away."""
        linked.push_external_id = None
        db.session.commit()
        app.config.update(BOTH)

        assert push_queue.reachable(linked.person) is False


class TestBuildingItFromConfig:
    def test_both_builds_the_two_real_transports(self):
        built = build_push_transport(BOTH)

        assert built.name == "both"
        assert [part.name for part in built.parts] == ["onesignal", "webpush"]

    def test_a_missing_vapid_key_refuses_at_boot(self):
        """Not at the first send. A church that meant to run both and set up
        one has a misconfiguration, not a half-working setup to discover one
        phone at a time."""
        with pytest.raises(ValueError):
            build_push_transport({**BOTH, "VAPID_PRIVATE_KEY": ""})

    def test_a_missing_provider_key_refuses_at_boot(self):
        with pytest.raises(ValueError):
            build_push_transport({**BOTH, "ONESIGNAL_API_KEY": ""})

    @pytest.mark.parametrize("missing", [
        "VAPID_PRIVATE_KEY", "ONESIGNAL_APP_ID", "ONESIGNAL_API_KEY",
    ])
    def test_production_refuses_to_start_half_configured(self, missing):
        """The check that runs before a single request is served.

        Found by a mutation: the transport factory refused a half-configured
        'both', but nothing covered the production boot check, so loosening it
        there broke nothing any test could see. A deploy that comes up with
        one pipe missing does not fail loudly. It serves perfectly for
        everybody on the platform that still works, and the other half simply
        stop getting notifications with nothing anywhere saying why.
        """
        from flask import Flask

        from app.config import ProductionConfig

        app = Flask(__name__)
        app.config.update(
            SQLALCHEMY_DATABASE_URI="postgresql+psycopg2://x/y",
            SECRET_KEY="a-real-one",
            MAIL_TRANSPORT="null",
            **{**BOTH, missing: ""},
        )

        with pytest.raises(RuntimeError):
            ProductionConfig.init_app(app)

    def test_production_starts_when_every_credential_is_there(self):
        """The other half of the pair, so the test above is failing for the
        missing credential rather than for the scaffolding around it."""
        from flask import Flask

        from app.config import ProductionConfig

        app = Flask(__name__)
        app.config.update(
            SQLALCHEMY_DATABASE_URI="postgresql+psycopg2://x/y",
            SECRET_KEY="a-real-one",
            MAIL_TRANSPORT="null",
            **BOTH,
        )

        ProductionConfig.init_app(app)

    def test_the_single_transports_are_untouched(self):
        """Churches on one transport must not be changed by this at all."""
        single = build_push_transport({
            "PUSH_TRANSPORT": "webpush", "VAPID_PRIVATE_KEY": "priv"})

        assert single.name == "webpush"
        assert not hasattr(single, "parts")


class TestWhatTheSettingsPageShows:
    def test_both_reads_as_real_when_every_credential_is_set(self):
        summary = transport_summary(BOTH)

        assert summary["real"] is True
        assert summary["combined"] is True

    def test_half_configured_is_not_real(self):
        summary = transport_summary({**BOTH, "ONESIGNAL_API_KEY": ""})

        assert summary["real"] is False
        assert summary["provider_keys_present"] is False
        assert summary["webpush_keys_present"] is True

    def test_a_single_transport_is_not_flagged_combined(self):
        """The key has to exist on every summary, or a template reading it
        raises on the churches that did not change anything."""
        assert transport_summary({"PUSH_TRANSPORT": "webpush"})["combined"] is False
        assert transport_summary({"PUSH_TRANSPORT": "onesignal"})["combined"] is False
        # Spelled out rather than passed as {}, because an empty dict is
        # falsy and `transport_summary` reads that as "no config given" and
        # reaches for the running app's instead.
        assert transport_summary({"PUSH_TRANSPORT": "null"})["combined"] is False
