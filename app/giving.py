"""Giving links.

Increment 7 does not move a church's money. It opens the giving platform they
already use. Per spec v3 section A, that decision deletes the hardest objection
in the sale (no treasurer has to migrate donor history) and removes PCI scope
entirely, because this application never touches a card number.

What it does introduce is a URL that staff type and members click, which is a
small but real surface. Two things follow:

**The scheme must be https.** `javascript:` in an href executes in the
clicking member's browser with their session. A church staff account is one
weak password away from an outsider, so this is validated rather than trusted.

**The host must be on an allowlist.** Not because a church would deliberately
point Give at somewhere else, but because a typo or a paste from the wrong tab
should fail at the form rather than send a congregation somewhere unexpected
with the church's name on the page. Adding a provider is one line here.
"""

from __future__ import annotations

from urllib.parse import urlparse

PROVIDER_TITHELY = "tithely"

# Hosts a giving link may point at, by provider. A church can only send its
# people to a platform this system knows about.
ALLOWED_HOSTS = {
    PROVIDER_TITHELY: (
        "tithe.ly",
        "get.tithe.ly",
        "give.tithe.ly",
        "tithely.com",
    ),
}

PROVIDER_LABELS = {PROVIDER_TITHELY: "Tithely"}

PROVIDERS = tuple(ALLOWED_HOSTS)


class InvalidGivingURL(ValueError):
    """Raised with a message a staff member can act on."""


def provider_label(code: str) -> str:
    return PROVIDER_LABELS.get(code, code.title())


def _host_allowed(host: str, provider: str) -> bool:
    host = host.lower()
    for allowed in ALLOWED_HOSTS.get(provider, ()):
        # Exact match or a subdomain of it. Endswith alone would accept
        # `nottithe.ly`, which is the whole trick.
        if host == allowed or host.endswith("." + allowed):
            return True
    return False


def validate_giving_url(raw: str | None, provider: str = PROVIDER_TITHELY) -> str | None:
    """Return a clean URL, or raise with a reason. Empty input clears the field."""
    if raw is None:
        return None
    raw = raw.strip()
    if not raw:
        return None

    if len(raw) > 500:
        raise InvalidGivingURL("That link is too long to be right.")

    parsed = urlparse(raw)

    if parsed.scheme.lower() != "https":
        raise InvalidGivingURL(
            "Giving links have to start with https. Anything else either is "
            "not secure or is not a web address at all."
        )

    if not parsed.netloc:
        raise InvalidGivingURL("That does not look like a full web address.")

    if "@" in parsed.netloc:
        # https://tithe.ly@evil.example.com reads as Tithely and goes elsewhere.
        raise InvalidGivingURL("That address is not a plain link. Paste it again.")

    host = parsed.hostname or ""
    if not _host_allowed(host, provider):
        allowed = ", ".join(ALLOWED_HOSTS.get(provider, ()))
        raise InvalidGivingURL(
            f"That link points at {host or 'nowhere'}. It has to be a "
            f"{provider_label(provider)} address: {allowed}."
        )

    return raw
