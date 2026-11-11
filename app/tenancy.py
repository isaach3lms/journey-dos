"""Resolve the request to exactly one church, before anything else runs.

Resolution order, first match wins:

1. Custom domain. `thejourneychurchsemo.com` maps straight to a row.
2. Platform subdomain. `journey.<PLATFORM_DOMAIN>` maps to slug `journey`.
3. Query override, `?tenant=journey`. Development and tests only, because
   `localhost` has no subdomain to read and neither does a Render preview URL.
4. `DEFAULT_TENANT_SLUG`. Also development only.

Anything unresolved in production is a 404, not a fallback. Serving the wrong
church's roster because a host was misconfigured is the worst failure this
system can have, so it fails closed.

`g.church` is set once per request. Nothing else in the codebase reads the
host header.
"""

from __future__ import annotations

from flask import abort, current_app, g, request

from app.models import Church

PLATFORM_ENDPOINTS = {"health.healthz", "health.readyz", "static"}


def host_without_port(host: str) -> str:
    return (host or "").split(":", 1)[0].strip().lower()


def subdomain_for(host: str, platform_domain: str) -> str | None:
    """Return the leftmost label if `host` sits under `platform_domain`."""
    host = host_without_port(host)
    platform_domain = host_without_port(platform_domain)
    if not host or not platform_domain:
        return None
    if not host.endswith("." + platform_domain):
        return None
    label = host[: -(len(platform_domain) + 1)]
    if not label or "." in label:
        return None
    return label


def resolve_church() -> Church | None:
    """Work out which church this request belongs to. Read-only, no side effects."""
    cfg = current_app.config
    host = host_without_port(request.host)

    church = Church.by_custom_domain(host)
    if church:
        return church

    label = subdomain_for(host, cfg.get("PLATFORM_DOMAIN", ""))
    if label and label not in cfg.get("RESERVED_SUBDOMAINS", set()):
        church = Church.by_slug(label)
        if church:
            return church

    if cfg.get("ALLOW_TENANT_QUERY_OVERRIDE"):
        override = request.args.get("tenant")
        if override:
            return Church.by_slug(override)
        return Church.by_slug(cfg.get("DEFAULT_TENANT_SLUG", ""))

    return None


def register_tenancy(app) -> None:
    @app.before_request
    def _attach_church():
        if request.endpoint in PLATFORM_ENDPOINTS:
            g.church = None
            return None

        g.church = resolve_church()
        if g.church is None:
            # Fails closed. See module docstring.
            abort(404, description="No church is configured for this address.")
        return None
