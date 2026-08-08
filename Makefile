# Every target execs inside the container. Host Python is 3.14, where onnxruntime
# (the embedder, M3) has no wheels — running pytest on the host is not a supported
# path and this Makefile is the only documented entry point. docs/PLAN.md §14.

COMPOSE := docker compose
API     := $(COMPOSE) exec -T api
WEB     := $(COMPOSE) exec -T web

.DEFAULT_GOAL := help
.PHONY: help up down build logs migrate makemigrations shell test test-web eval eval-baseline lint fmt typecheck clean

help: ## Show this help
	@grep -hE '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

up: .env ## Build, start, migrate, and print the URL
	$(COMPOSE) up -d --build
	@echo "waiting for api…" && $(COMPOSE) exec -T api sh -c 'until curl -fsS http://localhost:8000/healthz >/dev/null 2>&1; do sleep 1; done' || true
	-$(API) python manage.py migrate --noinput
	@echo ""
	@echo "  web  →  http://localhost:3000"
	@echo "  api  →  http://localhost:8000/healthz"
	@echo ""

.env: .env.example ## Create .env with freshly generated local secrets
	@cp .env.example .env
	@printf '%s\n' "$$(sed "s|^DJANGO_SECRET_KEY=.*|DJANGO_SECRET_KEY=$$(LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom 2>/dev/null | head -c 50)|; s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=$$(LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom 2>/dev/null | head -c 32)|" .env)" > .env.tmp && mv .env.tmp .env
	@echo "generated .env with fresh local secrets (gitignored)"

down: ## Stop and remove containers (keeps the database volume)
	$(COMPOSE) down

build: ## Build all images
	$(COMPOSE) build

logs: ## Tail logs
	$(COMPOSE) logs -f --tail=100

migrate: ## Apply migrations
	$(API) python manage.py migrate --noinput

makemigrations: ## Generate migrations
	$(API) python manage.py makemigrations

shell: ## Django shell
	$(COMPOSE) exec api python manage.py shell

test: ## Backend suite (no network, no API key)
	$(API) pytest

eval: ## Retrieval evaluation against the golden set (no API key needed)
	$(API) python manage.py run_eval

eval-baseline: ## Re-run the eval and commit the numbers as the new baseline
	$(API) python manage.py run_eval --write-baseline

lint: ## ruff + mypy
	$(API) ruff check .
	$(API) ruff format --check .
	$(API) mypy .

fmt: ## Autoformat
	$(API) ruff format .
	$(API) ruff check --fix .

typecheck: ## Frontend types
	$(WEB) pnpm typecheck

test-web: ## Frontend lint + types + build
	$(WEB) pnpm lint
	$(WEB) pnpm typecheck
	$(WEB) pnpm build

clean: ## Remove containers AND the database volume
	$(COMPOSE) down -v
