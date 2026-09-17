# First 3.13+ interpreter found; override with `make PYTHON=/path/to/python`.
PYTHON ?= $(shell command -v python3.13 || command -v python3.14 || command -v python3)
VENV   := backend/.venv
BIN    := $(VENV)/bin
PORT   ?= 8787

ENV_FILE  := backend/.env
ENV_FLAG  := $(if $(wildcard $(ENV_FILE)),--env-file $(ENV_FILE),)

.PHONY: setup dev test clean bundle vendor dist worker-dev deploy secret

## Create the venv and install the backend in editable mode.
setup:
	$(PYTHON) -m venv $(VENV)
	$(BIN)/pip install --quiet --upgrade pip
	$(BIN)/pip install --quiet -e "backend[local,dev]"
	@test -f $(ENV_FILE) || (cp backend/.env.example $(ENV_FILE) && \
		echo "→ created $(ENV_FILE); add your ANTHROPIC_API_KEY")
	@echo "ready — run: make dev"

## Serve the API and the frontend on one origin, as Cloudflare will.
dev:
	$(BIN)/uvicorn collaborate.local_server:app --reload \
		--reload-dir backend/src --reload-dir frontend \
		--port $(PORT) $(ENV_FLAG)

# Run from backend/ so pytest picks up the config in backend/pyproject.toml.
test:
	cd backend && .venv/bin/pytest -q

## Bake prompts and the sample list into a module for the Worker bundle.
bundle:
	uv run --quiet --with pyyaml python tools/build_bundle.py

## Copy the shared package into the Worker source tree. Cloudflare bundles every
## .py file under the entrypoint's directory, so the Worker imports the same
## modules the dev server does - no wheel, and no lock file pinning a hash that
## changes on every backend edit. The dev shell and the httpx transport are
## omitted: the Worker imports neither, and both need packages Pyodide lacks.
##
## Staged then swapped, never built in place: `pywrangler dev` watches this tree
## and will rebuild from a half-copied directory, crashing with a
## ModuleNotFoundError for whichever module had not landed yet.
vendor: bundle
	rm -rf worker/src/.collaborate.tmp worker/src/collaborate
	cp -R backend/src/collaborate worker/src/.collaborate.tmp
	rm -rf worker/src/.collaborate.tmp/__pycache__
	rm -f worker/src/.collaborate.tmp/local_server.py \
		worker/src/.collaborate.tmp/transport_httpx.py
	mv worker/src/.collaborate.tmp worker/src/collaborate
	@echo "→ worker/src/collaborate ($$(ls worker/src/collaborate/*.py | wc -l | tr -d ' ') modules)"

## Assemble the static assets the Worker serves: frontend + sample SVGs.
dist:
	rm -rf worker/public
	mkdir -p worker/public/samples
	cp -R frontend/. worker/public/
	cp samples/*.svg worker/public/samples/
	@echo "→ worker/public ($$(find worker/public -type f | wc -l | tr -d ' ') files)"

## Run the Worker locally under Pyodide, as Cloudflare will. Needs worker/.dev.vars.
worker-dev: vendor dist
	cd worker && uv run pywrangler dev --port 8788

## Deploy to Cloudflare. Needs `wrangler login` and the ANTHROPIC_API_KEY secret.
deploy: vendor dist
	cd worker && uv run pywrangler deploy

## Upload backend/.env's key as the Worker's secret. Idempotent - run it any
## time the deployed /api/health reports the key is not set. Piped via stdin so
## the key never lands in a command line or in shell history.
secret:
	@grep -m1 '^ANTHROPIC_API_KEY=' $(ENV_FILE) | cut -d= -f2- | tr -d '\n' \
		| (cd worker && npx --yes wrangler secret put ANTHROPIC_API_KEY)

clean:
	rm -rf $(VENV) backend/src/*.egg-info worker/public backend/dist backend/build \
		worker/src/collaborate backend/src/collaborate/bundle.py
