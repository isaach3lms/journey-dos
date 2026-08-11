from flask import Blueprint, current_app, render_template
from flask_login import current_user, login_required

from ..content import GIVE
from ..models import Church, Stage

bp = Blueprint("portal", __name__, url_prefix="/app")


@bp.route("/")
@login_required
def home():
    stages = (
        Stage.query.filter_by(church_id=current_user.church_id)
        .order_by(Stage.position)
        .all()
    )
    return render_template("portal/home.html", stages=stages)


@bp.route("/journey")
@login_required
def journey():
    stages = (
        Stage.query.filter_by(church_id=current_user.church_id)
        .order_by(Stage.position)
        .all()
    )
    return render_template("portal/journey.html", stages=stages, events=current_user.stage_events)


@bp.route("/give")
@login_required
def give():
    church = Church.query.get(current_user.church_id)
    give_url = church.tithely_give_url or current_app.config.get("TITHELY_GIVE_URL")
    form_id = church.tithely_form_id or current_app.config.get("TITHELY_FORM_ID")
    if not give_url and form_id:
        give_url = f"https://give.tithe.ly/?formId={form_id}"
    return render_template("portal/give.html", c=GIVE, give_url=give_url)
