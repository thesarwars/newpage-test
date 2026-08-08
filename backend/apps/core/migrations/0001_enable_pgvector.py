"""Enable the pgvector extension.

Its own migration, ahead of any model, for two reasons: `CREATE EXTENSION` needs
privileges that later migrations should not assume, and keeping it separate means
the readiness check for `vector` maps to exactly one reviewable migration rather
than a side effect of whichever model happened to add the first VectorField.
"""

from django.db import migrations
from pgvector.django import VectorExtension


class Migration(migrations.Migration):
    initial = True

    dependencies: list[tuple[str, str]] = []

    operations = [
        VectorExtension(),
    ]
