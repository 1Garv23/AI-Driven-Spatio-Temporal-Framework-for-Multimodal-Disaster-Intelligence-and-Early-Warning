.PHONY: test test-unit test-contract test-integration test-perf test-all lint fmt typecheck up down clean

# ── Testing ──────────────────────────────────────────────────────────
test: test-unit  ## Default: run unit tests only (< 10 s)

test-unit:
	uv run pytest -m unit -x

test-contract:
	uv run pytest -m contract -x

test-integration:
	uv run pytest -m integration -x

test-perf:
	uv run pytest -m perf -x

test-all:
	uv run pytest -x

# ── Code quality ─────────────────────────────────────────────────────
lint: typecheck  ## Lint and type-check
	uv run ruff check src tests

fmt:  ## Auto-format and fix
	uv run ruff format src tests
	uv run ruff check --fix src tests

typecheck:
	uv run mypy src

# ── Infrastructure ───────────────────────────────────────────────────
up:  ## Start ClickHouse, Redis, Redpanda
	docker compose up -d --wait

down:  ## Stop services (keep data)
	docker compose down

clean:  ## Stop services and delete volumes
	docker compose down -v
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type d -name .pytest_cache -exec rm -rf {} +
	find . -type d -name .mypy_cache -exec rm -rf {} +
