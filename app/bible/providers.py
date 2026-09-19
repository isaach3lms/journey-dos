"""Where scripture text comes from.

Two providers behind one interface, and the difference between them is a
licensing question rather than a technical one.

**World English Bible.** Public domain. Stored in this system's own database,
served without a network call, and available to every church with no
registration, no key, and nobody's permission. This is the floor: a member can
always read the passage in a plan.

**YouVersion Platform.** A licensed translation, NIV among them, reached with
**the church's own registration**, not ours. Per spec v3 section C.5 the
arrangement is that each church registers and we operate the app on their
behalf, which is why the credential is per church.

**Licensed text is never stored.** Not in a table, not in a cache, not in a
column added later for performance. The WEB model has no `translation` column
precisely so there is nowhere for NIV to accumulate. A copy of a licensed
translation sitting in this database, replicated across every backup, is a
licence violation that grows quietly and is discovered by somebody else.

**Failure always falls back rather than failing.** If a key is missing, expired,
rejected, or the API is simply down, the reader shows the WEB and says which
translation it is showing. A member opening a reading plan on a Sunday morning
should never see an error where a psalm was meant to be.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from app.mail.transport import USER_AGENT
from dataclasses import dataclass, field

from app.models.bible import WEB_CODE, WEB_NAME, BibleVerse

YOUVERSION_ENDPOINT = "https://api.youversion.com/v1/passages"
DEFAULT_TIMEOUT = 6

PROVIDER_WEB = "web"
PROVIDER_YOUVERSION = "youversion"


@dataclass(frozen=True)
class Verse:
    number: int
    text: str


@dataclass(frozen=True)
class Passage:
    reference: str
    translation_code: str
    translation_name: str
    verses: tuple[Verse, ...] = field(default_factory=tuple)
    # Set when a licensed provider was asked for and could not answer. The
    # reader shows this, because a member deserves to know they are reading a
    # different translation from the one their church chose.
    fell_back: bool = False
    fallback_reason: str | None = None

    @property
    def is_empty(self) -> bool:
        return not self.verses

    @property
    def text(self) -> str:
        return " ".join(verse.text for verse in self.verses)


class BibleProvider:
    code = "base"
    name = "base"

    def fetch(self, reference) -> Passage:
        raise NotImplementedError


class WebBibleProvider(BibleProvider):
    """Public domain, local, always available."""

    code = PROVIDER_WEB
    name = WEB_NAME

    def fetch(self, reference) -> Passage:
        rows = BibleVerse.passage(reference)
        return Passage(
            reference=str(reference),
            translation_code=WEB_CODE,
            translation_name=WEB_NAME,
            verses=tuple(Verse(row.verse, row.text) for row in rows),
        )


class YouVersionProvider(BibleProvider):
    """A licensed translation under the church's own registration.

    Nothing returned by this provider is written to the database. The result is
    rendered and discarded.
    """

    code = PROVIDER_YOUVERSION

    def __init__(self, app_key: str, translation: str = "niv", timeout: int = DEFAULT_TIMEOUT):
        if not app_key:
            raise ValueError(
                "A YouVersion app key is required. Without one the reader "
                "should use the World English Bible rather than construct a "
                "provider that cannot answer."
            )
        self.app_key = app_key
        self.translation = translation
        self.timeout = timeout

    @property
    def name(self) -> str:
        return self.translation.upper()

    def fetch(self, reference) -> Passage:
        request = urllib.request.Request(
            f"{YOUVERSION_ENDPOINT}?reference={urllib.parse.quote(str(reference))}"
            f"&version={self.translation}",
            headers={
                "X-YouVersion-Developer-Token": self.app_key,
                # See app/mail/transport.py: the default urllib signature is
                # blocked by Cloudflare-fronted APIs.
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
            },
            method="GET",
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            payload = json.loads(response.read().decode("utf-8") or "{}")

        verses = tuple(
            Verse(int(item.get("verse", index + 1)), (item.get("text") or "").strip())
            for index, item in enumerate(payload.get("verses", []))
        )
        return Passage(
            reference=str(reference),
            translation_code=self.translation,
            translation_name=self.name,
            verses=verses,
        )


def provider_for(church, secret_key: str | None = None) -> BibleProvider:
    """The provider a church has registered, or the public domain floor."""
    from app.models import IntegrationCredential

    credential = IntegrationCredential.for_provider(church.id, PROVIDER_YOUVERSION)
    if credential is None or not credential.is_usable or not secret_key:
        return WebBibleProvider()

    try:
        key = credential.private_key(secret_key)
    except Exception:  # noqa: BLE001
        return WebBibleProvider()

    if not key:
        return WebBibleProvider()

    try:
        return YouVersionProvider(key, translation=credential.organization_ref or "niv")
    except ValueError:
        return WebBibleProvider()


def fetch_passage(reference, church, secret_key: str | None = None) -> Passage:
    """Get a passage, falling back rather than failing.

    A member opening a reading plan on a Sunday morning should never see an
    error where a psalm was meant to be. If the licensed provider cannot
    answer, they read the WEB and the page says so.
    """
    if reference is None:
        return Passage("", WEB_CODE, WEB_NAME)

    provider = provider_for(church, secret_key)

    if isinstance(provider, WebBibleProvider):
        return provider.fetch(reference)

    try:
        passage = provider.fetch(reference)
        if not passage.is_empty:
            return passage
        reason = "That passage came back empty."
    except urllib.error.HTTPError as exc:
        reason = f"The translation service refused the request ({exc.code})."
    except urllib.error.URLError as exc:
        reason = f"The translation service could not be reached ({exc.reason})."
    except Exception as exc:  # noqa: BLE001
        reason = f"The translation service failed ({exc})."

    fallback = WebBibleProvider().fetch(reference)
    return Passage(
        reference=fallback.reference,
        translation_code=fallback.translation_code,
        translation_name=fallback.translation_name,
        verses=fallback.verses,
        fell_back=True,
        fallback_reason=reason,
    )
