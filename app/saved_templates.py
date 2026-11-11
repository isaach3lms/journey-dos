"""Saving what somebody just wrote, so they do not write it again.

Three small behaviours, shared by every screen that composes something to one
person: the email form on a profile, and the direct message composer in a
thread. They were written for the email form and moved here the moment the
second caller appeared, because two copies of "put the first name back" is
how one of them ends up greeting the wrong person.

The rules, in one place:

- **Naming it is what saves it.** No checkbox and no separate editor. The
  box is on the form, and a name in it means keep this.
- **The person comes back out.** Somebody writes "Hello Amy" to Amy; what is
  stored says "Hello {first_name}". Subject as well as body.
- **A use is a send.** The count behind the picker's ordering goes up when
  something is actually sent from a template, not when somebody opens one and
  changes their mind.
- **Filled when loaded, never at send.** See `app/models/email_template.py`.
"""

from __future__ import annotations

from flask import g, request
from flask_login import current_user

from app.extensions import db
from app.models import EmailTemplate, TEMPLATE_EMAIL

# A first name this short is not swapped out. A church with an Al or a Jo
# would otherwise find the letters cut out of the middle of other words.
SHORT_NAME = 2


def generaliser(person):
    """A function that takes this person's first name back out of some text."""

    def generalise(text: str) -> str:
        first = (person.first_name or "") if person is not None else ""
        if len(first) <= SHORT_NAME:
            return text
        return text.replace(first, "{first_name}")

    return generalise


def save_from_form(
    person,
    body: str,
    *,
    kind: str = TEMPLATE_EMAIL,
    category: str = "",
    subject: str = "",
    field: str = "template_name",
):
    """Keep what was just written, if the person named it. Caller commits.

    Returns the template, or None when the box was left empty, which is most
    sends. Saving under a name already in use replaces that template rather
    than making a second one nobody can tell apart.
    """
    name = (request.form.get(field) or "").strip()
    if not name:
        return None

    generalise = generaliser(person)

    template = EmailTemplate.named(g.church.id, name, kind=kind)
    if template is None:
        template = EmailTemplate(
            church_id=g.church.id, name=name[:120], kind=kind
        )
        db.session.add(template)

    template.category = category
    template.subject = generalise(subject)[:200]
    template.body = generalise(body)
    template.is_archived = False
    if template.created_by_id is None:
        template.created_by_id = current_user.id
    db.session.commit()
    return template


def prefill(person, *, kind: str = TEMPLATE_EMAIL, arg: str = "template") -> dict:
    """The form's starting contents, when a template was picked.

    A dictionary rather than a model, because the subject and body have
    already had this person put into them and are no longer the template's
    own words. Empty when nothing was picked, which is the normal case.

    Reached by a link with `?template=`, so it works with no JavaScript and
    so somebody can be sent a link to a form already filled in.
    """
    template_id = request.args.get(arg, type=int)
    if not template_id:
        return {}

    template = EmailTemplate.get_for_church(g.church.id, template_id, kind=kind)
    if template is None:
        return {}

    subject, body = template.rendered(person, g.church)
    return {
        "id": template.id,
        "name": template.name,
        "category": template.category,
        "subject": subject,
        "body": body,
    }


def count_use(*, kind: str = TEMPLATE_EMAIL, field: str = "from_template") -> None:
    """Note that something actually went out from a saved template.

    Counted on the send rather than on the click that loaded it, because a
    template somebody opened, read and decided against is not one the church
    uses, and the picker's order is meant to answer "what do we send" rather
    than "what have we looked at". Caller commits.
    """
    template_id = request.form.get(field, type=int)
    if not template_id:
        return
    template = EmailTemplate.get_for_church(g.church.id, template_id, kind=kind)
    if template is None:
        return
    template.used()
    db.session.commit()
