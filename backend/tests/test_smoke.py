"""M0 smoke tests: the scaffold is wired and CI has something real to run."""

from django.test import Client


def test_healthz_is_up_and_touches_no_dependency() -> None:
    """Liveness must not depend on the database.

    Asserted with no `django_db` marker: if this view ever grows a query, the
    test fails rather than the probe causing restart storms in production.
    """
    response = Client().get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_llm_is_never_reachable_from_the_test_settings() -> None:
    """CI must not be able to spend money (docs/PLAN.md §11).

    This is not a tautology, and it has already earned its place: the container
    and CI both export DJANGO_SETTINGS_MODULE=config.settings.dev, which
    pytest-django ranks *above* the ini key. Without the --ds in addopts the
    whole suite runs against dev settings and a real API key would be live.
    """
    from django.conf import settings

    assert settings.SETTINGS_MODULE == "config.settings.test"
    assert settings.LLM_BACKEND == "fake"
    assert settings.ANTHROPIC_API_KEY == ""
