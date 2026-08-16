"""Tenant resolution.

Order of resolution, per request:

1. Exact host match in ``church_domain``. This is how app.thejourneychurchsemo.com
   resolves, and how any future client custom domain will.
2. Platform subdomain fallback: ``<slug>.<PLATFORM_DOMAIN>`` matched against
   ``church.slug``. This keeps a working URL during DNS cutover, and gives
   support a host that does not depend on the client's registrar.
3. Development only: DEFAULT_CHURCH_SLUG, so localhost:5000 works without
   editing /etc/hosts.

An unresolved host is a 404, never a fallback to "the first church in the
table". A tenant chosen by accident is the failure this whole design exists
to prevent.
"""

from __future__ import annotations

from flask import current_app, g, request
from werkzeug.local import LocalProxy

from app.extensions import db
from app.models.church import Church, ChurchDomain

#: Endpoints that must answer before a tenant is known.
TENANT_EXEMPT_ENDPOINTS = {
    "health.livez",
    "health.readyz",
    "static",
}


def normalize_host(raw: str | None) -> str:
    """Lowercase, strip the port, strip a leading www. The only place this happens."""
    if not raw:
        return ""
    host = raw.strip().lower()
    if "@" in host:
        host = host.rsplit("@", 1)[-1]
    if host.startswith("[") and "]" in host:  # IPv6 literal
        host = host[1:host.index("]")]
    elif ":" in host:
        host = host.rsplit(":", 1)[0]
    if host.startswith("www."):
        host = host[4:]
    return host.rstrip(".")


def subdomain_slug(host: str, platform_domain: str) -> str | None:
    """Return the leading label when host is a subdomain of the platform domain."""
    platform = normalize_host(platform_domain)
    if not platform or not host or host == platform:
        return None
    suffix = "." + platform
    if not host.endswith(suffix):
        return None
    label = host[: -len(suffix)]
    if not label or "." in label:
        return None
    return label


def resolve_church(raw_host: str | None) -> Church | None:
    """Resolve a Host header to a tenant, or None."""
    host = normalize_host(raw_host)
    if not host:
        return None

    church = (
        db.session.query(Church)
        .join(ChurchDomain, ChurchDomain.church_id == Church.id)
        .filter(ChurchDomain.host == host)
        .one_or_none()
    )
    if church is not None:
        return church

    slug = subdomain_slug(host, current_app.config.get("PLATFORM_DOMAIN", ""))
    if slug:
        church = db.session.query(Church).filter(Church.slug == slug).one_or_none()
        if church is not None:
            return church

    if not current_app.config.get("IS_PRODUCTION"):
        default_slug = current_app.config.get("DEFAULT_CHURCH_SLUG")
        if default_slug:
            return (
                db.session.query(Church)
                .filter(Church.slug == default_slug)
                .one_or_none()
            )
    return None


def load_current_church() -> None:
    """before_request hook. Populates g.church or leaves it None."""
    g.church = None
    endpoint = request.endpoint or ""
    if endpoint in TENANT_EXEMPT_ENDPOINTS:
        return
    church = resolve_church(request.host)
    if church is not None and church.is_active:
        g.church = church


def _get_current_church():
    return getattr(g, "church", None)


current_church = LocalProxy(_get_current_church)
