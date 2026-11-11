"""CCLI license number and SongSelect import."""

from io import BytesIO

import pytest

from app import songselect
from app.models import Church, Song
from tests.conftest import JOURNEY_HOST

H = {"Host": JOURNEY_HOST}

USR = (
    "[File]\r\n"
    "Type=SongSelect Import File\r\n"
    "Version=3.0\r\n"
    "[S A6016351]\r\n"
    "Title=10,000 Reasons (Bless The Lord)\r\n"
    "Author=Jonas Myrin | Matt Redman\r\n"
    "Copyright=2011 Atlas Mountain Songs\r\n"
    "Admin=Capitol CMG Publishing\r\n"
    "Keys=G/tA\r\n"
    "Fields=Chorus/tVerse 1\r\n"
    "Words=Bless the Lord O my soul/nO my soul/t"
    "The sun comes up/nIt's a new day dawning\r\n"
)

TXT = """Way Maker

Verse 1
You are here moving in our midst
I worship You I worship You

Chorus
Way maker miracle worker promise keeper

CCLI Song # 7115744
Osinachi Kalu Okoro Egbu
© 2016 Integrity Music Europe
For use solely with the SongSelect® Terms of Use. All rights reserved. www.ccli.com
CCLI License # 1111111
"""

CHORDPRO = """{title: Goodness Of God}
{artist: Ed Cash, Jason Ingram}
{key: Ab}
{tempo: 63}
{time: 6/8}

[Ab]I love You Lord
Oh Your [Db]mercy never fails me

{comment: CCLI Song # 7117726}
{comment: CCLI License # 2222222}
"""


def journey(db):
    return db.session.scalar(db.select(Church).where(Church.slug == "journey"))


def songs(db):
    return db.session.scalars(
        db.select(Song).where(Song.church_id == journey(db).id).order_by(Song.title)
    ).all()


def upload(client, *files):
    data = {"files": [(BytesIO(body), name) for name, body in files]}
    return client.post(
        "/services/songs/import/", data=data, headers=H,
        content_type="multipart/form-data", follow_redirects=True,
    )


# ---------------------------------------------------------------------------
# Reading the files
# ---------------------------------------------------------------------------

class TestParsing:
    def test_usr(self):
        meta = songselect.parse("10000-reasons.usr", USR.encode())
        assert meta.title == "10,000 Reasons (Bless The Lord)"
        assert meta.author == "Jonas Myrin, Matt Redman"
        assert meta.ccli_number == "6016351"
        assert meta.default_key == "G"  # first of the listed keys

    def test_txt(self):
        meta = songselect.parse("way-maker.txt", TXT.encode())
        assert meta.title == "Way Maker"
        assert meta.ccli_number == "7115744"  # the song, not the church licence
        assert meta.author == "Osinachi Kalu Okoro Egbu"

    def test_chordpro(self):
        meta = songselect.parse("goodness.chordpro", CHORDPRO.encode())
        assert meta.title == "Goodness Of God"
        assert meta.author == "Ed Cash, Jason Ingram"
        assert meta.ccli_number == "7117726"
        assert meta.default_key == "Ab"
        assert meta.tempo_bpm == 63

    @pytest.mark.parametrize("ext", ["cho", "chopro", "pro", "crd"])
    def test_every_chordpro_extension(self, ext):
        assert songselect.parse(f"song.{ext}", CHORDPRO.encode()).title == "Goodness Of God"

    def test_windows_1252_and_utf16(self):
        text = TXT.replace("Way Maker", "Oceans (Where Feet May Fail)")
        assert songselect.parse("a.txt", text.encode("cp1252")).title.startswith("Oceans")
        assert songselect.parse("b.txt", text.encode("utf-16")).title.startswith("Oceans")

    def test_lyrics_are_never_returned(self):
        for name, body in (("a.usr", USR), ("b.txt", TXT), ("c.cho", CHORDPRO)):
            meta = songselect.parse(name, body.encode())
            joined = " ".join(str(v) for v in vars(meta).values())
            assert "soul" not in joined
            assert "midst" not in joined
            assert "mercy" not in joined

    def test_an_unspellable_key_is_dropped_not_guessed(self):
        meta = songselect.parse("x.usr", USR.replace("Keys=G/tA", "Keys=H#").encode())
        assert meta.default_key is None
        assert meta.title  # the rest still imports

    @pytest.mark.parametrize("name,body,reason", [
        ("scan.pdf", b"%PDF-1.7 no text here", "pdf_unreadable"),
        ("fake.pdf", b"<html>not a pdf</html>", "wrong_type"),
        ("renamed.txt", b"%PDF-1.7 a pdf called txt", "wrong_type"),
        ("notes.txt", b"Things to bring Sunday\nCoffee\n", "no_ccli"),
        ("song.docx", b"PK\x03\x04", "wrong_type"),
        ("song", b"no extension", "wrong_type"),
        ("empty.usr", b"   ", "empty"),
        ("broken.usr", b"[File]\nType=nothing\n", "unreadable"),
        ("huge.txt", b"x" * (songselect.MAX_FILE_BYTES + 1), "too_big"),
    ])
    def test_refusals_carry_a_reason(self, name, body, reason):
        with pytest.raises(songselect.NotASong) as refused:
            songselect.parse(name, body)
        assert refused.value.reason == reason

    def test_every_reason_has_copy(self):
        from app.content import SERVICES
        for reason in ("empty", "too_big", "wrong_type", "unreadable", "no_title", "no_ccli",
                       "pdf_unreadable", "pdf_no_ccli"):
            assert f"import_why_{reason}" in SERVICES


# ---------------------------------------------------------------------------
# Importing
# ---------------------------------------------------------------------------

class TestImport:
    def test_three_formats_at_once(self, db, leader):
        response = upload(leader, ("a.usr", USR.encode()), ("b.txt", TXT.encode()),
                          ("c.chordpro", CHORDPRO.encode()))
        assert response.status_code == 200
        assert b"3 added to your songs." in response.data
        titles = [s.title for s in songs(db)]
        assert titles == ["10,000 Reasons (Bless The Lord)", "Goodness Of God", "Way Maker"]

    def test_no_lyrics_reach_the_database(self, db, leader):
        upload(leader, ("a.usr", USR.encode()))
        [song] = songs(db)
        stored = " ".join(str(getattr(song, c.key)) for c in Song.__table__.columns)
        assert "soul" not in stored

    def test_the_same_song_twice_is_one_song(self, db, leader):
        upload(leader, ("a.usr", USR.encode()), ("again.usr", USR.encode()))
        assert len(songs(db)) == 1
        response = upload(leader, ("a.usr", USR.encode()))
        assert b"1 already here with nothing new." in response.data
        assert len(songs(db)) == 1

    def test_fills_blanks_but_never_overwrites_what_staff_typed(self, db, leader):
        church = journey(db)
        db.session.add(Song(church_id=church.id, title="10,000 Reasons (Bless The Lord)", default_key="A"))
        db.session.commit()
        response = upload(leader, ("a.usr", USR.encode()))
        assert b"1 already here, details filled in." in response.data
        [song] = songs(db)
        assert song.ccli_number == "6016351"
        assert song.author == "Jonas Myrin, Matt Redman"
        assert song.default_key == "A"  # theirs, kept

    def test_matched_on_ccli_number_before_title(self, db, leader):
        church = journey(db)
        db.session.add(Song(church_id=church.id, title="10000 Reasons", ccli_number="6016351"))
        db.session.commit()
        upload(leader, ("a.usr", USR.encode()))
        [song] = songs(db)
        assert song.title == "10000 Reasons"

    def test_importing_an_archived_song_brings_it_back(self, db, leader):
        church = journey(db)
        db.session.add(Song(church_id=church.id, title="Way Maker", ccli_number="7115744", is_active=False))
        db.session.commit()
        upload(leader, ("b.txt", TXT.encode()))
        assert songs(db)[0].is_active is True

    def test_one_bad_file_does_not_stop_the_rest(self, db, leader):
        response = upload(leader, ("scan.pdf", b"%PDF-1.7"), ("b.txt", TXT.encode()))
        assert b"1 added to your songs." in response.data
        assert b"scan.pdf" in response.data
        assert b"attach the PDF on its row" in response.data
        assert [s.title for s in songs(db)] == ["Way Maker"]

    def test_nothing_chosen(self, db, leader):
        response = leader.post("/services/songs/import/", data={}, headers=H, follow_redirects=True)
        assert b"Choose at least one file" in response.data

    def test_too_many_files(self, db, leader):
        files = [(f"{i}.txt", TXT.encode()) for i in range(songselect.MAX_FILES + 1)]
        response = upload(leader, *files)
        assert b"Import up to" in response.data
        assert songs(db) == []

    def test_filename_is_escaped_in_the_message(self, db, leader):
        response = upload(leader, ("<script>x</script>.pdf", b"%PDF-1.7"))
        assert b"<script>x</script>" not in response.data

    def test_members_cannot_import(self, db, member):
        response = upload(member, ("b.txt", TXT.encode()))
        assert songs(db) == []
        assert response.status_code == 403

    def test_imports_stay_in_their_own_church(self, db, leader):
        upload(leader, ("b.txt", TXT.encode()))
        other = db.session.scalars(
            db.select(Song).where(Song.church_id != journey(db).id)
        ).all()
        assert other == []


# ---------------------------------------------------------------------------
# The license number
# ---------------------------------------------------------------------------

class TestLicenseNumber:
    def save(self, client, value):
        return client.post("/settings/ccli/", data={"ccli_license_number": value},
                           headers=H, follow_redirects=True)

    def test_staff_save_it(self, db, staff):
        response = self.save(staff, "5731696")
        assert b"CCLI license number saved." in response.data
        assert journey(db).ccli_license_number == "5731696"

    def test_spaces_and_hash_are_tidied(self, db, staff):
        self.save(staff, " #5731-696 ")
        assert journey(db).ccli_license_number == "5731696"

    @pytest.mark.parametrize("bad", ["abc", "12", "12345678901", "5731696a"])
    def test_nonsense_is_refused(self, db, staff, bad):
        response = self.save(staff, bad)
        assert b"4 to 10 digits" in response.data
        assert journey(db).ccli_license_number is None

    def test_blank_removes_it(self, db, staff):
        self.save(staff, "5731696")
        self.save(staff, "")
        assert journey(db).ccli_license_number is None

    def test_leaders_cannot_change_it(self, db, leader):
        self.save(leader, "5731696")
        assert journey(db).ccli_license_number is None

    def test_shown_on_the_songs_page(self, db, staff):
        self.save(staff, "5731696")
        response = staff.get("/services/songs/", headers=H)
        assert b"CCLI License #5731696" in response.data

    def test_songs_page_asks_for_it_when_missing(self, db, staff):
        response = staff.get("/services/songs/", headers=H)
        assert b"Add your CCLI license number in Settings" in response.data

    def test_settings_shows_the_saved_number(self, db, staff):
        self.save(staff, "5731696")
        response = staff.get("/settings/", headers=H)
        assert b'value="5731696"' in response.data

    def test_one_church_number_never_shows_on_another(self, db, staff):
        self.save(staff, "5731696")
        others = db.session.scalars(db.select(Church).where(Church.slug != "journey")).all()
        assert others and all(c.ccli_license_number is None for c in others)


class TestSongSelectLinks:
    def test_library_links_each_song_to_songselect(self, db, leader):
        upload(leader, ("b.txt", TXT.encode()))
        response = leader.get("/services/songs/", headers=H)
        assert b"https://songselect.ccli.com/songs/7115744" in response.data

    def test_no_link_without_a_number(self, db):
        assert Song(church_id=1, title="Untitled").songselect_url is None
