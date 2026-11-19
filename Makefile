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

.PHONY: help install lint fmt typecheck test cov check serve seed migrate key openapi openapi-check mcp inspector tool-sizes bench-check bench-pilot bench-dry bench-report demo demo-render demo-check docker clean

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

test: ## Run the test suite with coverage gates (90% overall, for the MCP server and for bench)
	uv run pytest --cov --cov-report=term-missing:skip-covered
	uv run coverage report --include="src/helpdesk_mcp/*" --fail-under=90 --skip-covered
	uv run coverage report --include="bench/*" --fail-under=90 --skip-covered
	uv run coverage report --include="demo/*" --fail-under=90 --skip-covered

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

bench-check: ## Prove every benchmark task is solvable (scripted oracle) and fails when nothing is done
	uv run python -m bench check-tasks

bench-pilot: ## Run the pilot (real Claude Code runs on your login, haiku, about 27 runs) then the report
	uv run python -m bench run --matrix pilot
	uv run python -m bench report

bench-dry: ## Full pipeline with a fake claude (no model, no cost); results go to bench/.runs/dry-run-results
	uv run python -m bench run --matrix pilot --dry-run --workers 3

bench-report: ## Rebuild summary.csv, summary.md and the charts from bench/results
	uv run python -m bench report

demo-check: ## Prove every demo task is solvable (scripted oracle) and fails when nothing is done
	uv run python -m demo check

demo: ## Run the five demo tasks under both setups (10 real Claude Code runs on your login, sonnet)
	uv run python -m demo run

demo-render: ## Rebuild demo/screenshots from demo/results (needs Playwright, see docs/demo.md)
	uv run python -m demo render

docker: ## Build the API image
	docker build -t mcp-helpdesk-lab:dev .

clean: ## Remove caches, coverage output and local databases
	rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage helpdesk.db
