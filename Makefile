.PHONY: help install dev lint typecheck test test-cov clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

install: ## Install production dependencies
	uv pip install -e .

dev: ## Install with dev dependencies
	uv pip install -e ".[dev]"

lint: ## Run ruff linter + formatter
	ruff check . --fix
	ruff format .

typecheck: ## Run mypy type checker
	mypy codemesh/

test: ## Run tests
	pytest tests/ -v

test-cov: ## Run tests with coverage
	pytest tests/ -v --cov=codemesh --cov-report=term-missing

clean: ## Clean build artifacts
	rm -rf build/ dist/ *.egg-info .pytest_cache .mypy_cache .ruff_cache
	find . -type d -name __pycache__ -exec rm -rf {} +
