#!/usr/bin/env bash
#
# Start everything GalaxyHire needs, in the right order, and wait until each part is actually
# ready before starting the next.
#
# Ordering is not cosmetic. The API calls the corpus, and the corpus needs Postgres, so starting
# them together means the API comes up talking to something that is not listening yet and reports
# the corpus as unavailable until you restart it. Each step here waits on a health check rather
# than a sleep, because "how long does Postgres take to accept connections" has no fixed answer.
#
#   ./scripts/start.sh          serve the built UI from the API on :8000   (what you want normally)
#   ./scripts/start.sh --dev    Vite dev server on :1420 with hot reload
#   ./scripts/start.sh --stop   stop everything this script started
#
# Logs go to .run/*.log. Ctrl-C stops everything; so does --stop from another terminal.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
RUN_DIR=".run"
mkdir -p "$RUN_DIR"

CORPUS_PORT=8100
APP_PORT=8000
WEB_PORT=1420

info() { printf '\033[36m▸\033[0m %s\n' "$*"; }
ok()   { printf '\033[32m✓\033[0m %s\n' "$*"; }
die()  { printf '\033[31m✗\033[0m %s\n' "$*" >&2; exit 1; }

stop_all() {
  for f in "$RUN_DIR"/*.pid; do
    [ -e "$f" ] || continue
    pid=$(cat "$f" 2>/dev/null || true)
    # Negative pid signals the whole group: uvicorn and npm both spawn children, and killing only
    # the parent leaves those holding the port, so the next start fails with "address in use".
    [ -n "$pid" ] && kill -- "-$pid" 2>/dev/null || true
    [ -n "$pid" ] && kill "$pid" 2>/dev/null || true
    rm -f "$f"
  done
  ok "stopped"
}

if [ "${1:-}" = "--stop" ]; then stop_all; exit 0; fi
trap stop_all EXIT INT TERM

# Refuse to start on top of a running instance rather than producing a confusing port clash.
for port in "$CORPUS_PORT" "$APP_PORT"; do
  if lsof -ti ":$port" >/dev/null 2>&1; then
    die "port $port is already in use — run './scripts/start.sh --stop' first"
  fi
done

wait_for() { # url, label, attempts
  local url=$1 label=$2 tries=${3:-90}
  for _ in $(seq 1 "$tries"); do
    curl -sf "$url" >/dev/null 2>&1 && { ok "$label ready"; return 0; }
    sleep 1
  done
  die "$label did not become ready — see $RUN_DIR/"
}

spawn() { # name, command...
  local name=$1; shift
  setsid "$@" > "$RUN_DIR/$name.log" 2>&1 &
  echo $! > "$RUN_DIR/$name.pid"
}

info "starting postgres + redis"
docker compose up -d >/dev/null
# `docker compose up` returns once containers are created, which is well before Postgres accepts
# connections. The healthcheck is the real signal.
for _ in $(seq 1 60); do
  docker compose exec -T postgres pg_isready -U galaxy >/dev/null 2>&1 && break
  sleep 1
done
docker compose exec -T postgres pg_isready -U galaxy >/dev/null 2>&1 || die "postgres never became ready"
ok "database ready"

info "applying migrations"
(cd services/corpus && uv run python -c \
  "import asyncio; from galaxy.db.engine import migrate; asyncio.run(migrate())") >/dev/null 2>&1 \
  || die "migrations failed"
ok "schema up to date"

info "starting corpus on :$CORPUS_PORT"
spawn corpus env -C services/corpus uv run uvicorn galaxy.api.app:app --host 127.0.0.1 --port "$CORPUS_PORT"
wait_for "http://127.0.0.1:$CORPUS_PORT/health" "corpus"

if [ "${1:-}" = "--dev" ]; then
  info "starting API on :$APP_PORT (UI will be served by Vite)"
else
  info "building the UI"
  # The API serves apps/web/dist and silently skips the mount when it is absent, so without this
  # the app starts fine and / returns 404 — which reads as a broken install.
  (cd apps/web && npm run build) >"$RUN_DIR/build.log" 2>&1 || die "UI build failed — see $RUN_DIR/build.log"
  ok "UI built"
  info "starting API + UI on :$APP_PORT"
fi

spawn api env -C apps/api uv run python main.py --port "$APP_PORT"
wait_for "http://127.0.0.1:$APP_PORT/health" "API"

if [ "${1:-}" = "--dev" ]; then
  info "starting Vite on :$WEB_PORT"
  spawn web env -C apps/web npm run dev
  wait_for "http://127.0.0.1:$WEB_PORT/" "web"
  URL="http://127.0.0.1:$WEB_PORT"
else
  URL="http://127.0.0.1:$APP_PORT"
fi

echo
ok "GalaxyHire is up  →  $URL"
echo "   logs: $RUN_DIR/*.log     stop: ./scripts/start.sh --stop  (or Ctrl-C)"
echo
# Hold the terminal so Ctrl-C reaches the trap and stops the services. Without this the script
# exits, the trap fires immediately, and everything shuts down the moment it finished starting.
while true; do sleep 3600; done
