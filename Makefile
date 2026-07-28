.DEFAULT_GOAL := help

.PHONY: help setup sync test test-fast cov cov-open lint format typecheck check clean

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

sync:
	uv sync

setup: sync

test:
	uv run pytest

test-fast:
	uv run pytest --no-cov -q

cov: test

lint:
	uv run ruff check src tests

format:
	uv run ruff check --fix src tests
	uv run ruff format src tests

typecheck:
	uv run pyright

check: lint typecheck test

clean:
	rm -rf htmlcov .coverage coverage.json
	rm -rf .pytest_cache .ruff_cache .mypy_cache dist build
	find . -type d -name __pycache__ -prune -exec rm -rf {} +