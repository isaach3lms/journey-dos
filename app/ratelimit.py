"""
Rate limiting.

Deliberately in process and dependency free. This deployment is a single Render
instance, so a dict is honest about what it protects: it stops password guessing
and form flooding from one address. It resets on deploy and does not coordinate
across instances. If the app ever scales past one web instance, move this to the
database or Redis and delete this note.
"""

import time
from collections import defaultdict, deque
from functools import wraps

from flask import current_app, jsonify, render_template, request

_hits = defaultdict(deque)


def client_ip() -> str:
    # Render sits behind a proxy, so the socket address is always the proxy.
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or "unknown"


def too_many(bucket: str, limit: int, window_seconds: int) -> bool:
    """Record this attempt and report whether the caller is over the limit."""
    key = f"{bucket}:{client_ip()}"
    now = time.time()
    hits = _hits[key]
    while hits and now - hits[0] > window_seconds:
        hits.popleft()
    hits.append(now)
    return len(hits) > limit


def clear(bucket: str) -> None:
    """Called after a success, so one good login forgives earlier typos."""
    _hits.pop(f"{bucket}:{client_ip()}", None)


def limit(bucket: str, limit_count: int, window_seconds: int, json_response: bool = False):
    """Decorator for POST endpoints that should not be hammered."""

    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if request.method == "POST" and too_many(bucket, limit_count, window_seconds):
                current_app.logger.warning("rate limit hit on %s from %s", bucket, client_ip())
                if json_response:
                    return jsonify({"ok": False, "error": "too many requests"}), 429
                return render_template("auth/too_many.html"), 429
            return view(*args, **kwargs)

        return wrapped

    return decorator
