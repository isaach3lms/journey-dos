"""SongSelect PDFs: read for the song's details, kept as the band's chart."""

from datetime import timedelta
from io import BytesIO

import pytest

from app import songselect
from app.models import (
    Church, Person, Service, ServiceAssignment, ServiceItem, Song, SongChart,
    Team, TeamFile, User,
)
from app.models.base import utcnow
from app.models.service import ACCEPTED, DECLINED, INVITED
from app.models.songchart import stored_bytes
from tests.conftest import JOURNEY_HOST
from tests.pdfmaker import make_pdf, songselect_chart

H = {"Host": JOURNEY_HOST}
CHART = songselect_chart()  # 10,000 Reasons, G, 73 bpm, CCLI 6016351


def journey(db):
    return db.session.scalar(db.select(Church).where(Church.slug == "journey"))


def songs(db):
    return db.session.scalars(
        db.select(Song).where(Song.church_id == journey(db).id).order_by(Song.title)
    ).all()


def charts(db):
    return db.session.scalars(
        db.select(SongChart).where(SongChart.church_id == journey(db).id)
    ).all()


def import_files(client, *files):
    return client.post(
        "/services/songs/import/", headers=H, content_type="multipart/form-data",
        data={"files": [(BytesIO(body), name) for name, body in files]},
        follow_redirects=True,
    )


def attach(client, song_id, data=CHART, filename="chart.pdf", title=""):
    return client.post(
        f"/services/songs/{song_id}/charts/", headers=H,
        content_type="multipart/form-data",
        data={"title": title, "file": (BytesIO(data), filename)},
        follow_redirects=True,
    )


def make_song_with_chart(db, title="Way Maker", data=CHART):
    church = journey(db)
    song = Song(church_id=church.id, title=title, ccli_number="7115744")
    db.session.add(song)
    db.session.flush()
    chart = SongChart(church_id=church.id, song_id=song.id, title="Chord chart",
                      filename="way-maker.pdf", size_bytes=len(data), data=data)
    db.session.add(chart)
    db.session.commit()
    return song, chart


def make_service(db, song, published=True, starts_in=timedelta(days=3)):
    church = journey(db)
    service = Service(church_id=church.id, name="Sunday", starts_at=utcnow() + starts_in)
    db.session.add(service)
    db.session.flush()
    db.session.add(ServiceItem(church_id=church.id, service_id=service.id, position=1,
                               kind="song", title=song.title, song_id=song.id))
    if published:
        service.publish()
    db.session.commit()
    return service


def schedule(db, service, person, status=INVITED):
    db.session.add(ServiceAssignment(church_id=service.church_id, service_id=service.id,
                                     person_id=person.id, status=status))
    db.session.commit()


@pytest.fixture
def drummer(db):
    """The member login, linked to a roster record. Not signed in: the `db`
    fixture holds one app context, so a test can only sign in once."""
    user = db.session.scalar(db.select(User).where(User.email == "member@journeychurchsemo.com"))
    person = Person(church_id=user.church_id, first_name="Alicia", last_name="Romero",
                    email=user.email, approved_at=utcnow())
    db.session.add(person)
    db.session.flush()
    user.person_id = person.id
    db.session.commit()
    return person


# ---------------------------------------------------------------------------
# Reading a SongSelect PDF
# ---------------------------------------------------------------------------

class TestReadingPdfs:
    def test_the_details_on_a_chart(self):
        meta = songselect.parse("10000-reasons-G.pdf", CHART)
        assert meta.title == "10,000 Reasons (Bless The Lord)"
        assert meta.author == "Jonas Myrin, Matt Redman"
        assert meta.ccli_number == "6016351"  # the song, not "CCLI License #"
        assert meta.default_key == "G"
        assert meta.tempo_bpm == 73

    def test_writers_listed_without_a_prefix(self):
        pdf = songselect_chart(title="Goodness Of God", writers="Ed Cash, Jason Ingram",
                               header="Key - Ab | Tempo - 63", ccli="7117726")
        meta = songselect.parse("goodness.pdf", pdf)
        assert meta.author == "Ed Cash, Jason Ingram"
        assert meta.default_key == "Ab"

    def test_a_line_that_is_not_names_is_not_taken_as_writers(self):
        pdf = songselect_chart(writers="Arranged for band 2024", ccli="7117726")
        assert songselect.parse("a.pdf", pdf).author is None

    def test_a_multi_page_chart(self):
        meta = songselect.parse("a.pdf", songselect_chart(pages=4))
        assert meta.ccli_number == "6016351"

    def test_no_ccli_number_is_not_a_songselect_chart(self):
        with pytest.raises(songselect.NotASong) as refused:
            songselect.parse("a.pdf", songselect_chart(ccli=""))
        assert refused.value.reason == "pdf_no_ccli"

    def test_a_pdf_with_no_text_is_unreadable_not_an_error(self):
        blank = make_pdf([[]])
        with pytest.raises(songselect.NotASong) as refused:
            songselect.parse("scan.pdf", blank)
        assert refused.value.reason == "pdf_unreadable"

    def test_a_corrupt_pdf_is_unreadable_not_an_error(self):
        with pytest.raises(songselect.NotASong) as refused:
            songselect.parse("broken.pdf", b"%PDF-1.7\n\x00\xff garbage")
        assert refused.value.reason == "pdf_unreadable"

    def test_too_big(self):
        with pytest.raises(songselect.NotASong) as refused:
            songselect.parse("a.pdf", CHART + b"0" * songselect.MAX_PDF_BYTES)
        assert refused.value.reason == "too_big"


# ---------------------------------------------------------------------------
# Importing a PDF
# ---------------------------------------------------------------------------

class TestImportingPdfs:
    def test_adds_the_song_and_keeps_the_chart(self, db, leader):
        response = import_files(leader, ("10000-reasons-G.pdf", CHART))
        assert b"1 added to your songs." in response.data
        assert b"Charts attached: 1." in response.data
        [song] = songs(db)
        assert (song.title, song.default_key, song.ccli_number) == (
            "10,000 Reasons (Bless The Lord)", "G", "6016351")
        [chart] = charts(db)
        assert chart.song_id == song.id
        assert chart.data == CHART  # the file itself, byte for byte
        assert chart.title == "Chart in G"

    def test_lyrics_never_become_text_on_the_song(self, db, leader):
        import_files(leader, ("a.pdf", CHART))
        [song] = songs(db)
        stored = " ".join(str(getattr(song, c.key)) for c in Song.__table__.columns)
        assert "soul" not in stored

    def test_the_same_pdf_twice_is_one_song_and_one_chart(self, db, leader):
        import_files(leader, ("a.pdf", CHART), ("a.pdf", CHART))
        import_files(leader, ("a.pdf", CHART))
        assert len(songs(db)) == 1
        assert len(charts(db)) == 1

    def test_a_second_arrangement_is_a_second_chart(self, db, leader):
        lead = songselect_chart(header="Key - A | Tempo - 73")
        import_files(leader, ("chord-chart-G.pdf", CHART), ("lead-sheet-A.pdf", lead))
        [song] = songs(db)
        assert sorted(c.title for c in song.charts) == ["Chord chart in G", "Lead sheet in A"]

    def test_a_pdf_joins_the_song_a_text_file_already_made(self, db, leader):
        usr = ("[S A6016351]\nTitle=10,000 Reasons (Bless The Lord)\nAuthor=Jonas Myrin\n").encode()
        import_files(leader, ("a.usr", usr))
        import_files(leader, ("a.pdf", CHART))
        [song] = songs(db)
        assert len(song.charts) == 1
        assert song.default_key == "G"  # filled in from the chart

    def test_a_pdf_that_cannot_be_read_is_not_kept(self, db, leader):
        response = import_files(leader, ("scan.pdf", make_pdf([[]])))
        assert b"attach the PDF on its row" in response.data
        assert charts(db) == []
        assert songs(db) == []

    def test_a_fake_pdf_is_refused(self, db, leader):
        response = import_files(leader, ("chart.pdf", b"<html>hello</html>"))
        assert b"not a SongSelect download" in response.data
        assert charts(db) == []

    def test_the_church_quota_applies(self, db, leader, monkeypatch):
        import app.files
        monkeypatch.setattr(app.files, "CHURCH_QUOTA_BYTES", len(CHART) - 1)
        response = import_files(leader, ("a.pdf", CHART))
        assert b"file space" in response.data
        assert charts(db) == []


# ---------------------------------------------------------------------------
# Attaching by hand, opening, removing
# ---------------------------------------------------------------------------

class TestStaffSide:
    def test_attach_any_pdf_to_a_song(self, db, leader):
        church = journey(db)
        song = Song(church_id=church.id, title="Holy Forever")
        db.session.add(song)
        db.session.commit()
        blank = make_pdf([["My own arrangement"]])
        response = attach(leader, song.id, data=blank, filename="holy.pdf", title="Band chart in C")
        assert b"Chart attached to Holy Forever." in response.data
        [chart] = charts(db)
        assert chart.title == "Band chart in C"

    def test_a_hand_attached_songselect_pdf_is_named_from_its_key(self, db, leader):
        song, _ = make_song_with_chart(db)
        pdf = songselect_chart(header="Key - Bb | Tempo - 70")
        attach(leader, song.id, data=pdf, filename="vocal-sheet.pdf")
        assert sorted(c.title for c in song.charts) == ["Chord chart", "Vocal sheet in Bb"]

    def test_the_song_line_escapes_what_it_shows(self, db, leader):
        church = journey(db)
        db.session.add(Song(church_id=church.id, title="X", author="<script>alert(1)</script>",
                            ccli_number="123456"))
        db.session.commit()
        response = leader.get("/services/songs/", headers=H)
        assert b"<script>alert(1)</script>" not in response.data
        assert b"&lt;script&gt;" in response.data

    def test_the_song_line_renders_as_markup_not_text(self, db, leader):
        church = journey(db)
        db.session.add(Song(church_id=church.id, title="Z", author="A B", default_key="G",
                            ccli_number="7115744"))
        db.session.commit()
        page = leader.get("/services/songs/", headers=H).data
        assert b"&amp;middot;" not in page
        assert b"&lt;a href" not in page
        assert b'<a href="https://songselect.ccli.com/songs/7115744"' in page

    def test_no_stray_separator_without_writers(self, db, leader):
        church = journey(db)
        db.session.add(Song(church_id=church.id, title="Y", default_key="E"))
        db.session.commit()
        response = leader.get("/services/songs/", headers=H).data.decode()
        line = response.split('<span class="pe">', 1)[1].split("</span>", 1)[0].strip()
        assert line.startswith("E")

    def test_attaching_the_same_file_twice(self, db, leader):
        song, _chart = make_song_with_chart(db)
        response = attach(leader, song.id, filename="way-maker.pdf")
        assert b"already on Way Maker" in response.data
        assert len(charts(db)) == 1

    def test_attach_refuses_what_is_not_a_pdf(self, db, leader):
        song, _ = make_song_with_chart(db)
        response = attach(leader, song.id, data=b"<html>", filename="x.pdf")
        assert b"That is not a PDF" in response.data
        assert len(charts(db)) == 1

    def test_songs_page_lists_charts(self, db, leader):
        _song, chart = make_song_with_chart(db)
        response = leader.get("/services/songs/", headers=H)
        assert f"/services/songs/charts/{chart.id}/".encode() in response.data
        assert b"Attach a PDF chart" in response.data

    def test_open_is_always_a_pdf(self, db, leader):
        _song, chart = make_song_with_chart(db)
        response = leader.get(f"/services/songs/charts/{chart.id}/", headers=H)
        assert response.status_code == 200
        assert response.mimetype == "application/pdf"
        assert response.data == CHART
        assert "default-src 'none'" in response.headers["Content-Security-Policy"]

    def test_plan_shows_a_chart_button(self, db, leader):
        song, chart = make_song_with_chart(db)
        service = make_service(db, song)
        response = leader.get(f"/services/{service.id}/", headers=H)
        assert f"/services/songs/charts/{chart.id}/".encode() in response.data

    def test_remove(self, db, leader):
        _song, chart = make_song_with_chart(db)
        response = leader.post(f"/services/songs/charts/{chart.id}/delete/", headers=H,
                               follow_redirects=True)
        assert b"Chord chart removed." in response.data
        assert charts(db) == []

    def test_members_cannot_attach_or_remove(self, db, member):
        song, chart = make_song_with_chart(db)
        assert attach(member, song.id).status_code == 403
        assert member.post(f"/services/songs/charts/{chart.id}/delete/", headers=H).status_code == 403
        assert len(charts(db)) == 1

    def test_another_church_cannot_reach_it(self, db, leader):
        other = db.session.scalar(db.select(Church).where(Church.slug != "journey"))
        song = Song(church_id=other.id, title="Theirs")
        db.session.add(song)
        db.session.flush()
        chart = SongChart(church_id=other.id, song_id=song.id, title="x", filename="x.pdf",
                          size_bytes=len(CHART), data=CHART)
        db.session.add(chart)
        db.session.commit()
        assert leader.get(f"/services/songs/charts/{chart.id}/", headers=H).status_code == 404
        assert attach(leader, song.id).status_code == 404

    def test_quota_is_shared_with_team_files(self, db):
        church = journey(db)
        team = Team(church_id=church.id, name="Kids")
        db.session.add(team)
        db.session.flush()
        db.session.add(TeamFile(church_id=church.id, team_id=team.id, title="t",
                                filename="t.pdf", size_bytes=1000, data=b"%PDF-"))
        db.session.commit()
        make_song_with_chart(db)
        assert stored_bytes(church.id) == 1000 + len(CHART)


# ---------------------------------------------------------------------------
# Who on the member side can open a chart
# ---------------------------------------------------------------------------

class TestVolunteers:
    def open_(self, client, chart):
        return client.get(f"/me/serve/charts/{chart.id}/", headers=H)

    def test_scheduled_on_a_published_service(self, db, client, sign_in, drummer):
        song, chart = make_song_with_chart(db)
        schedule(db, make_service(db, song), drummer)
        sign_in("member@journeychurchsemo.com")
        response = self.open_(client, chart)
        assert response.status_code == 200
        assert response.mimetype == "application/pdf"

    def test_the_serve_tab_links_it(self, db, client, sign_in, drummer):
        song, chart = make_song_with_chart(db)
        schedule(db, make_service(db, song), drummer, status=ACCEPTED)
        sign_in("member@journeychurchsemo.com")
        response = client.get("/me/serve/", headers=H)
        assert f"/me/serve/charts/{chart.id}/".encode() in response.data

    def test_not_while_the_plan_is_a_draft(self, db, client, sign_in, drummer):
        song, chart = make_song_with_chart(db)
        schedule(db, make_service(db, song, published=False), drummer)
        sign_in("member@journeychurchsemo.com")
        assert self.open_(client, chart).status_code == 404
        assert f"/me/serve/charts/{chart.id}/".encode() not in client.get("/me/serve/", headers=H).data

    def test_not_after_declining(self, db, client, sign_in, drummer):
        song, chart = make_song_with_chart(db)
        schedule(db, make_service(db, song), drummer, status=DECLINED)
        sign_in("member@journeychurchsemo.com")
        assert self.open_(client, chart).status_code == 404
        assert f"/me/serve/charts/{chart.id}/".encode() not in client.get("/me/serve/", headers=H).data

    def test_not_when_the_song_is_not_in_their_service(self, db, client, sign_in, drummer):
        song, chart = make_song_with_chart(db)
        other_song = Song(church_id=song.church_id, title="Other")
        db.session.add(other_song)
        db.session.commit()
        schedule(db, make_service(db, other_song), drummer)
        sign_in("member@journeychurchsemo.com")
        assert self.open_(client, chart).status_code == 404

    def test_not_for_members_who_are_not_scheduled(self, db, client, sign_in, drummer):
        song, chart = make_song_with_chart(db)
        make_service(db, song)
        sign_in("member@journeychurchsemo.com")
        assert self.open_(client, chart).status_code == 404

    def test_still_open_on_the_day_of_the_service(self, db, client, sign_in, drummer):
        song, chart = make_song_with_chart(db)
        schedule(db, make_service(db, song, starts_in=-timedelta(hours=2)), drummer)
        sign_in("member@journeychurchsemo.com")
        assert self.open_(client, chart).status_code == 200

    def test_closed_once_the_service_is_well_past(self, db, client, sign_in, drummer):
        song, chart = make_song_with_chart(db)
        schedule(db, make_service(db, song, starts_in=-timedelta(days=3)), drummer)
        sign_in("member@journeychurchsemo.com")
        assert self.open_(client, chart).status_code == 404

    def test_leaders_can_always_open_it(self, db, client, sign_in):
        _song, chart = make_song_with_chart(db)
        sign_in("leader@journeychurchsemo.com")
        assert self.open_(client, chart).status_code == 200
