# Python 3.12, not the host's 3.14: onnxruntime (M3, the embedder) has no 3.14
# wheels, and nothing should compile from source on a reviewer's machine.
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

# ─── deps ─────────────────────────────────────────────────────────────────────
# Copied before the source so a code change doesn't invalidate the dependency
# layer. --frozen fails loudly if uv.lock is stale rather than silently
# resolving something different from what CI tested.
FROM base AS deps
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project

# ─── dev ──────────────────────────────────────────────────────────────────────
FROM deps AS dev
RUN uv sync --frozen --no-install-project --group dev
COPY . .
RUN useradd --create-home --uid 1000 app && chown -R app:app /app /opt/venv
USER app
EXPOSE 8000
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]

# ─── prod ─────────────────────────────────────────────────────────────────────
# gthread, not sync: SSE holds a connection open for the length of a generation,
# and a sync worker would be occupied for that whole time. See docs/PLAN.md §2.
FROM deps AS prod
COPY . .
RUN useradd --create-home --uid 1000 app && chown -R app:app /app
USER app
EXPOSE 8000
CMD ["gunicorn", "config.wsgi:application", \
     "--bind", "0.0.0.0:8000", \
     "--worker-class", "gthread", \
     "--workers", "2", \
     "--threads", "8", \
     "--timeout", "120", \
     "--access-logfile", "-"]
