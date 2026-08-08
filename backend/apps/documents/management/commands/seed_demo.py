"""`make seed` — load the demo corpus from the CLI.

Exists so the demo path can be exercised without a browser: it is how the
fixtures get smoke-tested after a change to the parser or the chunker, and how
you get a populated session to curl against. Same code as the endpoint, so a
green run here is evidence about the endpoint rather than about a parallel
implementation.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError

from apps.core.models import Session
from apps.documents.demo import WorkspaceNotEmptyError, seed


class Command(BaseCommand):
    help = "Ingest the demo résumé and job descriptions into a session."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--session",
            help="Session id to seed. Omit to create a fresh one.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        session_id = options.get("session")
        if session_id:
            session = Session.objects.filter(pk=session_id).first()
            if session is None:
                raise CommandError(f"no session {session_id}")
        else:
            session = Session.objects.create()

        try:
            documents = seed(session)
        except WorkspaceNotEmptyError as exc:
            raise CommandError(
                "That session already has documents. Seeding on top of them would "
                "trip the one-résumé limit with a confusing error."
            ) from exc

        self.stdout.write(f"session {session.id}")
        for document in documents:
            self.stdout.write(
                f"  {document.kind:7} {document.display_label:34} "
                f"{document.chunks.count():3} chunks  {document.sections.count():2} sections"
            )
        self.stdout.write(self.style.SUCCESS(f"seeded {len(documents)} documents"))
