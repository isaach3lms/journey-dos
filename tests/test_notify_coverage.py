"""The guard that stops the next notification from being email only.

This bug has now happened five times. The push machinery was built, tested,
and wired to nothing. Then `app/notify.py` was written to make one call do
both channels, and four more send sites were found still calling `queue`
directly: a pastoral request, a reported message, a service plan, and a
church-wide announcement. Each one looked right in testing, because email
worked.

Reviewing for it does not work. Every one of those sites was written by
somebody who knew about the rule, and the failure mode is silent: nobody
notices a notification that does not arrive, and the email that does arrive
makes the feature look finished.

So the rule is enforced here. **Every call to `app.mail.queue` outside the
outbox and `app.notify` itself must be on the list below, with a reason.** A
new send site has two ways through this test: call `notify`, or add itself to
the list and say in writing why email alone is right. The second one is a
sentence somebody has to write and a reviewer gets to argue with, which is
the whole point.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parent.parent / "app"

# Where `queue` may be called directly, and why.
#
# A person-directed notification does not belong here. Everything on this list
# is either addressed to a typed-in email address, where there is no person to
# push to, or a message whose whole content is an email.
EMAIL_ONLY = {
    "app/notify.py": "This is the function that wraps it. It pushes too.",
    "app/alerts.py": (
        "Staff alerts. The `named` branch sends to addresses a church typed "
        "in, which are usually not accounts in this system, so there is no "
        "person to push to. The staff-account branch in the same function "
        "calls `notify`."
    ),
    "app/accounts.py": (
        "Account mail: a confirmation link, a password reset. Nobody has the "
        "app yet, which is the entire situation these are sent in."
    ),
    "app/automation.py": (
        "A drip sequence. These are written as letters, a paragraph or more "
        "each, and a welcome series that also pushed four times in a fortnight "
        "would be the reason somebody turns notifications off."
    ),
    "app/guests.py": (
        "The account-request half, which goes to whoever administers the "
        "platform rather than to church staff. The connect-card half calls "
        "`tell_staff`, which pushes."
    ),
    "app/blueprints/kids.py": (
        "The forgot-PIN screen. Somebody is standing at a kiosk asking for "
        "their household code to be sent to them, and the address they are "
        "waiting on is the point of the screen."
    ),
    "app/blueprints/auth.py": (
        "Sign-in and sign-up mail. Same as app/accounts.py: there is no app "
        "on the phone of somebody who cannot sign in yet."
    ),
    "app/blueprints/settings.py": (
        "A test email to prove the mail transport works, and the delivery "
        "screen's retry. Both are about email specifically."
    ),
    "app/blueprints/people.py": (
        "A staff member composing an email to one person on their record, "
        "and the self-signup approval notice. The first is an email by "
        "definition; the second reaches somebody who has just been given an "
        "account and has not installed anything."
    ),
}

# `app/mail/outbox.py` is not on that list. It defines `queue` and never calls
# it, and the staleness check below would strike it off for that, which is
# correct: there is nothing in it to exempt.

# Modules that may call `app.push.send.send_to_person` directly. Everything
# else must go through `notify`, which is what keeps the opt-out check and the
# email in one place.
PUSH_DIRECT = {
    "app/notify.py": "This is the function that wraps it.",
    "app/push/send.py": "This is it.",
    "app/push/selftest.py": (
        "A self-test for one device, whose whole purpose is to prove the push "
        "transport works. An email alongside it would prove nothing."
    ),
}


def _python_files():
    for path in sorted(APP.rglob("*.py")):
        yield path, path.relative_to(APP.parent).as_posix()


def _calls_named(tree, name: str) -> bool:
    """Does this module call `name(...)` anywhere, by that bare name?

    Bare name only, deliberately. `self.queue(...)` or `q.queue(...)` is some
    other queue, and a test that flagged those would be a test people learn
    to work around.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id == name:
                return True
    return False


class TestEverySendSiteDoesBothChannels:
    """The rule: `queue` directly means email only, and that needs a reason."""

    def test_no_undeclared_email_only_send_site(self):
        offenders = []
        for path, rel in _python_files():
            if rel in EMAIL_ONLY:
                continue
            if _calls_named(ast.parse(path.read_text()), "queue"):
                offenders.append(rel)

        assert not offenders, (
            "These call app.mail.queue directly, which sends email and no "
            f"notification: {offenders}. Call app.notify.notify instead, or "
            "add the file to EMAIL_ONLY in this test with a written reason "
            "for why email alone is right."
        )

    def test_no_undeclared_direct_push(self):
        offenders = []
        for path, rel in _python_files():
            if rel in PUSH_DIRECT:
                continue
            if _calls_named(ast.parse(path.read_text()), "send_to_person"):
                offenders.append(rel)

        assert not offenders, (
            "These push directly, bypassing the opt-out check and the email "
            f"that is supposed to go with it: {offenders}. Use "
            "app.notify.notify."
        )

    @pytest.mark.parametrize("rel", sorted(EMAIL_ONLY))
    def test_every_exemption_still_exists_and_is_explained(self, rel):
        """A stale exemption is a hole waiting for the next send site.

        If a file stops calling `queue`, its exemption comes off the list.
        Otherwise the next person to add a send site to that file inherits
        permission to forget push, granted by a reason that is about code
        somebody deleted.
        """
        path = APP.parent / rel
        assert path.exists(), f"{rel} is on the exemption list but is gone."
        assert _calls_named(ast.parse(path.read_text()), "queue"), (
            f"{rel} no longer calls queue. Remove it from EMAIL_ONLY so the "
            "next send site added to that file is checked."
        )
        assert len(EMAIL_ONLY[rel]) > 20, (
            f"{rel} needs a real reason, not a placeholder."
        )

    @pytest.mark.parametrize("rel", sorted(PUSH_DIRECT))
    def test_every_push_exemption_still_pushes(self, rel):
        path = APP.parent / rel
        assert path.exists(), f"{rel} is on the push exemption list but is gone."
        assert _calls_named(ast.parse(path.read_text()), "send_to_person"), (
            f"{rel} no longer pushes directly. Remove it from PUSH_DIRECT."
        )


class TestTheGuardActuallyCatchesThings:
    """The guard above passes on a clean tree, which proves nothing on its own.

    These two run it against code that breaks the rule and assert it says so.
    Without them, a refactor that quietly stopped the AST walk from matching
    anything would leave two green tests enforcing nothing, which is the same
    class of silent failure the whole file exists to prevent.
    """

    def test_it_sees_a_bare_queue_call(self, tmp_path):
        bad = tmp_path / "sneaky.py"
        bad.write_text("def f(p):\n    queue(person=p, category='chat')\n")
        assert _calls_named(ast.parse(bad.read_text()), "queue")

    def test_it_ignores_an_unrelated_attribute_call(self, tmp_path):
        fine = tmp_path / "unrelated.py"
        fine.write_text("def f(worker):\n    worker.queue(1)\n")
        assert not _calls_named(ast.parse(fine.read_text()), "queue")
