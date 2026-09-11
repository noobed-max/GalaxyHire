#!/usr/bin/env bash
#
# GalaxyHire updater.
#
# Updates an existing installation to the latest source and restarts it with the new code:
#   1. stops the running stack (the .run supervisor or the systemd user service)
#   2. refreshes the checkout (git pull for a git clone, archive overlay otherwise)
#   3. reinstalls dependencies, applies migrations, and rebuilds the UI/extension
#   4. restarts in the mode it was running before
#
# Your data is preserved: .env files and .run state are not part of the source archive, and the
# profile/settings database lives outside the checkout.
#
# Usage (from the repository root):
#   ./scripts/update.sh                 update, then restart only if it was running
#   ./scripts/update.sh --start         update, then start even if it was stopped
#   ./scripts/update.sh --no-start      update without starting
#   ./scripts/update.sh --dev           update, then restart with the hot-reload dev server
#   ./scripts/update.sh --ref v1.5.0    update to a specific branch or tag
#   ./scripts/update.sh --dry-run       print every action without changing the system
#
# One-liners (no checkout needed):
#   curl -fsSL https://raw.githubusercontent.com/noobed-max/GalaxyHire/master/scripts/update.sh | bash
#   curl -fsSL .../update.sh | bash -s -- --start
#
# Run ./scripts/update.sh --help for all options.
set -Eeuo pipefail

# ── paths ─────────────────────────────────────────────────────────────────────
# Resolve the installation the same way uninstall.sh does: an explicit GALAXYHIRE_DIR wins, then
# this script's checkout, then the default bootstrap location.
SCRIPT_PATH="${BASH_SOURCE[0]:-}"
SCRIPT_DIR=''
if [ -n "${SCRIPT_PATH}" ] && [ -f "${SCRIPT_PATH}" ]; then
  SCRIPT_DIR="$(cd "$(dirname "${SCRIPT_PATH}")" && pwd)"
fi
CHECKOUT=''
if [ -n "${GALAXYHIRE_DIR:-}" ]; then
  CHECKOUT="${GALAXYHIRE_DIR}"
elif [ -n "${SCRIPT_DIR}" ]; then
  CHECKOUT="$(cd "${SCRIPT_DIR}/.." 2>/dev/null && pwd || true)"
fi
if [ -z "${CHECKOUT}" ]; then
  CHECKOUT="${HOME}/GalaxyHire"
fi

REPO_SLUG="${GALAXYHIRE_REPO:-noobed-max/GalaxyHire}"
REF="${GALAXYHIRE_REF:-master}"
REF_EXPLICIT=0
[ -n "${GALAXYHIRE_REF:-}" ] && REF_EXPLICIT=1
RUN_DIR="${CHECKOUT}/.run"
LOG_FILE="${RUN_DIR}/update-$(date +%Y%m%d-%H%M%S).log"

# ── options ───────────────────────────────────────────────────────────────────
START_MODE='auto' # auto | yes | no
DEV_MODE=0
DRY_RUN=0
ALLOW_ROOT=0

if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
  C_RESET=$'\033[0m'; C_BOLD=$'\033[1m'; C_DIM=$'\033[2m'
  C_RED=$'\033[31m'; C_GREEN=$'\033[32m'; C_YELLOW=$'\033[33m'; C_CYAN=$'\033[36m'
else
  C_RESET=''; C_BOLD=''; C_DIM=''; C_RED=''; C_GREEN=''; C_YELLOW=''; C_CYAN=''
fi

usage() {
  cat <<'EOF'
GalaxyHire updater

Usage: ./scripts/update.sh [options]

Stops the running app, refreshes the checkout to the latest source, reinstalls
dependencies, applies migrations, rebuilds the UI/extension, and restarts it in
the mode it was running before. Configuration and data are preserved.

Options:
  -y, --yes        accepted for interface parity; the updater never prompts
      --start      start afterwards even if the app was stopped
      --no-start   update without starting the app
      --dev        restart with the hot-reload dev server (implies --start)
      --ref REF    branch or tag to update to (default: master)
      --dry-run    print what would happen without changing anything
      --allow-root permit running as root (not recommended)
  -h, --help       show this help

Environment:
  GALAXYHIRE_DIR          installation to update (default: this script's checkout, else ~/GalaxyHire)
  GALAXYHIRE_REF          branch or tag to update to (default: master)
  GALAXYHIRE_REPO         owner/repo to download from (default: noobed-max/GalaxyHire)
  GALAXYHIRE_ARCHIVE_URL  full source tarball URL override (for forks or mirrors)

Exit status: 0 on success, non-zero on the first failed step.
Full output is mirrored to .run/update-*.log.
EOF
}

parse_args() {
  while (($#)); do
    case "$1" in
      -y|--yes) : ;; # interface parity with install.sh; the updater never prompts
      --start) START_MODE=yes ;;
      --no-start) START_MODE=no ;;
      --dev) DEV_MODE=1; START_MODE=yes ;;
      --ref)
        [ -n "${2:-}" ] || die "--ref needs a value"
        REF="$2"; REF_EXPLICIT=1; shift
        ;;
      --dry-run) DRY_RUN=1 ;;
      --allow-root) ALLOW_ROOT=1 ;;
      -h|--help) usage; exit 0 ;;
      *) printf 'Unknown option: %s (try --help)\n' "$1" >&2; exit 2 ;;
    esac
    shift
  done
}

# ── logging ───────────────────────────────────────────────────────────────────
_log() { { printf '[%s] %s\n' "$(date '+%H:%M:%S')" "$*" >>"${LOG_FILE}"; } 2>/dev/null || true; }
info() { printf '%s\n' "  ${C_CYAN}▸${C_RESET} $*"; _log "INFO  $*"; }
ok()   { printf '%s\n' "  ${C_GREEN}✓${C_RESET} $*"; _log "OK    $*"; }
warn() { printf '%s\n' "  ${C_YELLOW}!${C_RESET} $*" >&2; _log "WARN  $*"; }
die()  { printf '%s\n' "  ${C_RED}✗${C_RESET} $*" >&2; _log "ERROR $*"; exit 1; }

on_error() {
  local code=$?
  printf '\n%s\n' "${C_RED}${C_BOLD}Update failed (exit ${code}).${C_RESET}" >&2
  printf '%s\n' "  Your data and configuration are untouched; start it again with:" >&2
  printf '%s\n' "    bash ${CHECKOUT}/scripts/start.sh" >&2
  printf '%s\n' "  Log: ${LOG_FILE}" >&2
  exit "$code"
}
trap on_error ERR

run() {
  if (( DRY_RUN )); then
    printf '      %s[dry-run]%s %s\n' "${C_DIM}" "${C_RESET}" "$*"
    _log "DRY   $*"
    return 0
  fi
  _log "RUN   $*"
  "$@"
}

have() { command -v "$1" >/dev/null 2>&1; }

# ── preflight ─────────────────────────────────────────────────────────────────
preflight() {
  if [ "$(id -u)" -eq 0 ] && (( ! ALLOW_ROOT )); then
    die "Run this script as a normal user. Override with --allow-root."
  fi
  if [ ! -f "${CHECKOUT}/docker-compose.yml" ] || [ ! -d "${CHECKOUT}/services/corpus" ]; then
    die "No GalaxyHire installation found at ${CHECKOUT}.
    Install it first:
      curl -fsSL https://raw.githubusercontent.com/${REPO_SLUG}/master/scripts/install.sh | bash -s -- --start
    Or point at an existing install:
      GALAXYHIRE_DIR=/path/to/GalaxyHire bash scripts/update.sh"
  fi
  if (( DRY_RUN )); then
    info "Dry run: no changes will be made."
  fi
  info "Installation: ${CHECKOUT}"
  info "Updating to ${REF} (${REPO_SLUG})"
}

# ── 1. detect + stop the running app ──────────────────────────────────────────
pid_alive() { # pid file
  local pid=''
  pid="$(cat "$1" 2>/dev/null || true)"
  [ -n "${pid}" ] && kill -0 "${pid}" 2>/dev/null
}

detect_state() {
  PRIOR_RUNNING=0
  PRIOR_DEV=0
  PRIOR_SERVICE=0
  if pid_alive "${RUN_DIR}/supervisor" || pid_alive "${RUN_DIR}/api.pid" || pid_alive "${RUN_DIR}/corpus.pid"; then
    PRIOR_RUNNING=1
  fi
  if pid_alive "${RUN_DIR}/web.pid"; then
    PRIOR_DEV=1
  fi
  if have systemctl && systemctl --user is-active --quiet galaxyhire 2>/dev/null; then
    PRIOR_SERVICE=1
  fi
}

stop_app() {
  if (( PRIOR_SERVICE )); then
    info "Stopping the systemd user service..."
    if (( DRY_RUN )); then
      printf '      %s[dry-run]%s systemctl --user stop galaxyhire\n' "${C_DIM}" "${C_RESET}"
      return 0
    fi
    systemctl --user stop galaxyhire
    ok "service stopped"
  elif (( PRIOR_RUNNING )); then
    info "Stopping the running GalaxyHire stack..."
    run bash "${CHECKOUT}/scripts/start.sh" --stop
    (( DRY_RUN )) || ok "stopped"
  else
    info "GalaxyHire is not running; nothing to stop"
  fi
}

# ── 2. refresh the checkout ───────────────────────────────────────────────────
download_archive() { # url, target
  local url=$1 target=$2
  have curl || have wget || die "curl or wget is required to download updates."
  local tmp base
  tmp="$(mktemp -d "${TMPDIR:-/tmp}/galaxyhire-update.XXXXXX")"
  if have curl; then
    curl -fsSL "${url}" | tar -xz -C "${tmp}" || die "Could not download ${url}"
  else
    wget -qO- "${url}" | tar -xz -C "${tmp}" || die "Could not download ${url}"
  fi
  base="$(find "${tmp}" -mindepth 1 -maxdepth 1 -type d -print -quit)"
  [ -n "${base}" ] || die "The downloaded archive did not contain a repository directory."
  mkdir -p "${target}"
  # Overlay the code. The archive only carries tracked files, so .env files, .run state, and
  # installed dependencies in the target are left in place.
  cp -Rp "${base}/." "${target}/"
  chmod +x "${target}"/scripts/*.sh 2>/dev/null || true
  rm -rf "${tmp}"
}

refresh_checkout() {
  if [ -d "${CHECKOUT}/.git" ]; then
    # A git clone is the developer workflow; use git so local history stays coherent.
    if ! git -C "${CHECKOUT}" diff-index --quiet HEAD -- 2>/dev/null; then
      die "The git checkout at ${CHECKOUT} has uncommitted changes.
    Commit or stash them first, or update the archive install instead:
      GALAXYHIRE_DIR=\${HOME}/GalaxyHire bash scripts/update.sh"
    fi
    info "Pulling the latest code with git..."
    if (( REF_EXPLICIT )); then
      run git -C "${CHECKOUT}" fetch origin "${REF}"
      run git -C "${CHECKOUT}" checkout -B "${REF}" FETCH_HEAD
    else
      run git -C "${CHECKOUT}" pull --ff-only
    fi
  else
    local url="${GALAXYHIRE_ARCHIVE_URL:-https://codeload.github.com/${REPO_SLUG}/tar.gz/refs/heads/${REF}}"
    info "Downloading the latest code (${REF})..."
    if (( DRY_RUN )); then
      printf '      %s[dry-run]%s download %s and overlay %s\n' "${C_DIM}" "${C_RESET}" "${url}" "${CHECKOUT}"
    else
      download_archive "${url}" "${CHECKOUT}"
    fi
  fi
  (( DRY_RUN )) || ok "Code updated"
}

# ── 3. install dependencies, migrate, rebuild ─────────────────────────────────
apply_update() {
  info "Applying dependencies, migrations, and builds (this can take a few minutes)..."
  if (( DRY_RUN )); then
    printf '      %s[dry-run]%s bash %s/scripts/install.sh --yes\n' "${C_DIM}" "${C_RESET}" "${CHECKOUT}"
    return 0
  fi
  run bash "${CHECKOUT}/scripts/install.sh" --yes
  ok "Install steps complete"
}

# ── 4. restart in the previous mode ───────────────────────────────────────────
wait_http() { # url, label, tries
  local url=$1 label=$2 tries=${3:-180}
  for _ in $(seq 1 "${tries}"); do
    curl -fsS "${url}" >/dev/null 2>&1 && { ok "${label} ready"; return 0; }
    sleep 1
  done
  die "${label} did not become ready — see ${RUN_DIR}/*.log"
}

launch_detached() {
  info "Starting GalaxyHire in the background..."
  ( cd "${CHECKOUT}" && setsid nohup bash scripts/start.sh "$@" >"${RUN_DIR}/start.log" 2>&1 </dev/null & )
}

restart_app() {
  local start_after=0
  case "${START_MODE}" in
    yes) start_after=1 ;;
    no) start_after=0 ;;
    auto) if (( PRIOR_RUNNING || PRIOR_SERVICE )); then start_after=1; fi ;;
  esac

  if (( PRIOR_SERVICE )); then
    info "Restarting the systemd user service..."
    run systemctl --user restart galaxyhire
    (( DRY_RUN )) && return 0
    wait_http http://127.0.0.1:8000/health "GalaxyHire API"
  elif (( start_after )); then
    local start_args=()
    if (( DEV_MODE || PRIOR_DEV )); then start_args+=(--dev); fi
    if (( DRY_RUN )); then
      printf '      %s[dry-run]%s cd %s && setsid bash scripts/start.sh %s\n' \
        "${C_DIM}" "${C_RESET}" "${CHECKOUT}" "${start_args[*]:-}"
      return 0
    fi
    launch_detached "${start_args[@]}"
    sleep 1
    if (( DEV_MODE || PRIOR_DEV )); then
      wait_http http://127.0.0.1:1420/ "web UI"
    else
      wait_http http://127.0.0.1:8000/health "GalaxyHire API"
    fi
  else
    info "Update complete; GalaxyHire is stopped"
    printf '  Start it when ready:  %s\n' "bash ${CHECKOUT}/scripts/start.sh"
  fi
}

# ── main ──────────────────────────────────────────────────────────────────────
main() {
  parse_args "$@"
  preflight

  mkdir -p "${RUN_DIR}"
  _log "GalaxyHire update starting (ref=${REF}, dry-run=${DRY_RUN})"

  detect_state
  if (( PRIOR_SERVICE )); then
    info "Detected: systemd user service"
  elif (( PRIOR_RUNNING )); then
    if (( PRIOR_DEV )); then info "Detected: running stack (dev mode)"; else info "Detected: running stack"; fi
  else
    info "Detected: stopped"
  fi

  stop_app
  refresh_checkout
  apply_update
  restart_app

  if (( DRY_RUN )); then
    printf '\n%s\n' "${C_BOLD}Dry run complete — no changes were made.${C_RESET}"
    printf '%s\n' "  Re-run without --dry-run to update and restart."
  else
    printf '\n%s\n' "${C_GREEN}${C_BOLD}GalaxyHire is up to date.${C_RESET}"
    printf '%s\n' "  Checkout    ${CHECKOUT} (${REF})"
  fi
  printf '%s\n' "  Log         ${LOG_FILE}"
  printf '%s\n' "  Stop        bash ${CHECKOUT}/scripts/start.sh --stop"
  printf '%s\n' "  Docs        ${CHECKOUT}/docs/getting-started.md"
}

main "$@"
