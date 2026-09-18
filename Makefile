# First 3.13+ interpreter found; override with `make PYTHON=/path/to/python`.
PYTHON ?= $(shell command -v python3.13 || command -v python3.14 || command -v python3)
VENV   := backend/.venv
BIN    := $(VENV)/bin
PORT   ?= 8787

ENV_FILE  := backend/.env
## The deployed origin `make verify` probes. Override for another account:
##     make verify WORKER_URL=https://collaborate.you.workers.dev
WORKER_URL ?= https://collaborate.eric-j-witherspoon.workers.dev
ENV_FLAG  := $(if $(wildcard $(ENV_FILE)),--env-file $(ENV_FILE),)

.PHONY: setup dev test clean bundle vendor dist worker-dev deploy secret verify

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

## Deploy to Cloudflare, restore the key if the deploy dropped it, then prove
## the deployed Worker actually works.
##
## ANTHROPIC_API_KEY lives outside wrangler.jsonc, so a deploy that rebuilds the
## Worker's bindings from that file alone deletes it. The Worker stays up and
## every route works except the one that needs a key, which reads as an app bug
## rather than a deploy problem - that is why it kept looking intermittent.
##
## This is the only supported way to deploy. Turn deploy-on-push OFF in the
## Cloudflare dashboard (Worker -> Settings -> Build): a Workers Build has no
## `make secret` to heal itself, so it drops the key on every push and leaves
## the site broken until someone notices. While it is on, this target only
## protects the deploys you run yourself.
deploy: vendor dist
	cd worker && uv run pywrangler deploy
	@if cd worker && npx --yes wrangler secret list 2>/dev/null \
		| grep -q ANTHROPIC_API_KEY; then \
		echo "→ ANTHROPIC_API_KEY still bound"; \
	else \
		echo "→ ANTHROPIC_API_KEY missing after deploy; restoring"; \
		$(MAKE) --no-print-directory secret; \
	fi
	@$(MAKE) --no-print-directory verify

## Ask the deployed Worker whether it is actually healthy.
##
## `secret list` only proves a binding exists; this proves the key behind it
## authenticates. Retries because a freshly uploaded secret takes a few seconds
## to propagate and reports the old state until it does. Run it after any deploy
## you did not make yourself.
verify:
	@echo "→ checking $(WORKER_URL)/api/health" ; \
	for i in 1 2 3 4 5 6; do \
		body=$$(curl -fsS -m 15 "$(WORKER_URL)/api/health" 2>/dev/null) ; \
		case "$$body" in \
			*'"ok": true'*) echo "→ healthy: $$body" ; exit 0 ;; \
		esac ; \
		sleep 5 ; \
	done ; \
	echo "→ UNHEALTHY: $$body" ; \
	echo "   if it reports the key is not set, run: make secret" ; \
	exit 1

## Upload backend/.env's key as the Worker's secret. Idempotent - run it any
## time the deployed /api/health reports the key is not set. Piped via stdin so
## the key never lands in a command line or in shell history.
secret:
	@grep -m1 '^ANTHROPIC_API_KEY=' $(ENV_FILE) | cut -d= -f2- | tr -d '\n' \
		| (cd worker && npx --yes wrangler secret put ANTHROPIC_API_KEY)

clean:
	rm -rf $(VENV) backend/src/*.egg-info worker/public backend/dist backend/build \
		worker/src/collaborate backend/src/collaborate/bundle.py
