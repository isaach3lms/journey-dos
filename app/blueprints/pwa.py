"""Installing the member app to a home screen.

Three files and one privacy decision.

The manifest and the icons are per church, generated from the same brand tokens
as every screen, so a member who installs it gets their church's name and
colour on their home screen rather than ours. That is the entire point: it
should read as their church's app, not as a product they were signed up for.

**The privacy decision, which shapes the service worker.** A church tablet in a
lobby and a family iPad are both shared devices. A cache that keeps a member's
pages after they sign out would show the next person somebody else's giving
history, their household PIN, or their conversations. So:

- Static assets are cached hard. They contain no data about anybody.
- Pages that render a person are cached in a **separate, named runtime cache**
  that is deleted on sign-out.
- Anything under `/auth/` is never cached at all.

Offline reading works because a plan the member already opened is in the
runtime cache. Signing out empties it, which is the correct tradeoff: reading
offline is a convenience, and showing one person another's record is not a
tradeoff at all.
"""

from __future__ import annotations

from flask import Blueprint, Response, g, render_template, url_for

from app.brand import palette_for
from app.content import PWA

bp = Blueprint("pwa", __name__)

def _asset_fingerprint() -> str:
    """A hash of everything the worker caches hard.

    Hand-typing a version number does not work. I shipped a Services redesign
    and forgot to bump it, the worker kept serving the previous stylesheet
    cache-first, and the new layout simply did not appear: the HTML was
    current and the CSS was a week old. Nothing in the deploy or the tests
    could have caught that, because the staleness lived in a browser.

    Deriving it from the file contents means any change to a cached asset
    invalidates the cache by construction, and no future change can forget.
    """
    import hashlib
    from pathlib import Path as _Path

    static = _Path(__file__).resolve().parent.parent / "static"
    digest = hashlib.sha256()
    for name in sorted(("css/app.css", "img/icon-192.png")):
        path = static / name
        if path.exists():
            digest.update(path.read_bytes())

    # The worker itself is a template, so its own text counts too.
    worker = _Path(__file__).resolve().parent.parent / "templates" / "pwa" / "sw.js"
    if worker.exists():
        digest.update(worker.read_bytes())

    return "dos-" + digest.hexdigest()[:12]


CACHE_VERSION = _asset_fingerprint()


# Words that carry no identity in a church name. "The Journey Church" under
# an icon should read "Journey", not "The" and not "Church".
_SKIP_WORDS = {"the", "a", "an", "church", "chapel", "fellowship", "community",
               "parish", "assembly", "ministries", "ministry", "of", "at"}

SHORT_NAME_LIMIT = 12


def short_name_for(church) -> str:
    """What appears under the icon on a home screen.

    Home screens truncate hard, so this picks the word that identifies the
    church rather than the first word in its name.
    """
    full = (church.app_name or church.name or "").strip()
    words = full.split()
    meaningful = [w for w in words if w.lower().strip(".,'") not in _SKIP_WORDS]

    candidate = " ".join(meaningful) if meaningful else full
    if len(candidate) <= SHORT_NAME_LIMIT:
        return candidate or full[:SHORT_NAME_LIMIT]
    # Still too long: the single most identifying word beats a truncation
    # with a word cut in half.
    return (meaningful[0] if meaningful else full)[:SHORT_NAME_LIMIT]


@bp.get("/manifest.webmanifest")
def manifest():
    """Per church, from the brand tokens. No church resolved is a 404.

    Served with the correct MIME type, because a browser silently ignores a
    manifest sent as text/html and the install prompt simply never appears,
    with nothing in the console to explain why.
    """
    church = g.church
    palette = palette_for(church)

    document = {
        "id": f"/?church={church.slug}",
        "name": church.display_app_name,
        "short_name": short_name_for(church),
        "description": PWA["description"].format(church=church.name),
        "start_url": "/",
        "scope": "/",
        # `standalone` is what removes the browser chrome. Without it an
        # installed app still looks like a website with an address bar.
        "display": "standalone",
        "orientation": "portrait",
        "background_color": palette.bone,
        "theme_color": palette.deep,
        "lang": "en",
        "icons": [
            {
                "src": url_for("static", filename="img/icon-192.png"),
                "sizes": "192x192",
                "type": "image/png",
                "purpose": "any",
            },
            {
                "src": url_for("static", filename="img/icon-512.png"),
                "sizes": "512x512",
                "type": "image/png",
                "purpose": "any",
            },
            {
                # Android crops this to the launcher's shape, which is why the
                # artwork sits inside the middle 80 percent.
                "src": url_for("static", filename="img/icon-maskable-512.png"),
                "sizes": "512x512",
                "type": "image/png",
                "purpose": "maskable",
            },
        ],
        "shortcuts": [
            {
                "name": PWA["shortcut_read"],
                "url": "/me/read/",
                "icons": [
                    {
                        "src": url_for("static", filename="img/icon-192.png"),
                        "sizes": "192x192",
                    }
                ],
            },
            {
                "name": PWA["shortcut_you"],
                "url": "/me/you/",
                "icons": [
                    {
                        "src": url_for("static", filename="img/icon-192.png"),
                        "sizes": "192x192",
                    }
                ],
            },
        ],
    }
    return Response(
        response=__import__("json").dumps(document, indent=1),
        mimetype="application/manifest+json",
    )


@bp.get("/sw.js")
def service_worker():
    """Served from the root so its scope covers /me/.

    A service worker can only control paths at or below its own URL. Serving
    this from /static/ would scope it to /static/ and it would control nothing
    that matters.
    """
    body = render_template(
        "pwa/sw.js",
        cache_version=CACHE_VERSION,
        offline_url=url_for("pwa.offline"),
        assets=[
            url_for("static", filename="css/app.css"),
            url_for("static", filename="img/icon-192.png"),
            url_for("pwa.offline"),
        ],
    )
    return Response(
        response=body,
        mimetype="application/javascript",
        headers={
            # The worker itself must not be cached, or a phone keeps running
            # last month's caching rules forever.
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Service-Worker-Allowed": "/",
        },
    )


@bp.get("/offline/")
def offline():
    """Shown when a page is requested with no connection and no cached copy.

    Deliberately says nothing about the person. It is rendered from the cache
    on a device that may have been handed to somebody else.
    """
    return render_template("pwa/offline.html", church=g.church, content=PWA)
