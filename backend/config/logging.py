"""structlog wiring.

JSON to stdout in every environment except local development, where a console
renderer is readable. Django's own loggers are routed through the same chain so
there is one log format, not two.

The redaction processor sits *last* before rendering, so it also scrubs anything
Django itself put in the event.
"""

from __future__ import annotations

from typing import Any

import structlog

from apps.core.logging import redact_pii

CONSOLE = "console"
JSON = "json"


def configure_structlog(*, fmt: str) -> dict[str, Any]:
    """Configure structlog and return the matching Django LOGGING dict.

    Takes an explicit format rather than a `debug` flag, deliberately. Deriving
    the renderer from `DEBUG` looks tidier and is a trap: settings modules
    override `DEBUG` *after* importing base, so the renderer would be chosen
    from base's value and every leaf override would silently do nothing.
    (config.settings.test sets DEBUG=False and still logged in console format.)

    Decoupling them is also just better ops: JSON logs are sometimes what you
    want locally while debugging a parser, and console logs are sometimes what
    you want in a staging container.
    """
    shared: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
        redact_pii,
    ]

    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    if fmt not in (CONSOLE, JSON):
        raise ValueError(f"LOG_FORMAT must be {CONSOLE!r} or {JSON!r}, got {fmt!r}")

    renderer: Any = (
        structlog.dev.ConsoleRenderer() if fmt == CONSOLE else structlog.processors.JSONRenderer()
    )

    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "structlog": {
                "()": structlog.stdlib.ProcessorFormatter,
                "processor": renderer,
                "foreign_pre_chain": shared,
            },
        },
        "handlers": {
            "console": {"class": "logging.StreamHandler", "formatter": "structlog"},
        },
        "root": {"handlers": ["console"], "level": "INFO"},
        "loggers": {
            # Access logging is gunicorn's job; django.server would emit the
            # same events again in a different shape.
            "django.server": {"handlers": ["console"], "level": "WARNING", "propagate": False},
        },
    }
