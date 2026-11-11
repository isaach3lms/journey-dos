"""Team files: a PDF the whole team can open, stored where a deploy cannot wipe it."""

import io

import pytest

from app.files import CHURCH_QUOTA_BYTES, MAX_FILE_BYTES, RefusedFile, check_pdf, safe_filename
from app.models import Church, Person, Team, TeamFile, TeamMembership, User
from app.models.base import utcnow
from tests.conftest import JOURNEY_HOST

H = {"Host": JOURNEY_HOST}
PDF = b"%PDF-1.7\n1 0 obj\n<< >>\nendobj\ntrailer\n%%EOF\n"


def journey(db):
    return db.session.scalar(db.select(Church).where(Church.slug == "journey"))


def upload(client, team_id, data=PDF, filename="curriculum.pdf", title="October curriculum"):
    return client.post(
        f"/services/teams/{team_id}/files/",
        headers=H,
        data={"title": title, "file": (io.BytesIO(data), filename)},
        content_type="multipart/form-data",
        follow_redirects=True,
    )


@pytest.fixture
def kids(db):
    team = Team(church_id=journey(db).id, name="Kids")
    db.session.add(team)
    db.session.commit()
    return team


@pytest.fixture
def staff(client, sign_in):
    sign_in("pastor@journeychurchsemo.com")
    return client


@pytest.fixture
def volunteer(client, sign_in, db, kids):
    user = db.session.scalar(db.select(User).where(User.email == "member@journeychurchsemo.com"))
    p = Person(church_id=user.church_id, first_name="A", last_name="R", email=user.email, approved_at=utcnow())
    db.session.add(p)
    db.session.flush()
    user.person_id = p.id
    db.session.add(TeamMembership(church_id=p.church_id, team_id=kids.id, person_id=p.id))
    db.session.commit()
    sign_in("member@journeychurchsemo.com")
    return client, p


class TestChecks:
    def test_a_pdf_passes(self):
        check_pdf(PDF, "a.pdf")

    def test_contents_decide_not_the_name(self):
        with pytest.raises(RefusedFile) as refused:
            check_pdf(b"<html>not really</html>", "trustme.pdf")
        assert refused.value.reason == "file_not_pdf"

    def test_the_extension_matters_too(self):
        with pytest.raises(RefusedFile):
            check_pdf(PDF, "curriculum.html")

    def test_empty_and_oversized(self):
        with pytest.raises(RefusedFile) as empty:
            check_pdf(b"", "a.pdf")
        assert empty.value.reason == "file_empty"
        with pytest.raises(RefusedFile) as big:
            check_pdf(PDF + b"x" * MAX_FILE_BYTES, "a.pdf")
        assert big.value.reason == "file_too_big"

    def test_the_church_quota(self):
        with pytest.raises(RefusedFile) as full:
            check_pdf(PDF, "a.pdf", used_bytes=CHURCH_QUOTA_BYTES)
        assert full.value.reason == "file_quota"

    @pytest.mark.parametrize("raw,expected", [
        ("kids curriculum.pdf", "kids curriculum.pdf"),
        ("../../etc/passwd", "etc passwd.pdf"),
        ('we"ird<>.pdf', "weird.pdf"),
        ("", "file.pdf"),
        ("notes", "notes.pdf"),
    ])
    def test_filenames_are_rewritten(self, raw, expected):
        assert safe_filename(raw) == expected


class TestStaff:
    def test_upload_and_list(self, staff, db, kids):
        r = upload(staff, kids.id)
        assert b"Everyone on Kids can open it now" in r.data
        record = db.session.scalar(db.select(TeamFile))
        assert (record.title, record.filename, record.team_id) == ("October curriculum", "curriculum.pdf", kids.id)
        assert record.size_bytes == len(PDF) and record.uploaded_by_name == "Pastor Reed"
        page = staff.get("/services/teams/", headers=H).data
        assert b"October curriculum" in page and b"Team files" in page

    def test_the_title_falls_back_to_the_filename(self, staff, db, kids):
        upload(staff, kids.id, title="  ")
        assert db.session.scalar(db.select(TeamFile)).title == "curriculum.pdf"

    def test_a_non_pdf_is_refused(self, staff, db, kids):
        r = upload(staff, kids.id, data=b"<html>hi</html>")
        assert b"not a PDF" in r.data
        assert db.session.scalar(db.select(TeamFile)) is None

    def test_download_is_always_served_as_a_pdf(self, staff, db, kids):
        upload(staff, kids.id)
        record = db.session.scalar(db.select(TeamFile))
        r = staff.get(f"/services/teams/files/{record.id}/", headers=H)
        assert r.status_code == 200
        assert r.mimetype == "application/pdf"
        assert r.data == PDF

    def test_delete(self, staff, db, kids):
        upload(staff, kids.id)
        record = db.session.scalar(db.select(TeamFile))
        staff.post(f"/services/teams/files/{record.id}/delete/", headers=H)
        assert db.session.scalar(db.select(TeamFile)) is None

    def test_deleting_the_team_takes_its_files(self, staff, db, kids):
        upload(staff, kids.id)
        db.session.delete(kids)
        db.session.commit()
        assert db.session.scalar(db.select(TeamFile)) is None

    def test_another_churchs_file_is_a_404(self, staff, db):
        other = db.session.scalar(db.select(Church).where(Church.slug == "riverbend"))
        team = Team(church_id=other.id, name="Theirs")
        db.session.add(team)
        db.session.flush()
        record = TeamFile(church_id=other.id, team_id=team.id, title="Theirs", filename="t.pdf",
                          size_bytes=len(PDF), data=PDF)
        db.session.add(record)
        db.session.commit()
        assert staff.get(f"/services/teams/files/{record.id}/", headers=H).status_code == 404
        assert staff.post(f"/services/teams/files/{record.id}/delete/", headers=H).status_code == 404


class TestVolunteers:
    def test_the_team_sees_its_files_on_serve(self, volunteer, staff, db, kids):
        client, person = volunteer
        record = TeamFile(church_id=kids.church_id, team_id=kids.id, title="October curriculum",
                          filename="c.pdf", size_bytes=len(PDF), data=PDF)
        db.session.add(record)
        db.session.commit()
        page = client.get("/me/serve/", headers=H).data
        assert b"October curriculum" in page and b"Kids" in page
        assert client.get(f"/me/serve/files/{record.id}/", headers=H).mimetype == "application/pdf"

    def test_somebody_not_on_the_team_gets_a_404(self, client, sign_in, db, kids):
        record = TeamFile(church_id=kids.church_id, team_id=kids.id, title="Private",
                          filename="c.pdf", size_bytes=len(PDF), data=PDF)
        user = db.session.scalar(db.select(User).where(User.email == "member@journeychurchsemo.com"))
        p = Person(church_id=user.church_id, first_name="B", last_name="C", email=user.email, approved_at=utcnow())
        db.session.add_all([record, p])
        db.session.flush()
        user.person_id = p.id
        db.session.commit()
        sign_in("member@journeychurchsemo.com")
        assert client.get("/me/serve/", headers=H).data.count(b"Private") == 0
        assert client.get(f"/me/serve/files/{record.id}/", headers=H).status_code == 404

    def test_members_cannot_upload(self, volunteer, db, kids):
        client, _ = volunteer
        r = upload(client, kids.id)
        assert db.session.scalar(db.select(TeamFile)) is None
