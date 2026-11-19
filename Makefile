.DEFAULT_GOAL := help
SHELL := /bin/bash

# The seed pins "now" for generated dates; the API must use the same instant so that the
# refund and reopen windows line up with the data. Override with `make serve FROZEN_TIME=`
# (empty) to run on the real clock.
FROZEN_TIME ?= 2026-06-01T12:00:00+00:00
DATABASE_URL ?= sqlite:///./helpdesk.db
PORT ?= 8000
# MCP server: which tool design to run, and extra flags (for example MCP_ARGS="--tool-count 25").
VARIANT ?= per-endpoint
MCP_ARGS ?=
export DATABASE_URL

.PHONY: help install lint fmt typecheck test cov check serve seed migrate key openapi openapi-check mcp inspector tool-sizes docker clean

help: ## Show this help
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-14s %s\n", $$1, $$2}'

install: ## Create the virtualenv and install all extras
	uv sync --all-extras

lint: ## Ruff lint and format check
	uv run ruff check .
	uv run ruff format --check .

fmt: ## Auto-fix lint issues and format
	uv run ruff check --fix .
	uv run ruff format .

typecheck: ## mypy --strict
	uv run mypy

test: ## Run the test suite with a coverage gate (90% overall, 90% for the MCP server)
	uv run pytest --cov --cov-report=term-missing:skip-covered
	uv run coverage report --include="src/helpdesk_mcp/*" --fail-under=90 --skip-covered

cov: ## Test suite with an HTML coverage report
	uv run pytest --cov --cov-report=html
	@echo "Open htmlcov/index.html"

check: lint typecheck test openapi-check ## Everything CI runs, except Docker

migrate: ## Apply database migrations
	uv run helpdesk migrate

seed: migrate ## Migrate, then load synthetic data (replaces existing rows)
	uv run helpdesk seed --reset

key: ## Print a new admin API key linked to lead agent A-0002 (run after `make seed`)
	@uv run helpdesk create-api-key --name local --scopes admin --agent A-0002

serve: ## Run the API on $(PORT) (uses FROZEN_TIME to match the seed)
	HELPDESK_FROZEN_TIME=$(FROZEN_TIME) uv run helpdesk serve --port $(PORT)

openapi: ## Regenerate openapi.json
	uv run python scripts/export_openapi.py

openapi-check: ## Fail if openapi.json is stale
	uv run python scripts/export_openapi.py --check

mcp: ## Run the MCP server over stdio (VARIANT=, MCP_ARGS=; needs HELPDESK_MCP_API_KEY)
	uv run helpdesk-mcp --variant $(VARIANT) $(MCP_ARGS)

inspector: ## Open the MCP Inspector on the server (needs npx and HELPDESK_MCP_API_KEY)
	npx @modelcontextprotocol/inspector uv run helpdesk-mcp --variant $(VARIANT) $(MCP_ARGS)

tool-sizes: ## Regenerate docs/tool-list-sizes.md (tool counts and approximate tokens per variant)
	uv run python scripts/mcp_token_report.py -o docs/tool-list-sizes.md

docker: ## Build the API image
	docker build -t mcp-helpdesk-lab:dev .

clean: ## Remove caches, coverage output and local databases
	rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage helpdesk.db
