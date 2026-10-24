# The everyday commands, so nobody has to remember them (docs/running-locally.md explains each).
#   make setup     install the server, its test tools and the screens (once, in a virtual env)
#   make demo      the whole platform with simulated prices on http://localhost:8000
#   make code      the demo's login code (a new one every 30 s)
#   make check     everything CI checks on a pull request, except the Docker image
#   make test      only the tests; make lint: only style, lint and types
# On Windows, run them inside WSL (Ubuntu).

PYTHON ?= python3

.PHONY: setup demo code check test test-server test-screens lint format image clean

setup:
	$(PYTHON) -m pip install -r requirements-dev.txt
	cd ui && npm ci && npm run build
	cd ui/desktop && npm ci

demo:
	cd ui && npm run build
	$(PYTHON) -m mocktrade.tools.demo --ui ui/dist

code:
	$(PYTHON) -m mocktrade.tools.demo --code

check: lint test

test: test-server test-screens

test-server:
	$(PYTHON) -m pytest -q tests

test-screens:
	cd ui && npm test && npm run build
	cd ui/desktop && npm test

lint:
	ruff check .
	ruff format --check .
	cd ui && npm run typecheck
	cd ui/desktop && npm run typecheck

format:
	ruff format .
	ruff check --fix .

image:
	docker build -t mocktrade:local .
	deploy/smoke.sh mocktrade:local

clean:
	rm -rf .pytest_cache .ruff_cache .mypy_cache build src/*.egg-info ui/dist ui/desktop/dist
	find . -name __pycache__ -type d -prune -not -path "./ui/*" -exec rm -rf {} +
