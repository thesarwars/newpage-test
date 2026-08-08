"""Liveness, readiness, version."""

from __future__ import annotations

import pytest
from rest_framework.test import APIClient


def test_healthz_touches_no_database(client: APIClient) -> None:
    """No django_db marker: if liveness ever grows a query, this fails.

    That is the point — a liveness probe that checks the database converts one
    slow query into a restart storm.
    """
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.django_db
def test_readyz_reports_database_and_pgvector(client: APIClient) -> None:
    response = client.get("/readyz")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["checks"]["database"]["ok"] is True
    assert body["checks"]["pgvector"]["ok"] is True, (
        "the vector extension is missing — 0001_enable_pgvector did not run"
    )


@pytest.mark.django_db
def test_readyz_reports_the_anthropic_key_without_requiring_it(client: APIClient) -> None:
    """The keyless path is a supported deployment, not a degraded one.

    A readiness probe that failed on a missing optional credential would make
    docs/PLAN.md §14 undeployable.
    """
    body = client.get("/readyz").json()

    key_check = body["checks"]["anthropic_key"]
    assert key_check["required"] is False
    assert key_check["ok"] is False  # test settings deliberately hold no key
    assert body["status"] == "ready"


@pytest.mark.django_db
def test_readyz_reports_the_embedder_as_not_yet_required(client: APIClient) -> None:
    """Until M3 there is no embedder; readiness must not lie about it either way."""
    embedder = client.get("/readyz").json()["checks"]["embedder"]

    assert embedder["required"] is False


def test_version_exposes_build_and_model_identity(client: APIClient) -> None:
    body = client.get("/version").json()

    assert set(body) == {"git_sha", "built_at", "model", "embedding_model"}
    assert body["model"] == "claude-opus-5"
