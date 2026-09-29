"""Dragging the running order instead of clicking Up and Down.

A plan is a list somebody rearranges on a Thursday evening with the band
waiting. Moving the sermon three places used to be six clicks and six page
loads. It is now one drag.

Two things are load bearing here and both are tested rather than trusted:

1. **The whole order is posted, not a from-and-to pair.** Two people editing
   one plan is the ordinary case in a church office, and a move described as
   "item 4 goes to slot 2" is wrong the moment the other person deleted item
   3. A full list either matches the plan exactly or is refused.
2. **The Up and Down buttons stay in the markup.** They are hidden by the
   script once it has run. A plan that cannot be reordered without JavaScript
   is worse than two extra buttons, and the drag handle is the thing that has
   to earn its place, not the fallback.
"""

import pytest

from app.models import Church, Service, ServiceItem
from app.models.base import utcnow
from tests.conftest import JOURNEY_HOST

H = {"Host": JOURNEY_HOST}


@pytest.fixture
def journey(db):
    return db.session.scalar(db.select(Church).where(Church.slug == "journey"))


@pytest.fixture
def staff(client, sign_in):
    sign_in("pastor@journeychurchsemo.com")
    return client


def a_service(db, journey, titles=("A", "B", "C", "D")):
    from datetime import timedelta

    service = Service(church_id=journey.id, name="Sunday",
                      starts_at=utcnow() + timedelta(days=4))
    db.session.add(service)
    db.session.flush()
    for index, title in enumerate(titles, start=1):
        db.session.add(ServiceItem(
            church_id=journey.id, service_id=service.id, position=index,
            kind="element", title=title, minutes=5,
        ))
    db.session.commit()
    db.session.refresh(service)
    return service


def titles(db, service):
    db.session.refresh(service)
    return [item.title for item in service.items]


def ids_for(db, service, wanted):
    db.session.refresh(service)
    by_title = {item.title: item.id for item in service.items}
    return [by_title[title] for title in wanted]


class TestTheModelTakesAWholeOrder:
    def test_it_rearranges_to_the_order_given(self, db, journey):
        service = a_service(db, journey)
        assert service.reorder_items(ids_for(db, service, ["D", "A", "C", "B"]))
        db.session.commit()
        assert titles(db, service) == ["D", "A", "C", "B"]

    def test_positions_read_one_to_n_afterwards(self, db, journey):
        """No gaps, so the next item added lands at the end where it should."""
        service = a_service(db, journey)
        service.reorder_items(ids_for(db, service, ["C", "B", "D", "A"]))
        db.session.commit()
        db.session.refresh(service)
        assert [item.position for item in service.items] == [1, 2, 3, 4]

    def test_the_unique_constraint_is_not_tripped(self, db, journey):
        """Reversing a plan makes every item want a number another one holds."""
        service = a_service(db, journey)
        assert service.reorder_items(ids_for(db, service, ["D", "C", "B", "A"]))
        db.session.commit()
        assert titles(db, service) == ["D", "C", "B", "A"]

    def test_the_same_order_again_changes_nothing(self, db, journey):
        service = a_service(db, journey)
        assert service.reorder_items(ids_for(db, service, ["A", "B", "C", "D"]))
        db.session.commit()
        assert titles(db, service) == ["A", "B", "C", "D"]

    def test_a_short_list_is_refused(self, db, journey):
        """Anything missing would be silently dropped off the end."""
        service = a_service(db, journey)
        assert service.reorder_items(ids_for(db, service, ["B", "A"])) is False
        assert titles(db, service) == ["A", "B", "C", "D"]

    def test_a_list_naming_something_else_is_refused(self, db, journey):
        service = a_service(db, journey)
        other = a_service(db, journey, titles=("X", "Y", "Z", "W"))
        borrowed = ids_for(db, service, ["A", "B", "C"])
        borrowed.append(other.items[0].id)
        assert service.reorder_items(borrowed) is False
        assert titles(db, service) == ["A", "B", "C", "D"]

    def test_a_duplicated_id_is_refused(self, db, journey):
        """Right length, wrong set. Two slots for one item and one item lost."""
        service = a_service(db, journey)
        wanted = ids_for(db, service, ["A", "B", "C"])
        wanted.append(wanted[0])
        assert service.reorder_items(wanted) is False
        assert titles(db, service) == ["A", "B", "C", "D"]

    def test_an_empty_list_is_refused(self, db, journey):
        service = a_service(db, journey)
        assert service.reorder_items([]) is False
        assert titles(db, service) == ["A", "B", "C", "D"]


class TestTheRouteThatSavesADrag:
    def url(self, service):
        return f"/services/{service.id}/items/order/"

    def test_it_saves_the_posted_order(self, db, journey, staff):
        service = a_service(db, journey)
        wanted = ids_for(db, service, ["C", "A", "D", "B"])
        staff.post(self.url(service),
                   data={"order": ",".join(str(i) for i in wanted)}, headers=H)
        assert titles(db, service) == ["C", "A", "D", "B"]

    def test_it_says_so(self, db, journey, staff):
        service = a_service(db, journey)
        wanted = ids_for(db, service, ["B", "A", "C", "D"])
        page = staff.post(self.url(service),
                          data={"order": ",".join(str(i) for i in wanted)},
                          headers=H, follow_redirects=True).data.decode()
        assert "Moved." in page

    def test_it_lands_back_on_the_plan(self, db, journey, staff):
        service = a_service(db, journey)
        wanted = ids_for(db, service, ["B", "A", "C", "D"])
        r = staff.post(self.url(service),
                       data={"order": ",".join(str(i) for i in wanted)}, headers=H)
        assert r.status_code == 302
        assert r.headers["Location"].endswith(f"/services/{service.id}/")

    def test_a_partial_order_changes_nothing(self, db, journey, staff):
        service = a_service(db, journey)
        wanted = ids_for(db, service, ["C", "A"])
        staff.post(self.url(service),
                   data={"order": ",".join(str(i) for i in wanted)}, headers=H)
        assert titles(db, service) == ["A", "B", "C", "D"]

    def test_rubbish_changes_nothing(self, db, journey, staff):
        service = a_service(db, journey)
        staff.post(self.url(service), data={"order": "four,three"}, headers=H)
        assert titles(db, service) == ["A", "B", "C", "D"]

    def test_nothing_at_all_changes_nothing(self, db, journey, staff):
        service = a_service(db, journey)
        staff.post(self.url(service), data={}, headers=H)
        assert titles(db, service) == ["A", "B", "C", "D"]

    def test_another_church_cannot_reach_it(self, db, journey, staff):
        """The ids are guessable integers, so the tenant check is the guard."""
        other = db.session.scalar(db.select(Church).where(Church.slug != "journey"))
        service = a_service(db, journey)
        r = staff.post(f"/services/{service.id}/items/order/",
                       data={"order": "1"},
                       headers={"Host": f"{other.slug}.localhost"})
        assert r.status_code in (302, 404)
        assert titles(db, service) == ["A", "B", "C", "D"]

    def test_a_missing_service_is_a_404(self, db, journey, staff):
        r = staff.post("/services/999999/items/order/",
                       data={"order": "1"}, headers=H)
        assert r.status_code == 404

    def test_signed_out_cannot_reorder(self, db, journey, client):
        service = a_service(db, journey)
        wanted = ids_for(db, service, ["D", "C", "B", "A"])
        client.post(self.url(service),
                    data={"order": ",".join(str(i) for i in wanted)}, headers=H)
        assert titles(db, service) == ["A", "B", "C", "D"]


def plan_page(staff, service):
    return staff.get(f"/services/{service.id}/", headers=H).data.decode()


class TestTheScreen:
    def test_every_row_carries_its_id(self, db, journey, staff):
        service = a_service(db, journey)
        page = plan_page(staff, service)
        for item in service.items:
            assert f'data-item-id="{item.id}"' in page

    def test_each_row_has_a_handle(self, db, journey, staff):
        service = a_service(db, journey)
        page = plan_page(staff, service)
        assert page.count('class="grip"') == len(service.items)

    def test_the_handle_says_what_it_moves(self, db, journey, staff):
        """A screen reader should not announce four identical handles."""
        service = a_service(db, journey)
        page = plan_page(staff, service)
        assert 'aria-label="Move A"' in page
        assert 'aria-label="Move D"' in page

    def test_the_handle_is_a_button(self, db, journey, staff):
        """So it can be tabbed to and driven with the arrow keys."""
        service = a_service(db, journey)
        page = plan_page(staff, service)
        start = page.index('class="grip"')
        assert "<button" in page[start - 120:start]

    def test_the_order_form_is_there(self, db, journey, staff):
        service = a_service(db, journey)
        page = plan_page(staff, service)
        assert "data-order-form" in page
        assert "data-order-field" in page
        assert f"/services/{service.id}/items/order/" in page

    def test_the_order_form_is_outside_the_list(self, db, journey, staff):
        """Rows carry forms of their own and forms cannot nest."""
        service = a_service(db, journey)
        page = plan_page(staff, service)
        assert page.index("data-order-form") < page.index("data-runsheet")

    def test_the_order_form_is_protected(self, db, journey, staff):
        service = a_service(db, journey)
        page = plan_page(staff, service)
        block = page[page.index("data-order-form"):]
        assert "csrf_token" in block[:block.index("</form>")]

    def test_remove_is_still_on_every_row(self, db, journey, staff):
        service = a_service(db, journey)
        page = plan_page(staff, service)
        assert page.count("/delete/") >= len(service.items)

    def test_the_up_and_down_buttons_are_still_in_the_markup(self, db, journey, staff):
        """The script hides them. Without it they are the only way to reorder."""
        service = a_service(db, journey)
        page = plan_page(staff, service)
        assert "nojsmove" in page
        assert ">Up<" in page and ">Down<" in page

    def test_the_hint_starts_hidden(self, db, journey, staff):
        """It describes dragging, which does not work until the script runs."""
        service = a_service(db, journey)
        page = plan_page(staff, service)
        block = page[page.index("data-drag-hint"):]
        assert "hidden" in block[:block.index(">")]

    def test_an_empty_plan_has_no_handles(self, db, journey, staff):
        from datetime import timedelta

        service = Service(church_id=journey.id, name="Sunday",
                          starts_at=utcnow() + timedelta(days=4))
        db.session.add(service)
        db.session.commit()
        page = plan_page(staff, service)
        assert 'class="grip"' not in page
        assert '<ol class="runsheet"' not in page
        assert f"/services/{service.id}/items/order/" not in page


class TestTheScript:
    from pathlib import Path as _Path

    TEMPLATE = (_Path(__file__).resolve().parent.parent / "app" / "templates"
                / "services" / "plan.html").read_text()

    def test_it_uses_pointer_events(self):
        """The HTML5 drag API does not fire on touch, and this is used on a
        phone on Sunday morning."""
        assert "pointerdown" in self.TEMPLATE
        assert "pointermove" in self.TEMPLATE
        assert "pointerup" in self.TEMPLATE

    def test_a_cancelled_drag_is_handled(self):
        assert "pointercancel" in self.TEMPLATE

    def test_it_only_hides_the_buttons_once_it_runs(self):
        assert 'list.classList.add("dragon")' in self.TEMPLATE

    def test_it_does_nothing_without_the_list(self):
        assert "if (!list || !form || !field) { return; }" in self.TEMPLATE

    def test_it_saves_the_whole_order(self):
        assert 'field.value = order().join(",")' in self.TEMPLATE

    def test_a_drag_that_moved_nothing_saves_nothing(self):
        assert 'if (order().join(",") !== before) { save(); }' in self.TEMPLATE

    def test_the_arrow_keys_do_what_dragging_does(self):
        assert '"ArrowUp"' in self.TEMPLATE and '"ArrowDown"' in self.TEMPLATE


class TestTheStylesheet:
    from pathlib import Path as _Path

    CSS = (_Path(__file__).resolve().parent.parent / "app" / "static" / "css"
           / "app.css").read_text()

    def test_the_handle_is_hidden_until_the_script_runs(self):
        assert ".grip{" in self.CSS
        assert ".runsheet.dragon .grip{display:block}" in self.CSS

    def test_the_buttons_go_away_only_once_it_runs(self):
        assert ".runsheet.dragon .nojsmove{display:none}" in self.CSS

    def test_the_handle_does_not_scroll_the_page(self):
        """Without this a drag on a phone scrolls instead of moving the row."""
        block = self.CSS[self.CSS.index(".grip{"):]
        assert "touch-action:none" in block[:block.index("}")]

    def test_the_row_being_dragged_looks_lifted(self):
        assert ".runsheet.dragon li.lifted{" in self.CSS
