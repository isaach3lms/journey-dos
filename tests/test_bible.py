"""Increment 8: the Bible.

Two things carry the weight. Reference parsing, because a pastor types what he
says and the shapes are irregular. And the licensing rule, because a copy of a
licensed translation sitting in this database, replicated across every backup,
is a violation that grows quietly and is found by somebody else.
"""

import urllib.error

import pytest

from app.bible import parse
from app.bible.providers import (
    PROVIDER_YOUVERSION,
    Passage,
    Verse,
    WebBibleProvider,
    YouVersionProvider,
    fetch_passage,
    provider_for,
)
from app.bible.reference import BOOK_NAMES, CHAPTER_COUNTS, normalize_book
from app.models import BibleVerse, Church, IntegrationCredential
from tests.conftest import JOURNEY_HOST

SAMPLE = [
    ("Psalms", 139, 1, "Yahweh, you have searched me, and you know me."),
    ("Psalms", 139, 2, "You know my sitting down and my rising up."),
    ("Psalms", 139, 3, "You search out my path and my lying down."),
    ("John", 1, 47, "Behold, an Israelite indeed, in whom is no deceit!"),
]


@pytest.fixture
def journey(db):
    church = db.session.scalar(db.select(Church).where(Church.slug == "journey"))
    church.timezone = "America/Chicago"
    db.session.commit()
    return church


@pytest.fixture
def web_text(db):
    for book, chapter, verse, text in SAMPLE:
        db.session.add(
            BibleVerse(book=book, chapter=chapter, verse=verse, text=text)
        )
    db.session.commit()


class TestReferenceParsing:
    def test_a_plain_reference(self):
        reference = parse("Psalm 139:1-6")
        assert (reference.book, reference.chapter) == ("Psalms", 139)
        assert (reference.start_verse, reference.end_verse) == (1, 6)

    def test_a_whole_chapter(self):
        reference = parse("John 1")
        assert reference.is_whole_chapter
        assert reference.start_verse is None

    def test_a_single_verse(self):
        assert str(parse("John 3:16")) == "John 3:16"

    def test_psalm_and_psalms_are_one_book(self):
        assert parse("Psalm 23").book == parse("Psalms 23").book == "Psalms"

    def test_a_leading_number_belongs_to_the_book(self):
        """"1 Corinthians" is not Corinthians chapter 1."""
        reference = parse("1 Cor 13:4-7")
        assert reference.book == "1 Corinthians"
        assert reference.chapter == 13

    def test_roman_and_written_ordinals(self):
        assert parse("I John 4").book == "1 John"
        assert parse("First John 4").book == "1 John"
        assert parse("II Timothy 2").book == "2 Timothy"

    def test_no_space_after_the_ordinal(self):
        assert parse("1john 4:8").book == "1 John"

    def test_revelations_is_wrong_and_universal(self):
        assert parse("Revelations 21:1").book == "Revelation"

    def test_abbreviations(self):
        for written, expected in [
            ("Ps 23", "Psalms"), ("Mt 5:3", "Matthew"), ("Heb 10:24", "Hebrews"),
            ("Gen 1:1", "Genesis"), ("Rom 8", "Romans"),
        ]:
            assert parse(written).book == expected, written

    def test_a_one_chapter_book_needs_no_chapter(self):
        assert str(parse("Jude")) == "Jude 1"
        assert str(parse("Philemon")) == "Philemon 1"

    def test_a_dot_separator(self):
        assert str(parse("John 3.16")) == "John 3:16"

    def test_an_en_dash_range(self):
        assert parse("Psalm 139:1\u20136").end_verse == 6

    def test_a_backwards_range_reads_as_one_verse(self):
        """A typo, not an instruction. One verse is the least surprising thing
        to do with it."""
        assert parse("John 3:16-12").end_verse == 16

    def test_a_chapter_past_the_end_of_the_book_is_refused(self):
        assert parse("Jude 4") is None
        assert parse("Psalm 200") is None

    @pytest.mark.parametrize("bad", ["", None, "   ", "Nonsense 4", "Hezekiah 3:2", "12345"])
    def test_nonsense_returns_none_rather_than_raising(self, bad):
        """A typo in a plan should show one apologetic card, not take down the
        page a member is reading."""
        assert parse(bad) is None

    def test_every_book_parses_by_its_own_name(self):
        for name in BOOK_NAMES:
            reference = parse(f"{name} 1")
            assert reference is not None and reference.book == name, name

    def test_chapter_counts_are_present_for_every_book(self):
        assert set(CHAPTER_COUNTS) == set(BOOK_NAMES)

    def test_normalize_book(self):
        assert normalize_book("  PSALM ") == "Psalms"
        assert normalize_book("nonsense") is None
        assert normalize_book(None) is None

    def test_covers(self):
        reference = parse("Psalm 139:1-6")
        assert reference.covers(3)
        assert not reference.covers(7)
        assert parse("John 1").covers(999)


class TestWebProvider:
    def test_it_returns_the_stored_verses(self, db, web_text):
        passage = WebBibleProvider().fetch(parse("Psalm 139:1-3"))
        assert len(passage.verses) == 3
        assert passage.verses[0].number == 1
        assert "searched me" in passage.verses[0].text

    def test_a_range_is_respected(self, db, web_text):
        passage = WebBibleProvider().fetch(parse("Psalm 139:2"))
        assert [v.number for v in passage.verses] == [2]

    def test_a_whole_chapter_returns_everything_loaded(self, db, web_text):
        passage = WebBibleProvider().fetch(parse("Psalm 139"))
        assert len(passage.verses) == 3

    def test_a_passage_not_loaded_is_empty_not_an_error(self, db, web_text):
        assert WebBibleProvider().fetch(parse("Romans 8:28")).is_empty

    def test_it_needs_no_key_and_no_network(self, db, web_text):
        """The floor: every church, no registration, nobody's permission."""
        passage = WebBibleProvider().fetch(parse("John 1:47"))
        assert not passage.is_empty
        assert passage.translation_code == "web"


class TestLicensedTextIsNeverStored:
    """The rule that makes this shippable without a signature."""

    def test_the_verse_table_has_no_translation_column(self):
        """There is nowhere for NIV to accumulate, by construction."""
        columns = {c.name for c in BibleVerse.__table__.columns}
        assert "translation" not in columns
        assert "version" not in columns

    def test_the_verse_table_is_global_not_tenant_scoped(self):
        """Scripture is not a church's data, and copying 31,000 verses per
        tenant would be absurd."""
        columns = {c.name for c in BibleVerse.__table__.columns}
        assert "church_id" not in columns

    def test_a_licensed_passage_is_not_written_to_the_database(
        self, db, journey, web_text, monkeypatch
    ):
        before = BibleVerse.verse_count()

        def fake_fetch(self, reference):
            return Passage(
                reference=str(reference),
                translation_code="niv",
                translation_name="NIV",
                verses=(Verse(1, "Licensed text that must never be stored."),),
            )

        monkeypatch.setattr(YouVersionProvider, "fetch", fake_fetch)
        credential = IntegrationCredential(
            church_id=journey.id, provider=PROVIDER_YOUVERSION, public_key="pub"
        )
        credential.set_private_key("yv_key_123", "secret")
        db.session.add(credential)
        db.session.commit()

        passage = fetch_passage(parse("Psalm 139:1"), journey, "secret")
        assert passage.translation_code == "niv"

        db.session.expire_all()
        assert BibleVerse.verse_count() == before
        stored = " ".join(v.text for v in db.session.scalars(db.select(BibleVerse)))
        assert "must never be stored" not in stored


class TestProviderResolution:
    def test_no_credential_means_the_public_domain_text(self, db, journey):
        assert isinstance(provider_for(journey, "secret"), WebBibleProvider)

    def test_an_unusable_credential_falls_back(self, db, journey):
        db.session.add(
            IntegrationCredential(
                church_id=journey.id, provider=PROVIDER_YOUVERSION, public_key="pub"
            )
        )
        db.session.commit()
        assert isinstance(provider_for(journey, "secret"), WebBibleProvider)

    def test_a_wrong_secret_key_falls_back_rather_than_raising(self, db, journey):
        credential = IntegrationCredential(
            church_id=journey.id, provider=PROVIDER_YOUVERSION, public_key="pub"
        )
        credential.set_private_key("yv_key_123", "the-right-key")
        db.session.add(credential)
        db.session.commit()

        assert isinstance(provider_for(journey, "the-wrong-key"), WebBibleProvider)

    def test_a_registered_church_gets_the_licensed_provider(self, db, journey):
        credential = IntegrationCredential(
            church_id=journey.id, provider=PROVIDER_YOUVERSION, public_key="pub"
        )
        credential.set_private_key("yv_key_123", "secret")
        db.session.add(credential)
        db.session.commit()

        assert isinstance(provider_for(journey, "secret"), YouVersionProvider)

    def test_the_credential_is_per_church(self, db, journey):
        """Per spec C.5 each church registers and we operate on their behalf."""
        riverbend = db.session.scalar(db.select(Church).where(Church.slug == "riverbend"))
        credential = IntegrationCredential(
            church_id=journey.id, provider=PROVIDER_YOUVERSION, public_key="pub"
        )
        credential.set_private_key("yv_key_123", "secret")
        db.session.add(credential)
        db.session.commit()

        assert isinstance(provider_for(riverbend, "secret"), WebBibleProvider)

    def test_constructing_a_provider_without_a_key_is_refused(self):
        with pytest.raises(ValueError, match="World English Bible"):
            YouVersionProvider("")


class TestFallbackNeverFails:
    """A member opening a plan on a Sunday should never see an error where a
    psalm was meant to be."""

    def _register(self, db, journey):
        credential = IntegrationCredential(
            church_id=journey.id, provider=PROVIDER_YOUVERSION, public_key="pub"
        )
        credential.set_private_key("yv_key_123", "secret")
        db.session.add(credential)
        db.session.commit()

    def test_a_refused_request_falls_back_and_says_so(
        self, db, journey, web_text, monkeypatch
    ):
        self._register(db, journey)

        def boom(self, reference):
            raise urllib.error.HTTPError("u", 403, "Forbidden", {}, None)

        monkeypatch.setattr(YouVersionProvider, "fetch", boom)
        passage = fetch_passage(parse("Psalm 139:1"), journey, "secret")

        assert passage.translation_code == "web"
        assert passage.fell_back
        assert "403" in passage.fallback_reason
        assert not passage.is_empty

    def test_an_unreachable_service_falls_back(self, db, journey, web_text, monkeypatch):
        self._register(db, journey)

        def boom(self, reference):
            raise urllib.error.URLError("no route to host")

        monkeypatch.setattr(YouVersionProvider, "fetch", boom)
        assert fetch_passage(parse("Psalm 139:1"), journey, "secret").fell_back

    def test_an_empty_licensed_response_falls_back(self, db, journey, web_text, monkeypatch):
        self._register(db, journey)
        monkeypatch.setattr(
            YouVersionProvider, "fetch",
            lambda self, reference: Passage(str(reference), "niv", "NIV"),
        )
        passage = fetch_passage(parse("Psalm 139:1"), journey, "secret")
        assert passage.fell_back
        assert passage.translation_code == "web"

    def test_an_unparseable_reference_returns_an_empty_passage(self, db, journey):
        assert fetch_passage(parse("Nonsense 4"), journey, "secret").is_empty

    def test_no_fallback_flag_when_the_web_was_the_choice(self, db, journey, web_text):
        """Not a fallback if nobody asked for anything else."""
        passage = fetch_passage(parse("Psalm 139:1"), journey, "secret")
        assert not passage.fell_back


class TestInTheReader:
    def _plan(self, db, journey, reference="Psalm 139:1-3"):
        from app.models import Resource, ResourceSession

        resource = Resource(
            church_id=journey.id, title="Known", kind="reading_plan", status="published"
        )
        db.session.add(resource)
        db.session.flush()
        session = ResourceSession(
            church_id=journey.id, resource_id=resource.id, position=1,
            title="Day 1", passage_ref=reference, body="Read it slowly.",
        )
        db.session.add(session)
        db.session.commit()
        return resource, session

    def _link_member(self, db, journey):
        from app.models import Person, User

        person = Person(
            church_id=journey.id, first_name="Alicia", last_name="Romero",
            email="member@journeychurchsemo.com", stage="attender",
        )
        db.session.add(person)
        db.session.flush()
        user = db.session.scalar(
            db.select(User).where(
                User.email == "member@journeychurchsemo.com",
                User.church_id == journey.id,
            )
        )
        user.person_id = person.id
        db.session.commit()

    def test_the_passage_appears_inline(self, db, journey, web_text, member):
        self._link_member(db, journey)
        resource, session = self._plan(db, journey)

        r = member.get(
            f"/me/read/{resource.id}/{session.id}/", headers={"Host": JOURNEY_HOST}
        )
        assert r.status_code == 200
        assert b"searched me" in r.data
        assert b"World English Bible" in r.data

    def test_a_passage_we_do_not_have_says_so_kindly(self, db, journey, web_text, member):
        self._link_member(db, journey)
        resource, session = self._plan(db, journey, reference="Romans 8:28")

        r = member.get(
            f"/me/read/{resource.id}/{session.id}/", headers={"Host": JOURNEY_HOST}
        )
        assert r.status_code == 200
        assert b"do not have the text" in r.data

    def test_a_typo_in_a_plan_does_not_break_the_page(self, db, journey, web_text, member):
        self._link_member(db, journey)
        resource, session = self._plan(db, journey, reference="Hezekiah 3:2")

        r = member.get(
            f"/me/read/{resource.id}/{session.id}/", headers={"Host": JOURNEY_HOST}
        )
        assert r.status_code == 200
        assert b"Read it slowly" in r.data


class TestImport:
    def test_it_loads_verses(self, app, db, tmp_path):
        import json

        path = tmp_path / "web.json"
        path.write_text(json.dumps([
            {"book": "Romans", "chapter": 8, "verse": 28, "text": "We know that all things work together"},
        ]))
        result = app.test_cli_runner().invoke(
            args=["import-bible", "--file", str(path)]
        )
        assert result.exit_code == 0
        assert not WebBibleProvider().fetch(parse("Romans 8:28")).is_empty

    def test_it_normalizes_book_names(self, app, db, tmp_path):
        import json

        path = tmp_path / "web.json"
        path.write_text(json.dumps([
            {"book": "Psalm", "chapter": 1, "verse": 1, "text": "Blessed is the man"},
        ]))
        app.test_cli_runner().invoke(args=["import-bible", "--file", str(path)])
        assert db.session.scalars(db.select(BibleVerse)).one().book == "Psalms"

    def test_a_bad_book_writes_nothing(self, app, db, tmp_path):
        import json

        path = tmp_path / "web.json"
        path.write_text(json.dumps([
            {"book": "Romans", "chapter": 8, "verse": 28, "text": "Good"},
            {"book": "Hezekiah", "chapter": 1, "verse": 1, "text": "Bad"},
        ]))
        result = app.test_cli_runner().invoke(
            args=["import-bible", "--file", str(path)]
        )
        assert result.exit_code != 0
        assert db.session.scalars(db.select(BibleVerse)).all() == []

    def test_reimporting_updates_rather_than_duplicates(self, app, db, tmp_path):
        import json

        path = tmp_path / "web.json"
        path.write_text(json.dumps([
            {"book": "Romans", "chapter": 8, "verse": 28, "text": "First wording"},
        ]))
        runner = app.test_cli_runner()
        runner.invoke(args=["import-bible", "--file", str(path)])

        path.write_text(json.dumps([
            {"book": "Romans", "chapter": 8, "verse": 28, "text": "Second wording"},
        ]))
        result = runner.invoke(args=["import-bible", "--file", str(path)])
        assert "updated 1" in result.output
        assert db.session.scalars(db.select(BibleVerse)).one().text == "Second wording"
