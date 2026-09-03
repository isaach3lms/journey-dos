"""Increment 13: the Tithely read-only sync.

Three things carry the risk, and none of them is the schema.

Matching, because attributing money to the wrong person surfaces in a giving
statement at year end. Lapse detection, because a card that failed and retried
is not a person leaving. Encryption, because the stored key can read every gift
a church has ever received.
"""

from datetime import date, timedelta

import pytest

from app.crypto import DecryptionFailed, decrypt, encrypt, mask
from app.matching import (
    CONFIDENCE_AMBIGUOUS,
    CONFIDENCE_EMAIL,
    CONFIDENCE_NAME,
    CONFIDENCE_NONE,
    CONFIDENCE_PHONE,
    match_donor,
    normalize_phone,
    split_donor_name,
)
from app.models import (
    LAPSE_GRACE_DAYS,
    MATCH_IGNORED,
    MATCH_MATCHED,
    MATCH_UNMATCHED,
    Church,
    ExternalGift,
    ExternalRecurringGift,
    IntegrationCredential,
    Person,
)
from app.models.base import utcnow
from app.models.giving_mirror import PROVIDER_TITHELY, RECURRING_CANCELLED
from tests.conftest import JOURNEY_HOST

TODAY = date(2026, 9, 2)


@pytest.fixture
def journey(db):
    church = db.session.scalar(db.select(Church).where(Church.slug == "journey"))
    church.timezone = "America/Chicago"
    db.session.commit()
    return church


@pytest.fixture
def chris(db, journey):
    person = Person(
        church_id=journey.id, first_name="Chris", last_name="Vaughn",
        email="chris.vaughn@example.com", phone="573-555-4085", stage="attender",
    )
    db.session.add(person)
    db.session.commit()
    return person


def add_gift(db, journey, txn, cents, when, person=None, email=None, name=None):
    gift = ExternalGift(
        church_id=journey.id,
        provider=PROVIDER_TITHELY,
        provider_txn_id=txn,
        donor_name=name,
        donor_email=email,
        amount_cents=cents,
        received_on=when,
        person_id=person.id if person else None,
        match_status=MATCH_MATCHED if person else MATCH_UNMATCHED,
    )
    db.session.add(gift)
    db.session.commit()
    return gift


class TestEncryption:
    def test_a_key_round_trips(self):
        assert decrypt(encrypt("sk_live_abc123", "secret"), "secret") == "sk_live_abc123"

    def test_the_ciphertext_does_not_contain_the_key(self):
        blob = encrypt("sk_live_abc123", "secret")
        assert "sk_live_abc123" not in blob

    def test_the_same_value_encrypts_differently_each_time(self):
        """Otherwise two churches with the same key are visibly identical."""
        assert encrypt("same", "secret") != encrypt("same", "secret")

    def test_a_different_secret_cannot_read_it(self):
        blob = encrypt("sk_live_abc123", "secret-one")
        with pytest.raises(DecryptionFailed, match="SECRET_KEY"):
            decrypt(blob, "secret-two")

    def test_a_tampered_value_fails_loudly(self):
        """Authenticated encryption, so it does not decrypt to garbage."""
        blob = encrypt("sk_live_abc123", "secret")
        tampered = blob[:-4] + "AAAA"
        with pytest.raises(DecryptionFailed):
            decrypt(tampered, "secret")

    def test_empty_values(self):
        assert encrypt(None, "secret") is None
        assert encrypt("", "secret") is None
        assert decrypt(None, "secret") is None

    def test_masking_shows_enough_to_recognise_and_not_to_use(self):
        assert mask("sk_live_abcdef1234") == "••••••••••••••1234"
        assert "abcdef" not in mask("sk_live_abcdef1234")


class TestCredentials:
    def test_the_private_key_is_stored_encrypted(self, db, journey):
        credential = IntegrationCredential(
            church_id=journey.id, provider=PROVIDER_TITHELY, public_key="pub"
        )
        credential.set_private_key("sk_live_secret", "the-secret-key")
        db.session.add(credential)
        db.session.commit()

        assert "sk_live_secret" not in (credential.private_key_encrypted or "")
        assert credential.private_key("the-secret-key") == "sk_live_secret"

    def test_the_repr_never_leaks_key_material(self, db, journey):
        """Reprs end up in logs."""
        credential = IntegrationCredential(
            church_id=journey.id, provider=PROVIDER_TITHELY, public_key="pub_abc"
        )
        credential.set_private_key("sk_live_secret", "the-secret-key")
        assert "sk_live_secret" not in repr(credential)
        assert "pub_abc" not in repr(credential)

    def test_the_masked_key_is_what_a_screen_shows(self, db, journey):
        credential = IntegrationCredential(church_id=journey.id, provider=PROVIDER_TITHELY)
        credential.set_private_key("sk_live_secret", "k")
        assert credential.masked_private_key == "••••••••"

    def test_one_credential_per_provider_per_church(self, db, journey):
        for _ in range(2):
            db.session.add(
                IntegrationCredential(church_id=journey.id, provider=PROVIDER_TITHELY)
            )
        with pytest.raises(Exception):
            db.session.commit()
        db.session.rollback()

    def test_saving_keys_through_the_screen(self, db, journey, staff):
        staff.post(
            "/giving/keys/",
            data={"public_key": "pub_1", "private_key": "sk_live_1",
                  "organization_ref": "org_1"},
            headers={"Host": JOURNEY_HOST},
        )
        credential = IntegrationCredential.for_provider(journey.id)
        assert credential.public_key == "pub_1"
        assert credential.private_key_encrypted
        assert "sk_live_1" not in credential.private_key_encrypted

    def test_an_empty_private_key_field_leaves_the_stored_one_alone(
        self, db, journey, staff
    ):
        """Otherwise editing an unrelated field silently wipes the key."""
        staff.post(
            "/giving/keys/",
            data={"public_key": "pub_1", "private_key": "sk_live_1"},
            headers={"Host": JOURNEY_HOST},
        )
        before = IntegrationCredential.for_provider(journey.id).private_key_encrypted

        staff.post(
            "/giving/keys/",
            data={"public_key": "pub_2", "private_key": ""},
            headers={"Host": JOURNEY_HOST},
        )
        credential = IntegrationCredential.for_provider(journey.id)
        assert credential.public_key == "pub_2"
        assert credential.private_key_encrypted == before

    def test_a_leader_cannot_touch_the_keys(self, leader):
        """These read every gift the church ever received. Staff only."""
        r = leader.post(
            "/giving/keys/", data={"public_key": "x"}, headers={"Host": JOURNEY_HOST}
        )
        assert r.status_code == 403


class TestMatching:
    def test_a_unique_email_matches(self, db, journey, chris):
        result = match_donor(journey.id, email="chris.vaughn@example.com")
        assert result.confidence == CONFIDENCE_EMAIL
        assert result.person.id == chris.id
        assert result.is_auto

    def test_email_matching_is_case_insensitive(self, db, journey, chris):
        result = match_donor(journey.id, email="CHRIS.VAUGHN@EXAMPLE.COM")
        assert result.person.id == chris.id

    def test_a_shared_email_never_auto_matches(self, db, journey, chris):
        """Households share addresses. Guessing which spouse gave is the error
        this whole module exists to avoid."""
        spouse = Person(
            church_id=journey.id, first_name="Alina", last_name="Vaughn",
            email="chris.vaughn@example.com", stage="attender",
        )
        db.session.add(spouse)
        db.session.commit()

        result = match_donor(journey.id, email="chris.vaughn@example.com")
        assert result.confidence == CONFIDENCE_AMBIGUOUS
        assert result.person is None
        assert not result.is_auto
        assert len(result.candidates) == 2

    def test_a_phone_match_is_suggested_but_never_applied(self, db, journey, chris):
        """A phone is a household line as often as a personal one."""
        result = match_donor(journey.id, phone="(573) 555-4085")
        assert result.confidence == CONFIDENCE_PHONE
        assert result.person.id == chris.id
        assert not result.is_auto

    def test_phone_spellings_normalize_to_one_number(self):
        for written in ["573-555-4085", "(573) 555-4085", "+1 573 555 4085", "5735554085"]:
            assert normalize_phone(written) == "5735554085"

    def test_a_name_match_is_suggested_but_never_applied(self, db, journey, chris):
        """A name is not an identifier."""
        result = match_donor(journey.id, name="Chris Vaughn")
        assert result.confidence == CONFIDENCE_NAME
        assert result.person.id == chris.id
        assert not result.is_auto

    def test_two_people_with_one_name_never_auto_match(self, db, journey, chris):
        """Two Chris Vaughns in a church is ordinary."""
        twin = Person(
            church_id=journey.id, first_name="Chris", last_name="Vaughn", stage="member"
        )
        db.session.add(twin)
        db.session.commit()

        result = match_donor(journey.id, name="Chris Vaughn")
        assert result.confidence == CONFIDENCE_AMBIGUOUS
        assert result.person is None

    def test_email_beats_name(self, db, journey, chris):
        other = Person(
            church_id=journey.id, first_name="Chris", last_name="Vaughn",
            email="different@example.com", stage="member",
        )
        db.session.add(other)
        db.session.commit()

        result = match_donor(
            journey.id, email="different@example.com", name="Chris Vaughn"
        )
        assert result.person.id == other.id
        assert result.confidence == CONFIDENCE_EMAIL

    def test_nobody_matches(self, db, journey, chris):
        result = match_donor(journey.id, email="stranger@example.com", name="A Stranger")
        assert result.confidence == CONFIDENCE_NONE
        assert result.person is None

    def test_matching_never_crosses_churches(self, db, journey, chris):
        riverbend = db.session.scalar(db.select(Church).where(Church.slug == "riverbend"))
        result = match_donor(riverbend.id, email="chris.vaughn@example.com")
        assert result.person is None

    def test_an_archived_person_does_not_match(self, db, journey, chris):
        chris.is_archived = True
        db.session.commit()
        assert match_donor(journey.id, email="chris.vaughn@example.com").person is None

    def test_name_splitting(self):
        assert split_donor_name("Chris Vaughn") == ("chris", "vaughn")
        assert split_donor_name("Mary Anne Smith") == ("mary", "smith")
        assert split_donor_name("Cher") == ("cher", "")
        assert split_donor_name(None) == ("", "")


class TestTheMirrorIsIdempotent:
    def test_the_same_transaction_cannot_be_stored_twice(self, db, journey):
        """A sync that runs twice must not double a church's totals."""
        add_gift(db, journey, "txn_1", 5000, TODAY)
        db.session.add(
            ExternalGift(
                church_id=journey.id, provider=PROVIDER_TITHELY,
                provider_txn_id="txn_1", amount_cents=5000, received_on=TODAY,
            )
        )
        with pytest.raises(Exception):
            db.session.commit()
        db.session.rollback()

    def test_two_churches_may_share_a_transaction_id(self, db, journey):
        riverbend = db.session.scalar(db.select(Church).where(Church.slug == "riverbend"))
        add_gift(db, journey, "txn_1", 5000, TODAY)
        db.session.add(
            ExternalGift(
                church_id=riverbend.id, provider=PROVIDER_TITHELY,
                provider_txn_id="txn_1", amount_cents=5000, received_on=TODAY,
            )
        )
        db.session.commit()

    def test_amounts_are_integer_cents(self, db, journey):
        """19.99 is not representable in binary floating point, and a church's
        totals should not drift by a cent a year."""
        gift = add_gift(db, journey, "txn_1", 1999, TODAY)
        assert isinstance(gift.amount_cents, int)
        assert gift.amount_display == "$19.99"


class TestGivingThatStopped:
    def _recurring(self, db, journey, last_gift_days_ago, frequency="monthly",
                   status="active", person=None):
        recurring = ExternalRecurringGift(
            church_id=journey.id,
            provider=PROVIDER_TITHELY,
            provider_ref=f"rec_{last_gift_days_ago}_{frequency}_{status}",
            amount_cents=4000,
            frequency=frequency,
            status=status,
            person_id=person.id if person else None,
            last_success_on=utcnow().date() - timedelta(days=last_gift_days_ago),
        )
        db.session.add(recurring)
        db.session.commit()
        return recurring

    def test_a_monthly_gift_on_time_has_not_stopped(self, db, journey):
        assert not self._recurring(db, journey, 20).has_stopped

    def test_a_failed_card_that_retried_is_not_a_person_leaving(self, db, journey):
        """Three days late is a payment system doing its job."""
        assert not self._recurring(db, journey, 34).has_stopped

    def test_silence_past_the_grace_period_has_stopped(self, db, journey):
        assert self._recurring(db, journey, 31 + LAPSE_GRACE_DAYS + 1).has_stopped

    def test_a_weekly_gift_lapses_sooner_than_a_monthly_one(self, db, journey):
        """The window is per frequency, not one number for everyone."""
        assert self._recurring(db, journey, 40, frequency="weekly").has_stopped
        assert not self._recurring(db, journey, 40, frequency="monthly").has_stopped

    def test_a_cancelled_arrangement_is_not_stopped(self, db, journey):
        """They told the church. That is not a signal, it is a decision."""
        assert not self._recurring(
            db, journey, 400, status=RECURRING_CANCELLED
        ).has_stopped

    def test_an_arrangement_that_never_gave_is_not_stopped(self, db, journey):
        recurring = ExternalRecurringGift(
            church_id=journey.id, provider=PROVIDER_TITHELY, provider_ref="rec_new",
            amount_cents=4000, frequency="monthly", last_success_on=None,
        )
        db.session.add(recurring)
        db.session.commit()
        assert not recurring.has_stopped

    def test_the_query_agrees_with_the_property(self, db, journey, chris):
        """A dashboard that disagrees with the record it links to is worse
        than no dashboard."""
        self._recurring(db, journey, 10)
        self._recurring(db, journey, 90, person=chris)
        self._recurring(db, journey, 400, status=RECURRING_CANCELLED)
        self._recurring(db, journey, 40, frequency="weekly")

        by_query = {r.id for r in db.session.scalars(ExternalRecurringGift.stopped(journey.id))}
        by_property = {
            r.id
            for r in db.session.scalars(
                db.select(ExternalRecurringGift).where(
                    ExternalRecurringGift.church_id == journey.id
                )
            )
            if r.has_stopped
        }
        assert by_query == by_property

    def test_the_reason_names_the_amount_and_the_gap(self, db, journey, chris):
        recurring = self._recurring(db, journey, 90, person=chris)
        assert "$40.00" in recurring.stopped_reason
        assert "90 days" in recurring.stopped_reason

    def test_the_count_is_scoped_to_one_church(self, db, journey):
        riverbend = db.session.scalar(db.select(Church).where(Church.slug == "riverbend"))
        self._recurring(db, journey, 90)
        db.session.add(
            ExternalRecurringGift(
                church_id=riverbend.id, provider=PROVIDER_TITHELY,
                provider_ref="theirs", amount_cents=1000, frequency="monthly",
                last_success_on=utcnow().date() - timedelta(days=200),
            )
        )
        db.session.commit()
        assert ExternalRecurringGift.stopped_count(journey.id) == 1


class TestReviewQueue:
    def test_unmatched_gifts_are_listed(self, db, journey, staff):
        add_gift(db, journey, "txn_1", 5000, TODAY, name="Unknown Donor")
        r = staff.get("/giving/unmatched/", headers={"Host": JOURNEY_HOST})
        assert r.status_code == 200
        assert b"Unknown Donor" in r.data

    def test_attaching_a_gift_to_a_person(self, db, journey, chris, staff):
        gift = add_gift(db, journey, "txn_1", 5000, TODAY, name="C Vaughn")
        staff.post(
            f"/giving/unmatched/{gift.id}/attach/",
            data={"person_id": chris.id},
            headers={"Host": JOURNEY_HOST},
        )
        db.session.refresh(gift)
        assert gift.person_id == chris.id
        assert gift.match_status == MATCH_MATCHED
        assert gift.matched_by_user_id is not None

    def test_setting_a_gift_aside(self, db, journey, staff):
        """A business, an anonymous gift, a test transaction."""
        gift = add_gift(db, journey, "txn_1", 5000, TODAY, name="Some Company LLC")
        staff.post(
            f"/giving/unmatched/{gift.id}/ignore/", headers={"Host": JOURNEY_HOST}
        )
        db.session.refresh(gift)
        assert gift.match_status == MATCH_IGNORED
        assert gift.person_id is None

    def test_a_person_from_another_church_cannot_be_attached(self, db, journey, staff):
        gift = add_gift(db, journey, "txn_1", 5000, TODAY)
        riverbend = db.session.scalar(db.select(Church).where(Church.slug == "riverbend"))
        stranger = Person(
            church_id=riverbend.id, first_name="Not", last_name="Ours", stage="member"
        )
        db.session.add(stranger)
        db.session.commit()

        r = staff.post(
            f"/giving/unmatched/{gift.id}/attach/",
            data={"person_id": stranger.id},
            headers={"Host": JOURNEY_HOST},
        )
        assert r.status_code == 400
        db.session.refresh(gift)
        assert gift.person_id is None

    def test_a_gift_from_another_church_is_a_404(self, db, staff):
        riverbend = db.session.scalar(db.select(Church).where(Church.slug == "riverbend"))
        theirs = ExternalGift(
            church_id=riverbend.id, provider=PROVIDER_TITHELY,
            provider_txn_id="theirs", amount_cents=1000, received_on=TODAY,
        )
        db.session.add(theirs)
        db.session.commit()

        r = staff.post(
            f"/giving/unmatched/{theirs.id}/attach/",
            data={"person_id": 1},
            headers={"Host": JOURNEY_HOST},
        )
        assert r.status_code == 404

    def test_a_leader_cannot_see_the_queue(self, leader):
        assert leader.get(
            "/giving/unmatched/", headers={"Host": JOURNEY_HOST}
        ).status_code == 403


class TestGivingOnThePersonRecord:
    def test_the_summary_totals_only_this_person(self, db, journey, chris):
        add_gift(db, journey, "t1", 5000, TODAY, person=chris)
        add_gift(db, journey, "t2", 2500, TODAY - timedelta(days=30), person=chris)
        add_gift(db, journey, "t3", 9999, TODAY)

        summary = ExternalGift.person_summary(journey.id, chris.id)
        assert summary["count"] == 2
        assert summary["total_cents"] == 7500

    def test_the_person_page_shows_gifts(self, db, journey, chris, staff):
        add_gift(db, journey, "t1", 5000, TODAY, person=chris)
        r = staff.get(f"/people/{chris.id}/", headers={"Host": JOURNEY_HOST})
        assert b"$50.00" in r.data

    def test_a_person_with_no_gifts_says_so(self, db, journey, chris, staff):
        r = staff.get(f"/people/{chris.id}/", headers={"Host": JOURNEY_HOST})
        assert b"No gifts recorded" in r.data

    def test_month_to_date(self, db, journey, chris):
        today = utcnow().date()
        add_gift(db, journey, "t1", 5000, today, person=chris)
        add_gift(db, journey, "t2", 2500, today.replace(day=1) - timedelta(days=5),
                 person=chris)
        assert ExternalGift.month_to_date_cents(journey.id) == 5000


class TestCSVImport:
    def _write(self, tmp_path, rows):
        import csv

        header = ["transaction_id", "donor_name", "donor_email", "amount",
                  "fund", "method", "date"]
        path = tmp_path / "gifts.csv"
        with open(path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=header)
            writer.writeheader()
            writer.writerows(rows)
        return str(path)

    def test_a_clean_file_imports_and_auto_matches_on_email(
        self, app, db, journey, chris, tmp_path
    ):
        path = self._write(tmp_path, [{
            "transaction_id": "t1", "donor_name": "Chris Vaughn",
            "donor_email": "chris.vaughn@example.com", "amount": "40.00",
            "fund": "General", "method": "card", "date": "2026-08-02",
        }])
        result = app.test_cli_runner().invoke(
            args=["import-gifts", "--church", "journey", "--file", path]
        )
        assert result.exit_code == 0

        gift = db.session.scalars(db.select(ExternalGift)).one()
        assert gift.amount_cents == 4000
        assert gift.person_id == chris.id
        assert gift.match_status == MATCH_MATCHED

    def test_a_name_only_row_lands_in_the_queue(self, app, db, journey, chris, tmp_path):
        """A name is not an identifier, so a human decides."""
        path = self._write(tmp_path, [{
            "transaction_id": "t1", "donor_name": "Chris Vaughn", "donor_email": "",
            "amount": "40.00", "fund": "", "method": "", "date": "2026-08-02",
        }])
        app.test_cli_runner().invoke(
            args=["import-gifts", "--church", "journey", "--file", path]
        )
        gift = db.session.scalars(db.select(ExternalGift)).one()
        assert gift.match_status == MATCH_UNMATCHED

    def test_importing_twice_does_not_double_the_totals(
        self, app, db, journey, tmp_path
    ):
        path = self._write(tmp_path, [{
            "transaction_id": "t1", "donor_name": "A", "donor_email": "",
            "amount": "40.00", "fund": "", "method": "", "date": "2026-08-02",
        }])
        runner = app.test_cli_runner()
        runner.invoke(args=["import-gifts", "--church", "journey", "--file", path])
        result = runner.invoke(args=["import-gifts", "--church", "journey", "--file", path])

        assert "updated 1" in result.output
        assert len(db.session.scalars(db.select(ExternalGift)).all()) == 1

    def test_a_reimport_does_not_undo_a_human_match(
        self, app, db, journey, chris, tmp_path
    ):
        """The provider owns the ledger. The match is ours."""
        path = self._write(tmp_path, [{
            "transaction_id": "t1", "donor_name": "C Vaughn", "donor_email": "",
            "amount": "40.00", "fund": "", "method": "", "date": "2026-08-02",
        }])
        runner = app.test_cli_runner()
        runner.invoke(args=["import-gifts", "--church", "journey", "--file", path])

        gift = db.session.scalars(db.select(ExternalGift)).one()
        gift.attach(chris)
        db.session.commit()

        runner.invoke(args=["import-gifts", "--church", "journey", "--file", path])
        db.session.expire_all()
        gift = db.session.scalars(db.select(ExternalGift)).one()
        assert gift.person_id == chris.id

    def test_one_bad_row_writes_nothing(self, app, db, journey, tmp_path):
        path = self._write(tmp_path, [
            {"transaction_id": "t1", "donor_name": "Good", "donor_email": "",
             "amount": "40.00", "fund": "", "method": "", "date": "2026-08-02"},
            {"transaction_id": "t2", "donor_name": "Bad", "donor_email": "",
             "amount": "forty dollars", "fund": "", "method": "", "date": "2026-08-02"},
        ])
        result = app.test_cli_runner().invoke(
            args=["import-gifts", "--church", "journey", "--file", path]
        )
        assert result.exit_code != 0
        assert db.session.scalars(db.select(ExternalGift)).all() == []

    def test_a_row_with_no_transaction_id_is_refused(self, app, db, journey, tmp_path):
        """Without it there is nothing to dedupe on, so a re-import doubles."""
        path = self._write(tmp_path, [{
            "transaction_id": "", "donor_name": "A", "donor_email": "",
            "amount": "40.00", "fund": "", "method": "", "date": "2026-08-02",
        }])
        result = app.test_cli_runner().invoke(
            args=["import-gifts", "--church", "journey", "--file", path]
        )
        assert result.exit_code != 0
        assert "deduped" in result.output

    def test_amounts_are_parsed_without_floats(self, app, db, journey, tmp_path):
        path = self._write(tmp_path, [{
            "transaction_id": "t1", "donor_name": "A", "donor_email": "",
            "amount": "$1,234.56", "fund": "", "method": "", "date": "2026-08-02",
        }])
        app.test_cli_runner().invoke(
            args=["import-gifts", "--church", "journey", "--file", path]
        )
        assert db.session.scalars(db.select(ExternalGift)).one().amount_cents == 123456

    def test_a_dry_run_writes_nothing(self, app, db, journey, tmp_path):
        path = self._write(tmp_path, [{
            "transaction_id": "t1", "donor_name": "A", "donor_email": "",
            "amount": "40.00", "fund": "", "method": "", "date": "2026-08-02",
        }])
        result = app.test_cli_runner().invoke(
            args=["import-gifts", "--church", "journey", "--file", path, "--dry-run"]
        )
        assert "Nothing written" in result.output
        assert db.session.scalars(db.select(ExternalGift)).all() == []

    def test_rematching_after_a_roster_import(self, app, db, journey, tmp_path):
        """Emails often arrive on the roster after the giving does."""
        path = self._write(tmp_path, [{
            "transaction_id": "t1", "donor_name": "Late Arrival",
            "donor_email": "late@example.com", "amount": "40.00",
            "fund": "", "method": "", "date": "2026-08-02",
        }])
        runner = app.test_cli_runner()
        runner.invoke(args=["import-gifts", "--church", "journey", "--file", path])
        assert ExternalGift.unmatched_count(journey.id) == 1

        db.session.add(
            Person(
                church_id=journey.id, first_name="Late", last_name="Arrival",
                email="late@example.com", stage="member",
            )
        )
        db.session.commit()

        result = runner.invoke(args=["match-gifts", "--church", "journey"])
        assert "Matched 1" in result.output
        assert ExternalGift.unmatched_count(journey.id) == 0


class TestTheQueueDoesNotInviteAMistake:
    def test_confirming_without_choosing_attaches_nothing(self, db, journey, staff):
        """The dropdown used to default to whoever was first alphabetically,
        one click from a green confirm button."""
        gift = add_gift(db, journey, "txn_1", 5000, TODAY, name="Hilltop Coffee LLC")
        r = staff.post(
            f"/giving/unmatched/{gift.id}/attach/",
            data={"person_id": ""},
            headers={"Host": JOURNEY_HOST},
            follow_redirects=True,
        )
        assert b"Pick who this is" in r.data
        db.session.refresh(gift)
        assert gift.person_id is None
        assert gift.match_status == MATCH_UNMATCHED

    def test_the_queue_offers_a_non_person_default(self, db, journey, staff):
        add_gift(db, journey, "txn_1", 5000, TODAY, name="Hilltop Coffee LLC")
        r = staff.get("/giving/unmatched/", headers={"Host": JOURNEY_HOST})
        assert b'<option value="">Choose a person</option>' in r.data
