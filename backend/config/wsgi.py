"""WSGI entrypoint.

WSGI, not ASGI, is deliberate: StreamingHttpResponse streams correctly under
gunicorn's gthread worker, and ASGI would mean sync_to_async around every ORM
write on the hottest path for no benefit at this scale. The honest ceiling is
~16 concurrent SSE streams per task — see docs/PLAN.md §2 and §16.
"""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.prod")

application = get_wsgi_application()
