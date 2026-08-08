"""Delete sessions past their TTL, and everything they own.

Makes the 7-day retention claim in the README true rather than aspirational. Run
from cron in a deployment; run by hand here. `--dry-run` because the first thing
anyone wants from a deletion command is to see what it would take.
"""

from __future__ import annotations

from typing import Any

import structlog
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.core.models import Session

log = structlog.get_logger(__name__)


class Command(BaseCommand):
    help = "Delete sessions whose TTL has expired, cascading to all owned rows."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would be deleted without deleting it.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        expired = Session.objects.filter(expires_at__lte=timezone.now())
        count = expired.count()

        if options["dry_run"]:
            self.stdout.write(f"would delete {count} expired session(s)")
            return

        if count:
            expired.delete()
            log.info("sessions_purged", count=count)

        self.stdout.write(self.style.SUCCESS(f"deleted {count} expired session(s)"))
