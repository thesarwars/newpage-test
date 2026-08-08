"""Production settings.

Not exercised by this take-home's deployment (there isn't one), but committed so
the production story in the README is a config diff rather than a description of
one. See docs/PLAN.md §16.
"""

from .base import *  # noqa: F403

DEBUG = False

SECURE_SSL_REDIRECT = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
