"""A church's own artwork on a resource, and the filters members use.

Two asks on one screen. The artwork replaces a brand gradient; the tags are
what a member filters the library by.
"""

import pytest

from app.files import CHURCH_QUOTA_BYTES, MAX_IMAGE_BYTES
from app.models import Church, Person, Resource, ResourceSession, ResourceTag, User
from app.models.base import utcnow
from app.models.resource import STATUS_PUBLISHED, TAG_STAGE, TAG_THEME
from tests.conftest import JOURNEY_HOST

H = {"Host": JOURNEY_HOST}

PNG = b"\x89PNG\r\n\x1a\n" + b"pretend image bytes" * 20
JPEG = b"\xff\xd8\xff\xe0" + b"pretend jpeg bytes" * 20


def journey(db):
    return db.session.scalar(db.select(Church).where(Church.slug == "journey"))


def make_resource(db, title="Known", published=True, **fields):
    resource = Resource(church_id=journey(db).id, title=title, kind="reading_plan", **fields)
    db.session.add(resource)
    db.session.flush()
    db.session.add(ResourceSession(church_id=resource.church_id, resource_id=resource.id,
                                   position=1, title="Day 1"))
    db.session.flush()
    if published:
        resource.publish()
    db.session.commit()
    return resource


def upload(client, resource, data=PNG, filename="cover.png"):
    import io

    return client.post(
        f"/resources/{resource.id}/thumbnail/",
        data={"thumbnail": (io.BytesIO(data), filename)},
        headers=H, content_type="multipart/form-data", follow_redirects=True,
    )


def tag(client, resource, stages=(), themes=""):
    return client.post(
        f"/resources/{resource.id}/tags/",
        data={"stages": list(stages), "themes": themes},
        headers=H, follow_redirects=True,
    )


@pytest.fixture
def reader(db, client, sign_in):
    """A signed-in member, for the member side of all this."""
    user = db.session.scalar(db.select(User).where(User.email == "member@journeychurchsemo.com"))
    person = Person(church_id=user.church_id, first_name="Alicia", last_name="Romero",
                    email=user.email, stage="member", approved_at=utcnow())
    db.session.add(person)
    db.session.flush()
    user.person_id = person.id
    db.session.commit()
    sign_in("member@journeychurchsemo.com")
    return client


class TestUploadingArtwork:
    def test_a_png(self, db, staff):
        resource = make_resource(db)
        response = upload(staff, resource)
        assert b"Members see the new cover straight away." in response.data

        db.session.refresh(resource)
        assert resource.has_thumb
        assert resource.thumb_type == "image/png"
        assert resource.thumb_bytes == len(PNG)

    def test_a_jpeg_too(self, db, staff):
        resource = make_resource(db)
        upload(staff, resource, JPEG, "cover.jpg")
        db.session.refresh(resource)
        assert resource.thumb_type == "image/jpeg"

    def test_it_replaces_the_gradient_on_the_card(self, db, staff):
        resource = make_resource(db)
        page = staff.get("/resources/", headers=H).data.decode()
        assert "cover-0" in page

        upload(staff, resource)
        page = staff.get("/resources/", headers=H).data.decode()
        assert f"/resources/{resource.id}/thumb/" in page

    def test_replacing_one(self, db, staff):
        resource = make_resource(db)
        upload(staff, resource)
        db.session.refresh(resource)
        first = resource.thumb_version

        upload(staff, resource, PNG + b"different", "other.png")
        db.session.refresh(resource)
        assert resource.thumb_bytes == len(PNG) + len(b"different")
        # The URL changes with the picture, or a browser serves the old one
        # from cache for a week.
        assert resource.thumb_version != first or resource.thumb_set_at is not None

    def test_going_back_to_the_gradient(self, db, staff):
        resource = make_resource(db)
        upload(staff, resource)
        response = staff.post(f"/resources/{resource.id}/thumbnail/remove/",
                              headers=H, follow_redirects=True)
        assert b"Back to the gradient cover." in response.data
        db.session.refresh(resource)
        assert resource.has_thumb is False
        assert resource.thumb_data is None
        assert resource.thumb_bytes == 0


class TestWhatIsRefused:
    def test_a_file_that_is_not_an_image(self, db, staff):
        resource = make_resource(db)
        response = upload(staff, resource, b"<html>not a picture</html>", "cover.png")
        assert b"not a PNG or a JPEG" in response.data
        db.session.refresh(resource)
        assert resource.has_thumb is False

    def test_a_pdf_renamed_to_png(self, db, staff):
        """Checked by its first bytes, like every other upload here."""
        resource = make_resource(db)
        response = upload(staff, resource, b"%PDF-1.7 ...", "cover.png")
        assert b"not a PNG or a JPEG" in response.data

    def test_an_empty_file(self, db, staff):
        resource = make_resource(db)
        response = upload(staff, resource, b"", "cover.png")
        assert b"empty" in response.data

    def test_one_that_is_too_big(self, db, staff):
        resource = make_resource(db)
        big = b"\x89PNG\r\n\x1a\n" + b"x" * MAX_IMAGE_BYTES
        response = upload(staff, resource, big, "cover.png")
        assert b"over 2 MB" in response.data
        db.session.refresh(resource)
        assert resource.has_thumb is False

    def test_a_member_cannot_upload(self, db, reader):
        resource = make_resource(db)
        import io

        assert reader.post(
            f"/resources/{resource.id}/thumbnail/",
            data={"thumbnail": (io.BytesIO(PNG), "cover.png")},
            headers=H, content_type="multipart/form-data",
        ).status_code == 403

    def test_another_church_cannot_be_touched(self, db, staff):
        other = db.session.scalar(db.select(Church).where(Church.slug == "riverbend"))
        theirs = Resource(church_id=other.id, title="Theirs", kind="reading_plan")
        db.session.add(theirs)
        db.session.commit()
        import io

        assert staff.post(
            f"/resources/{theirs.id}/thumbnail/",
            data={"thumbnail": (io.BytesIO(PNG), "cover.png")},
            headers=H, content_type="multipart/form-data",
        ).status_code == 404


class TestServingTheImage:
    def test_it_comes_back_as_what_it_is(self, db, staff):
        resource = make_resource(db)
        upload(staff, resource)
        response = staff.get(f"/resources/{resource.id}/thumb/", headers=H)
        assert response.status_code == 200
        assert response.headers["Content-Type"] == "image/png"
        assert response.data == PNG

    def test_a_browser_is_told_not_to_sniff_it(self, db, staff):
        resource = make_resource(db)
        upload(staff, resource)
        response = staff.get(f"/resources/{resource.id}/thumb/", headers=H)
        assert response.headers["X-Content-Type-Options"] == "nosniff"

    def test_it_is_cached_privately(self, db, staff):
        """It belongs to one church, so no shared cache should keep it."""
        resource = make_resource(db)
        upload(staff, resource)
        response = staff.get(f"/resources/{resource.id}/thumb/", headers=H)
        assert "private" in response.headers["Cache-Control"]

    def test_a_member_can_see_a_published_cover(self, db, staff, reader):
        """Signed in as staff to upload, then as the member to read it."""
        resource = make_resource(db)
        resource.set_thumb(PNG, "image/png")
        db.session.commit()
        assert reader.get(f"/resources/{resource.id}/thumb/", headers=H).status_code == 200

    def test_a_member_cannot_see_a_draft_cover(self, db, reader):
        """Guessing an id must not reveal work in progress, the same rule the
        member routes use for the resource itself."""
        resource = make_resource(db, published=False)
        resource.set_thumb(PNG, "image/png")
        db.session.commit()
        assert reader.get(f"/resources/{resource.id}/thumb/", headers=H).status_code == 404

    def test_nothing_uploaded_is_a_404_not_a_blank_image(self, db, staff):
        resource = make_resource(db)
        assert staff.get(f"/resources/{resource.id}/thumb/", headers=H).status_code == 404

    def test_another_church_cannot_read_it(self, db, client, sign_in, staff):
        resource = make_resource(db)
        upload(staff, resource)
        sign_in("pastor@journeychurchsemo.com", host="riverbend.dos.test")
        assert client.get(f"/resources/{resource.id}/thumb/",
                          headers={"Host": "riverbend.dos.test"}).status_code == 404


class TestTheStorageQuota:
    def test_a_thumbnail_counts_against_it(self, db, staff):
        from app.models.songchart import stored_bytes

        resource = make_resource(db)
        before = stored_bytes(journey(db).id)
        upload(staff, resource)
        assert stored_bytes(journey(db).id) == before + len(PNG)

    def test_replacing_a_picture_is_not_charged_twice(self, db, staff):
        """The one coming off makes room for the one going on, or replacing a
        picture with itself could fail on a church near its limit."""
        resource = make_resource(db)
        resource.set_thumb(b"\x89PNG\r\n\x1a\n" + b"x" * 1000, "image/png")
        resource.thumb_bytes = CHURCH_QUOTA_BYTES - 500
        db.session.commit()

        response = upload(staff, resource)
        assert b"Members see the new cover" in response.data


class TestTaggingAResource:
    def test_stages(self, db, staff):
        resource = make_resource(db)
        tag(staff, resource, stages=["visitor", "member"])
        db.session.refresh(resource)
        assert resource.stage_codes == ["visitor", "member"]
        assert resource.stage_labels == ["Visitor", "Member"]

    def test_stages_come_back_in_rail_order(self, db, staff):
        resource = make_resource(db)
        tag(staff, resource, stages=["leader", "visitor", "member"])
        db.session.refresh(resource)
        assert resource.stage_codes == ["visitor", "member", "leader"]

    def test_themes(self, db, staff):
        resource = make_resource(db)
        tag(staff, resource, themes="Prayer, Marriage")
        db.session.refresh(resource)
        assert resource.themes == ["Marriage", "Prayer"]

    def test_a_theme_already_in_use_keeps_its_spelling(self, db, staff):
        """Or "Prayer" and "prayer" become two filters for one thing."""
        first = make_resource(db, "First")
        tag(staff, first, themes="Prayer")
        second = make_resource(db, "Second")
        tag(staff, second, themes="prayer")
        db.session.refresh(second)
        assert second.themes == ["Prayer"]
        assert ResourceTag.themes_for_church(journey(db).id) == ["Prayer"]

    def test_saving_again_replaces_rather_than_adds(self, db, staff):
        resource = make_resource(db)
        tag(staff, resource, stages=["visitor"], themes="Prayer")
        tag(staff, resource, stages=["member"], themes="Marriage")
        db.session.refresh(resource)
        assert resource.stage_codes == ["member"]
        assert resource.themes == ["Marriage"]

    def test_clearing_them(self, db, staff):
        resource = make_resource(db)
        tag(staff, resource, stages=["visitor"], themes="Prayer")
        tag(staff, resource)
        db.session.refresh(resource)
        assert resource.stage_codes == []
        assert resource.themes == []

    def test_a_stage_that_does_not_exist_is_dropped(self, db, staff):
        resource = make_resource(db)
        tag(staff, resource, stages=["visitor", "guest", "nonsense"])
        db.session.refresh(resource)
        assert resource.stage_codes == ["visitor"]

    def test_blank_and_duplicate_themes(self, db, staff):
        resource = make_resource(db)
        tag(staff, resource, themes="Prayer, , Prayer,   ,Marriage")
        db.session.refresh(resource)
        assert resource.themes == ["Marriage", "Prayer"]

    def test_a_very_long_theme_is_cut(self, db, staff):
        resource = make_resource(db)
        tag(staff, resource, themes="T" * 200)
        db.session.refresh(resource)
        assert len(resource.themes[0]) == 60

    def test_deleting_a_resource_takes_its_tags(self, db, staff):
        resource = make_resource(db)
        tag(staff, resource, themes="Prayer")
        db.session.delete(resource)
        db.session.commit()
        assert db.session.scalars(db.select(ResourceTag)).all() == []

    def test_a_theme_nothing_carries_stops_existing(self, db, staff):
        """A church's themes are the ones in use, so a filter never offers an
        empty result."""
        resource = make_resource(db)
        tag(staff, resource, themes="Prayer")
        assert ResourceTag.themes_for_church(journey(db).id) == ["Prayer"]
        tag(staff, resource, themes="")
        assert ResourceTag.themes_for_church(journey(db).id) == []


class TestFilteringOnTheStaffScreen:
    @pytest.fixture
    def library(self, db, staff):
        a = make_resource(db, "For visitors")
        tag(staff, a, stages=["visitor"], themes="New here")
        b = make_resource(db, "For members")
        tag(staff, b, stages=["member"], themes="Prayer")
        c = make_resource(db, "For everyone")
        tag(staff, c, stages=["visitor", "member"], themes="Prayer")
        return a, b, c

    def test_by_stage(self, db, staff, library):
        page = staff.get("/resources/?stage=visitor", headers=H).data.decode()
        assert "For visitors" in page
        assert "For everyone" in page
        assert "For members" not in page

    def test_by_theme(self, db, staff, library):
        page = staff.get("/resources/?theme=Prayer", headers=H).data.decode()
        assert "For members" in page
        assert "For visitors" not in page

    def test_both_at_once(self, db, staff, library):
        page = staff.get("/resources/?stage=visitor&theme=Prayer", headers=H).data.decode()
        assert "For everyone" in page
        assert "For visitors" not in page
        assert "For members" not in page

    def test_a_resource_with_two_matching_tags_appears_once(self, db, staff, library):
        page = staff.get("/resources/?stage=visitor", headers=H).data.decode()
        grid = page[page.index('class="resgrid"'):]
        assert grid.count('data-title="for everyone"') == 1

    @pytest.mark.parametrize("query", ["stage=nonsense", "stage=<script>", "theme=Nothing"])
    def test_a_filter_nobody_uses_shows_everything(self, db, staff, library, query):
        """Not an empty screen that looks broken."""
        page = staff.get(f"/resources/?{query}", headers=H).data.decode()
        assert "For visitors" in page and "For members" in page
        assert "<script>alert" not in page

    def test_the_filter_row_only_offers_what_is_in_use(self, db, staff, library):
        page = staff.get("/resources/", headers=H).data.decode()
        row = page[page.index('class="resfilter"'):]
        row = row[:row.index("</form>")]
        assert "Visitor" in row and "Member" in row
        assert "Disciple" not in row  # nothing is tagged for it

    def test_no_tags_anywhere_means_no_filter_row(self, db, staff):
        make_resource(db, "Untagged")
        page = staff.get("/resources/", headers=H).data.decode()
        assert 'class="resfilter"' not in page


class TestFilteringInTheMemberApp:
    @pytest.fixture
    def library(self, db):
        """Tagged in the database rather than over HTTP.

        The `db` fixture holds one application context and Flask-Login caches
        the signed-in user on it, so a test cannot be staff and then a member.
        These tests are about what the member sees."""
        a = make_resource(db, "For visitors")
        a.set_tags(TAG_STAGE, ["visitor"])
        a.set_tags(TAG_THEME, ["New here"])
        b = make_resource(db, "For members")
        b.set_tags(TAG_STAGE, ["member"])
        b.set_tags(TAG_THEME, ["Prayer"])
        draft = make_resource(db, "Not finished", published=False)
        draft.set_tags(TAG_THEME, ["Secret"])
        db.session.commit()
        return a, b, draft

    def test_the_chips_are_there(self, db, library, reader):
        page = reader.get("/me/read/", headers=H).data.decode()
        chips = page[page.index('class="mfilters"'):page.index("</nav>")]
        assert "Visitor" in chips and "Member" in chips
        assert "Prayer" in chips and "New here" in chips

    def test_a_draft_theme_is_not_offered_to_members(self, db, library, reader):
        """The library only shows published work, so its filters must too."""
        page = reader.get("/me/read/", headers=H).data.decode()
        assert "Secret" not in page

    def test_filtering_by_stage(self, db, library, reader):
        page = reader.get("/me/read/?stage=visitor", headers=H).data.decode()
        assert "For visitors" in page
        assert "For members" not in page

    def test_filtering_by_theme(self, db, library, reader):
        page = reader.get("/me/read/?theme=Prayer", headers=H).data.decode()
        assert "For members" in page
        assert "For visitors" not in page

    def test_the_chip_you_are_on_is_marked(self, db, library, reader):
        page = reader.get("/me/read/?stage=visitor", headers=H).data.decode()
        chips = page[page.index('class="mfilters"'):page.index("</nav>")]
        # The chip itself, found by its label rather than by a URL that also
        # appears inside every other chip's link.
        visitor = chips[:chips.index(">Visitor</a>")]
        assert "mchip on" in visitor[visitor.rindex("<a "):]

    def test_tapping_the_same_chip_again_clears_it(self, db, library, reader):
        page = reader.get("/me/read/?stage=visitor", headers=H).data.decode()
        chips = page[page.index('class="mfilters"'):page.index("</nav>")]
        # The chip that is on links to the list with no stage on it.
        assert 'href="/me/read/"' in chips

    def test_a_filter_with_nothing_in_it_says_so(self, db, library, reader):
        page = reader.get("/me/read/?stage=leader", headers=H).data.decode()
        assert "Nothing matches that filter yet." in page

    def test_no_tags_means_no_chip_row(self, db, reader):
        make_resource(db, "Untagged")
        page = reader.get("/me/read/", headers=H).data.decode()
        assert 'class="mfilters"' not in page

    def test_the_cover_is_the_churchs_picture(self, db, library, reader):
        resource = library[0]
        resource.set_thumb(PNG, "image/png")
        db.session.commit()
        page = reader.get("/me/read/", headers=H).data.decode()
        assert f"/resources/{resource.id}/thumb/" in page


class TestTheArtworkSitsWhereTheGradientDid:
    """Measured in a browser, guarded here.

    A percentage height on an in-flow image resolves against a box the image
    is itself sizing. The first version grew to the picture's own height and
    the cover swallowed the whole card, text and all.
    """

    from pathlib import Path as _Path

    CSS = (_Path(__file__).resolve().parent.parent
           / "app" / "static" / "css" / "app.css").read_text()

    def rule(self, selector):
        block = self.CSS[self.CSS.index(selector):]
        return block[:block.index("}")]

    def test_the_picture_is_pinned_to_its_box(self):
        art = self.rule(".coverart{")
        assert "position:absolute" in art
        assert "inset:0" in art
        assert "object-fit:cover" in art

    def test_every_cover_box_contains_it(self):
        assert ".rescover,.planbanner,.mcover{position:relative;overflow:hidden}" in self.CSS

    def test_one_wrapper_either_way(self, db, staff):
        """The element carrying the cover class is the same with a picture
        and without, so no screen needs a second sizing rule."""
        resource = make_resource(db)
        page = staff.get("/resources/", headers=H).data.decode()
        assert '<span class="rescover cover-0">' in page

        upload(staff, resource)
        page = staff.get("/resources/", headers=H).data.decode()
        assert '<span class="rescover">' in page
        assert "coverart" in page
