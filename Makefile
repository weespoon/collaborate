# First 3.13+ interpreter found; override with `make PYTHON=/path/to/python`.
PYTHON ?= $(shell command -v python3.13 || command -v python3.14 || command -v python3)
VENV   := backend/.venv
BIN    := $(VENV)/bin
PORT   ?= 8787

ENV_FILE  := backend/.env
ENV_FLAG  := $(if $(wildcard $(ENV_FILE)),--env-file $(ENV_FILE),)

.PHONY: setup dev test clean bundle wheel dist worker-dev deploy

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
	$(BIN)/python tools/build_bundle.py

## Build the shared package as a wheel. pywrangler installs into Pyodide with
## --no-build, so the Worker consumes a wheel rather than the source tree.
wheel: bundle
	rm -rf backend/dist
	uv build backend --wheel --out-dir backend/dist
	cd worker && uv sync --reinstall-package collaborate

## Assemble the static assets the Worker serves: frontend + sample SVGs.
dist:
	rm -rf worker/public
	mkdir -p worker/public/samples
	cp -R frontend/. worker/public/
	cp samples/*.svg worker/public/samples/
	@echo "→ worker/public ($$(find worker/public -type f | wc -l | tr -d ' ') files)"

## Run the Worker locally under Pyodide, as Cloudflare will. Needs worker/.dev.vars.
worker-dev: wheel dist
	cd worker && uv run pywrangler dev --port 8788

## Deploy to Cloudflare. Needs `wrangler login` and the ANTHROPIC_API_KEY secret.
deploy: wheel dist
	cd worker && uv run pywrangler deploy

clean:
	rm -rf $(VENV) backend/src/*.egg-info worker/public backend/dist \
		backend/src/collaborate/bundle.py
