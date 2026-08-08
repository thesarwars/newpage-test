"""Settings shared by every environment.

Environment-specific modules (dev/prod/test) import * from here and override.
Nothing in this file reads a secret without a default that is safe to commit.
"""

from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env(
    DJANGO_DEBUG=(bool, False),
    DJANGO_ALLOWED_HOSTS=(list, ["localhost", "127.0.0.1"]),
    CORS_ALLOWED_ORIGINS=(list, ["http://localhost:3000"]),
    LLM_DAILY_COST_CEILING_USD=(float, 10.0),
)

SECRET_KEY = env("DJANGO_SECRET_KEY", default="")
DEBUG = env("DJANGO_DEBUG")
ALLOWED_HOSTS = env("DJANGO_ALLOWED_HOSTS")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.postgres",
    "django.contrib.staticfiles",
    "rest_framework",
    "corsheaders",
    "apps.core",
    "apps.documents",
    "apps.rag",
    "apps.analysis",
    "apps.chat",
    "apps.observability",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # Outermost of ours: everything downstream logs with the correlation id
    # already bound, including the session resolution below it.
    "apps.core.middleware.RequestIdMiddleware",
    "apps.core.middleware.SessionCookieMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env("POSTGRES_DB", default="cia"),
        "USER": env("POSTGRES_USER", default="cia"),
        # No default. An empty password fails to connect loudly; a committed
        # one would be a real credential in a public repository.
        "PASSWORD": env("POSTGRES_PASSWORD", default=""),
        "HOST": env("POSTGRES_HOST", default="db"),
        "PORT": env("POSTGRES_PORT", default="5432"),
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
# No MEDIA_ROOT: nothing in this project writes a user file to disk. Uploads are
# parsed in memory and only the normalized text is stored. See
# apps/documents/models.py.

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    # No auth classes and no user: the tenant is the signed session cookie,
    # resolved by SessionCookieMiddleware. See apps/core/models.py.
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
    "UNAUTHENTICATED_USER": None,
    "EXCEPTION_HANDLER": "apps.core.errors.exception_handler",
    # Two windows on chat, because one is always the wrong shape: 20/min alone
    # permits 1,200 an hour, and 60/hour alone permits all 60 in one second.
    "DEFAULT_THROTTLE_RATES": {
        "chat_burst": env("THROTTLE_CHAT_BURST", default="20/min"),
        "chat_sustained": env("THROTTLE_CHAT_SUSTAINED", default="60/hour"),
        "upload": env("THROTTLE_UPLOAD", default="20/hour"),
    },
}

# Django rejects a larger body before the view runs, which surfaced as a bare
# 500 with no error_code. Sized just above MAX_PASTED_CHARS (120k chars, up to
# 4 bytes each under UTF-8) plus JSON overhead, so the limit a user actually
# meets is the documented one with a real message — not this backstop.
DATA_UPLOAD_MAX_MEMORY_SIZE = 6 * 1024 * 1024
# The multipart path is bounded separately by MAX_FILE_BYTES (10 MB), checked in
# validate_upload with its own error_code.
FILE_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024 + 1024

CORS_ALLOWED_ORIGINS = env("CORS_ALLOWED_ORIGINS")
CORS_ALLOW_CREDENTIALS = True

# ─── Application settings (docs/PLAN.md §14) ──────────────────────────────────
ANTHROPIC_API_KEY = env("ANTHROPIC_API_KEY", default="")
CIA_CHAT_MODEL = env("CIA_CHAT_MODEL", default="claude-opus-5")
LLM_BACKEND = env("LLM_BACKEND", default="auto")  # auto | anthropic | fake
LLM_DAILY_COST_CEILING_USD = env("LLM_DAILY_COST_CEILING_USD")
# Artificial per-token delay for the keyless stub, so a demo answer streams
# visibly instead of arriving whole. Zero in tests — the suite should not spend
# wall-clock proving that `time.sleep` sleeps.
LLM_FAKE_DELAY_S = env.float("LLM_FAKE_DELAY_S", default=0.012)
CIA_CHAT_EFFORT = env("CIA_CHAT_EFFORT", default="medium")
EMBEDDING_BACKEND = env("EMBEDDING_BACKEND", default="local")
EMBEDDING_MODEL = env("EMBEDDING_MODEL", default="BAAI/bge-small-en-v1.5")
# Where the ONNX weights are baked at image build time (backend/Dockerfile).
FASTEMBED_CACHE_PATH = env("FASTEMBED_CACHE_PATH", default="/opt/models")

# Structured logs to stdout, with PII redaction on by default. Configured here
# rather than in AppConfig.ready() so logging is live before the app registry
# finishes loading — startup failures are logs too.
#
# LOG_FORMAT is explicit rather than derived from DEBUG: leaf settings modules
# override DEBUG after importing this file, so a derived renderer would ignore
# them. See config/logging.py.
from config.logging import configure_structlog  # noqa: E402

LOG_FORMAT = env("LOG_FORMAT", default="console" if DEBUG else "json")
LOGGING = configure_structlog(fmt=LOG_FORMAT)
