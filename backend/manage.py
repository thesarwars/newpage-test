#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""

import os
import sys


def main() -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:  # pragma: no cover - import-time guard
        raise ImportError(
            "Couldn't import Django. Are you running inside the container? "
            "Every target in the Makefile execs in the api container — host Python "
            "is not a supported environment. See docs/PLAN.md §14."
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
