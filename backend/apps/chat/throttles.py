"""Per-session throttles.

Keyed on the session, not the IP. An IP key is wrong in both directions here: it
throttles a shared office NAT as one user, and it does nothing against a client
that rotates addresses. The session cookie is the tenant, so it is also the
rate-limit subject.

Two windows on chat, because one is always the wrong shape — a per-minute limit
alone permits 1,200 requests an hour, and a per-hour limit alone permits all 60
in the first second.

Backed by Django's default local-memory cache, which means the limit is
per-process. With one gunicorn worker that is exact; with four it is four times
looser. Correct for this deployment, wrong for a real one, and the fix is a
shared Redis cache rather than a code change — noted here so the limitation is
recorded where the code is, not only in the README.
"""

from __future__ import annotations

from typing import Any

from rest_framework.request import Request
from rest_framework.throttling import SimpleRateThrottle


class SessionThrottle(SimpleRateThrottle):
    """Base: rate-limit by session id, and don't limit at all without one."""

    def get_cache_key(self, request: Request, view: Any) -> str | None:
        session = getattr(request, "cia_session", None)
        if session is None:
            # No session means the request is about to be rejected anyway; a
            # throttle key derived from nothing would bucket every anonymous
            # caller together.
            return None
        return self.cache_format % {"scope": self.scope, "ident": str(session.pk)}


class ChatBurstThrottle(SessionThrottle):
    scope = "chat_burst"


class ChatSustainedThrottle(SessionThrottle):
    scope = "chat_sustained"
