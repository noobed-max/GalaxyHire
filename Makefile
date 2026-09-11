.DEFAULT_GOAL := help
SHELL := /bin/bash

CORPUS := services/corpus
API    := apps/api
WEB    := apps/web
EXT    := apps/extension
SCRAPER := services/scraper-node

# Ports. APP_PORT serves both the API and the built UI (same origin), which is the
# default local setup described in docs/getting-started.md.
APP_PORT    ?= 8000
CORPUS_PORT ?= 8100

.PHONY: help
help: ## show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
	  | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

# ── infrastructure ────────────────────────────────────────────────────────────
.PHONY: infra-up infra-down migrate
infra-up: ## start postgres + redis
	docker compose up -d
	@echo "waiting for postgres..." && until docker compose exec -T postgres pg_isready -U galaxy >/dev/null 2>&1; do sleep 1; done && echo "ready"

infra-down: ## stop postgres + redis
	docker compose down

migrate: ## apply corpus schema
	cd $(CORPUS) && uv run python -c "import asyncio; from galaxy.db.engine import migrate; asyncio.run(migrate())"

# ── install ───────────────────────────────────────────────────────────────────
.PHONY: setup install update
setup: ## one-command install: toolchains, Docker infra, deps, schema, builds
	./scripts/install.sh

install: ## install every workspace's deps
	cd $(CORPUS) && uv sync --extra embed
	cd $(API) && uv sync
	cd $(WEB) && npm install
	cd $(EXT) && npm install
	cd $(SCRAPER) && npm install

update: ## update the installed app to the latest source and restart it
	./scripts/update.sh $(ARGS)

# ── run ───────────────────────────────────────────────────────────────────────
.PHONY: up up-dev down corpus api web ext-build
corpus: ## run the corpus service (retrieval + ingest)
	cd $(CORPUS) && uv run uvicorn galaxy.api.app:app --host 127.0.0.1 --port $(CORPUS_PORT) --reload

# Run through main.py, not `uvicorn api.app:create_app --factory`. `create_app` takes required
# keyword-only arguments (lifespan, token_getter, started_at), so uvicorn's factory mode cannot
# call it and the target died at startup with a TypeError. main.py is also what the desktop shell
# runs, so this keeps one entrypoint rather than two that can drift.
#
# It prints `JHM_TOKEN=…` and `PORT:…` on stdout before serving; the browser client picks those up
# via /bootstrap, the desktop shell over Tauri IPC.
#
# No --reload here, unlike the corpus target: main.py reserves the port itself and hands uvicorn an
# already-bound socket, and uvicorn's reloader needs to re-import an app by string in a subprocess.
# For UI work use `make web`, which hot-reloads the frontend against this API.
up: ## start everything (db, corpus, API+UI) and wait until each is ready
	./scripts/start.sh

up-dev: ## same, but with the Vite dev server and hot reload on :1420
	./scripts/start.sh --dev

down: ## stop everything `make up` started
	./scripts/start.sh --stop

api: ## run the app API alone (also serves the built UI at / — run `make build` first)
	cd $(API) && uv run python main.py --port $(APP_PORT)

web: ## run the UI dev server (proxies to the app API)
	cd $(WEB) && bun run dev

ext-build: ## build the MV3 extension into apps/extension/dist
	cd $(EXT) && npm run build

# ── scraping ──────────────────────────────────────────────────────────────────
CAREER_OPS_HOME ?= $(abspath ../career-ops)

.PHONY: vendor-check
vendor-check: ## fail if the vendored career-ops provider tree drifted from upstream
# The connector set is a vendored snapshot of career-ops providers. Re-vendor with:
#   rsync -a --exclude README.md $$CAREER_OPS_HOME/providers/ \
#     services/scraper-node/vendor/career-ops-providers/
# and record the upstream SHA in vendor/career-ops-providers/VENDOR_SOURCE.
	@src=$(CAREER_OPS_HOME)/providers; \
	if [ ! -d "$$src" ]; then echo "vendor-check: career-ops checkout not found at $$src — skipping"; exit 0; fi; \
	if diff -r -q --exclude README.md --exclude VENDOR_SOURCE --exclude arbeitsagentur.mjs --exclude vdab.mjs services/scraper-node/vendor/career-ops-providers "$$src" >/dev/null; \
	then echo "vendor-check: tree matches career-ops"; \
	else echo "vendor-check: VENDOR DRIFT — re-vendor per this target's comment"; exit 1; fi
# No `make scrape` target is provided. Searching in the app is what collects: the phrase you type
# is sent to the job boards, and filters apply afterwards over what came back. A second entry
# point meant two ways to fill the corpus that could disagree about what had been collected,
# and the freshness check that keeps searching fast only sees runs the app started.
#
# For debugging a single connector, call the harness directly:
#   cd services/scraper-node && npm start -- --role "x" --only greenhouse --dry-run

# ── tests ─────────────────────────────────────────────────────────────────────
.PHONY: test test-corpus test-api test-web test-ext test-scraper test-contract lint typecheck build
# `test` runs typecheck and the production build everywhere, not just the unit suites.
#
# vitest transpiles without typechecking, so a type error in a test file passed `npm test` and only
# surfaced when `npm run build` ran tsc — after the suite had already reported green. Anything the
# release build would reject has to fail here too, or "tests pass" means less than it appears to.
test: test-corpus test-api test-contract test-scraper test-web test-ext vendor-check build ## every suite + typecheck + build

test-corpus:
	cd $(CORPUS) && uv run pytest -q && uv run ruff check .

test-api:
	cd $(API) && uv run pytest -q && uv run ruff check .

test-contract:
	cd packages/contract && npm run typecheck && npm test

test-scraper:
	cd $(SCRAPER) && npm run typecheck && npm test

test-web:
	cd $(WEB) && npm run typecheck && npm run test

test-ext:
	cd $(EXT) && npx tsc --noEmit && npm test

typecheck: ## typecheck every TypeScript workspace
	cd packages/contract && npm run typecheck
	cd $(SCRAPER) && npm run typecheck
	cd $(WEB) && npm run typecheck
	cd $(EXT) && npx tsc --noEmit

build: ## production build of both bundles — catches what vitest cannot
	cd $(WEB) && npm run build
	cd $(EXT) && npm run build

lint: ## ruff + eslint
	cd $(CORPUS) && uv run ruff check .
	cd $(API) && uv run ruff check .
	cd $(WEB) && npm run lint

# ── reclaiming disk ───────────────────────────────────────────────────────────
#
# This project is a heavy disk user: six workspaces of dependencies, ONNX embedding models, two
# browser engines for the anti-detect scrapers, and a corpus that grows with every scrape. On a
# machine whose root partition is small, the package-manager caches are what fill it — not the
# checkout and not Docker, whose data root may well already live elsewhere.
#
# Check where the space actually is before deleting anything:
#     df -h /                       # is the root disk the one under pressure?
#     du -sh ~/.cache/* ~/.npm      # usually the answer
#     docker info | grep 'Root Dir' # often NOT /var/lib/docker

.PHONY: clean clean-cache clean-all
clean: ## remove build output (safe; regenerated by `make build`)
	rm -rf $(WEB)/dist $(EXT)/dist packages/contract/dist
	rm -rf $(API)/tests/.scratch-templates
	find . -type d -name __pycache__ -not -path './*/node_modules/*' -prune -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -not -path './*/node_modules/*' -prune -exec rm -rf {} + 2>/dev/null || true

clean-cache: ## reclaim shared tool caches (safe; re-downloads on next install)
	-uv cache clean
	-npm cache clean --force
	-docker builder prune -af

# Deliberately NOT part of `clean-all`: the corpus volume. It holds thousands of scraped jobs that
# cost hours of connector runs to rebuild, and `docker compose down -v` would delete it silently.
# If you really mean it: docker compose down -v
clean-all: clean clean-cache ## build output + caches, but never the scraped corpus
	rm -rf $(WEB)/node_modules $(EXT)/node_modules $(SCRAPER)/node_modules packages/contract/node_modules
	rm -rf $(CORPUS)/.venv $(API)/.venv
	@echo "run 'make install' to restore"
