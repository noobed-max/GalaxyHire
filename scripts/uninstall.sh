#!/usr/bin/env bash
#
# GalaxyHire uninstaller.
#
# Reverses what scripts/install.sh set up:
#   1. stops the running app (systemd user service and/or the .run supervisor)
#   2. removes the Docker containers, network, and database volume
#   3. removes the shell PATH lines the installer added
#   4. optionally deletes local profile/settings data (--purge-data)
#   5. optionally deletes the checkout itself (--remove-files)
#
# Usage:
#   ./scripts/uninstall.sh                      stop everything; keep data and files
#   ./scripts/uninstall.sh --purge-data         also delete profile/settings/leads
#   ./scripts/uninstall.sh --remove-files       also delete the checkout
#   GALAXYHIRE_DIR=~/GalaxyHire ./scripts/uninstall.sh -y --remove-files
#
# One-liner (no checkout needed):
#   curl -fsSL https://raw.githubusercontent.com/noobed-max/GalaxyHire/master/scripts/uninstall.sh | bash -s -- --remove-files
set -Eeuo pipefail

# ── paths ─────────────────────────────────────────────────────────────────────
SCRIPT_PATH="${BASH_SOURCE[0]:-}"
SCRIPT_DIR=''
if [ -n "${SCRIPT_PATH}" ] && [ -f "${SCRIPT_PATH}" ]; then
  SCRIPT_DIR="$(cd "$(dirname "${SCRIPT_PATH}")" && pwd)"
fi
CHECKOUT=''
if [ -n "${GALAXYHIRE_DIR:-}" ]; then
  # An explicit target always wins, so the source checkout can clean up a bootstrap install.
  CHECKOUT="${GALAXYHIRE_DIR}"
elif [ -n "${SCRIPT_DIR}" ]; then
  CHECKOUT="$(cd "${SCRIPT_DIR}/.." 2>/dev/null && pwd || true)"
fi
if [ -z "${CHECKOUT}" ]; then
  CHECKOUT="${HOME}/GalaxyHire"
fi
COMPOSE_FILE="${CHECKOUT}/docker-compose.yml"
RUN_DIR="${CHECKOUT}/.run"

# ── options ───────────────────────────────────────────────────────────────────
ASSUME_YES=0
PURGE_DATA=0
REMOVE_FILES=0
DRY_RUN=0
ALLOW_ROOT=0

if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
  C_RESET=$'\033[0m'; C_BOLD=$'\033[1m'; C_RED=$'\033[31m'
  C_GREEN=$'\033[32m'; C_YELLOW=$'\033[33m'; C_CYAN=$'\033[36m'
else
  C_RESET=''; C_BOLD=''; C_RED=''; C_GREEN=''; C_YELLOW=''; C_CYAN=''
fi

usage() {
  cat <<'EOF'
GalaxyHire uninstaller

Usage: ./scripts/uninstall.sh [options]

Stops GalaxyHire and removes its Docker containers, network, and database volume, its
systemd user service (if installed), and the shell PATH lines added by the installer.
Your profile/settings data and the checkout are kept unless you ask for them.

Options:
  -y, --yes        assume "yes" for prompts (non-interactive)
      --purge-data   also delete local profile/settings/leads data
      --remove-files also delete the checkout this script runs from
      --dry-run      print what would happen without changing anything
      --allow-root   permit running as root (not recommended)
  -h, --help         show this help

Environment:
  GALAXYHIRE_DIR   checkout to operate on (default: this script's checkout, else ~/GalaxyHire)
  JHM_APP_DATA_DIR local data directory override used by the app

Exit status: 0 on success, non-zero on the first failed step.
EOF
}

parse_args() {
  while (($#)); do
    case "$1" in
      -y|--yes) ASSUME_YES=1 ;;
      --purge-data) PURGE_DATA=1 ;;
      --remove-files) REMOVE_FILES=1 ;;
      --dry-run) DRY_RUN=1 ;;
      --allow-root) ALLOW_ROOT=1 ;;
      -h|--help) usage; exit 0 ;;
      *) printf 'Unknown option: %s (try --help)\n' "$1" >&2; exit 2 ;;
    esac
    shift
  done
}

# ── logging ───────────────────────────────────────────────────────────────────
info() { printf '%s\n' "  ${C_CYAN}▸${C_RESET} $*"; }
ok()   { printf '%s\n' "  ${C_GREEN}✓${C_RESET} $*"; }
warn() { printf '%s\n' "  ${C_YELLOW}!${C_RESET} $*" >&2; }
die()  { printf '%s\n' "  ${C_RED}✗${C_RESET} $*" >&2; exit 1; }

on_error() {
  local code=$?
  printf '\n%s\n' "${C_RED}${C_BOLD}Uninstall failed (exit ${code}).${C_RESET}" >&2
  local i
  for ((i = 1; i < ${#FUNCNAME[@]}; i++)); do
    printf '  at %s:%s (%s)\n' "${BASH_SOURCE[$i]##*/}" "${BASH_LINENO[$i - 1]}" "${FUNCNAME[$i]}" >&2
  done
  exit "$code"
}
trap on_error ERR

run() {
  if (( DRY_RUN )); then
    printf '      [dry-run] %s\n' "$*"
    return 0
  fi
  "$@"
}

try_quiet() { # best-effort cleanup: no output, never fails
  if (( DRY_RUN )); then
    printf '      [dry-run] %s\n' "$*"
    return 0
  fi
  "$@" >/dev/null 2>&1 || true
}

have() { command -v "$1" >/dev/null 2>&1; }

confirm() {
  local prompt="$1"
  if (( ASSUME_YES )); then return 0; fi
  if [ ! -t 0 ] && [ ! -r /dev/tty ]; then
    die "Cannot prompt without a terminal; re-run with --yes. (${prompt})"
  fi
  local reply=''
  printf '  %s? %s [y/N] ' "${C_BOLD}" "${prompt}"
  read -r reply < /dev/tty || reply=''
  case "${reply}" in
    [Yy]*) return 0 ;;
    *) return 1 ;;
  esac
}

preflight() {
  if [ "$(id -u)" -eq 0 ] && (( ! ALLOW_ROOT )); then
    die "Run this script as a normal user. Override with --allow-root."
  fi
  info "Checkout: ${CHECKOUT}"
}

# ── 1. stop the app ───────────────────────────────────────────────────────────
stop_systemd_units() {
  have systemctl || return 0
  local unit path found=0
  for unit in galaxyhire.service galaxyhire-api.service galaxyhire-corpus.service; do
    path="${HOME}/.config/systemd/user/${unit}"
    [ -f "${path}" ] || continue
    found=1
    info "Stopping systemd user service ${unit}..."
    run systemctl --user disable --now "${unit}" 2>/dev/null || true
    run rm -f "${path}"
  done
  if (( found )); then
    run systemctl --user daemon-reload 2>/dev/null || true
    run systemctl --user reset-failed 2>/dev/null || true
    ok "systemd user services removed"
  fi
}

stop_checkout_processes() {
  if [ -f "${CHECKOUT}/scripts/start.sh" ] && { [ -f "${RUN_DIR}/supervisor" ] || compgen -G "${RUN_DIR}/*.pid" >/dev/null; }; then
    info "Stopping processes started from ${CHECKOUT}..."
    run bash "${CHECKOUT}/scripts/start.sh" --stop || true
  else
    local f process_id
    for f in "${RUN_DIR}"/*.pid; do
      [ -e "${f}" ] || continue
      process_id=$(cat "${f}" 2>/dev/null || true)
      [ -n "${process_id}" ] && try_quiet kill "${process_id}"
      run rm -f "${f}"
    done
    if [ -f "${RUN_DIR}/supervisor" ]; then
      process_id=$(cat "${RUN_DIR}/supervisor" 2>/dev/null || true)
      [ -n "${process_id}" ] && try_quiet kill "${process_id}"
      run rm -f "${RUN_DIR}/supervisor"
    fi
  fi
  ok "app processes stopped"
}

# ── 2. Docker ─────────────────────────────────────────────────────────────────
SUDO=()
DOCKER=()

discover_docker() {
  DOCKER=()
  have docker || return 1
  if docker info >/dev/null 2>&1; then
    DOCKER=(docker)
    return 0
  fi
  if ((${#SUDO[@]})) && "${SUDO[@]}" -n docker info >/dev/null 2>&1; then
    DOCKER=("${SUDO[@]}" docker)
    return 0
  fi
  return 1
}

remove_docker() {
  if ! have docker; then
    warn "Docker is not installed; skipping container cleanup."
    return 0
  fi
  discover_docker || { warn "The Docker daemon is not reachable; skipping container cleanup."; return 0; }

  if [ -f "${COMPOSE_FILE}" ]; then
    info "Removing containers, network, and database volume..."
    run "${DOCKER[@]}" compose -f "${COMPOSE_FILE}" down -v --remove-orphans || true
  else
    info "Removing containers, network, and database volume by name..."
    local container
    for container in galaxyhire-postgres galaxyhire-redis; do
      try_quiet "${DOCKER[@]}" rm -f "${container}"
    done
    try_quiet "${DOCKER[@]}" volume rm galaxyhire_pgdata
    try_quiet "${DOCKER[@]}" network rm galaxyhire_default
  fi
  # Idempotent belt-and-braces when the checkout was renamed or already gone.
  local container
  for container in galaxyhire-postgres galaxyhire-redis; do
    try_quiet "${DOCKER[@]}" rm -f "${container}"
  done
  try_quiet "${DOCKER[@]}" volume rm galaxyhire_pgdata
  try_quiet "${DOCKER[@]}" network rm galaxyhire_default
  ok "Docker resources removed"
}

# ── 3. shell PATH lines the installer added ───────────────────────────────────
strip_shell_path() {
  local rc tmp
  for rc in "${HOME}/.bashrc" "${HOME}/.zshrc" "${HOME}/.profile"; do
    [ -f "${rc}" ] || continue
    grep -qF '# GalaxyHire toolchain (added by scripts/install.sh)' "${rc}" 2>/dev/null || continue
    info "Removing the toolchain PATH line from ${rc}"
    if (( DRY_RUN )); then continue; fi
    tmp="$(mktemp)"
    awk '
      /# GalaxyHire toolchain \(added by scripts\/install\.sh\)/ { drop=1; next }
      drop && /^export PATH=/ { drop=0; next }
      { drop=0; print }
    ' "${rc}" >"${tmp}"
    mv "${tmp}" "${rc}"
  done
  ok "shell PATH cleaned"
}

# ── 4. local app data (opt-in) ────────────────────────────────────────────────
app_data_dir() {
  if [ -n "${JHM_APP_DATA_DIR:-}" ]; then
    printf '%s\n' "${JHM_APP_DATA_DIR}"
    return
  fi
  local base
  if [ -n "${JHM_APP_DATA_BASE_DIR:-}" ]; then
    base="${JHM_APP_DATA_BASE_DIR}"
  elif [ "$(uname -s)" = Darwin ]; then
    base="${HOME}/Library/Application Support"
  else
    base="${XDG_DATA_HOME:-${HOME}/.local/share}"
  fi
  printf '%s\n' "${base}/JustHireMe"
}

purge_data() {
  local dir
  dir="$(app_data_dir)"
  if [ ! -e "${dir}" ]; then
    info "No local app data at ${dir}"
    return 0
  fi
  if ! confirm "Delete your local profile, settings, and leads at ${dir}?"; then
    info "Keeping local app data."
    return 0
  fi
  info "Deleting ${dir}..."
  run rm -rf "${dir}"
  ok "local app data deleted"
}

# ── 5. checkout (opt-in) ──────────────────────────────────────────────────────
remove_files() {
  if [ ! -d "${CHECKOUT}" ]; then
    info "Checkout not found at ${CHECKOUT}"
    return 0
  fi
  if [ -d "${CHECKOUT}/.git" ]; then
    warn "${CHECKOUT} is a git checkout; refusing to delete it."
    warn "Delete it yourself if you really want that: rm -rf '${CHECKOUT}'"
    return 0
  fi
  if ! confirm "Delete the checkout at ${CHECKOUT}?"; then
    info "Keeping the checkout."
    return 0
  fi
  info "Deleting ${CHECKOUT}..."
  run rm -rf "${CHECKOUT}"
  ok "checkout deleted"
}

print_summary() {
  local port
  for port in 8000 8100; do
    if have lsof && lsof -ti ":${port}" >/dev/null 2>&1; then
      warn "Port ${port} is still in use by another process."
    fi
  done
  printf '\n%s\n' "${C_GREEN}${C_BOLD}GalaxyHire uninstalled.${C_RESET}"
  printf '%s\n' "  Docker containers, network, and database volume: removed"
  printf '%s\n' "  Shell PATH lines and any systemd user service: removed"
  if (( ! PURGE_DATA )); then
    printf '%s\n' "  Local profile/settings data: kept ($(app_data_dir))"
  fi
  if (( ! REMOVE_FILES )) && [ -d "${CHECKOUT}" ]; then
    printf '%s\n' "  Checkout: kept (${CHECKOUT})"
  fi
  printf '%s\n' "  Toolchains installed by the installer (uv, Node.js, Bun, Docker) are left alone."
}

main() {
  parse_args "$@"
  preflight
  if ((${#SUDO[@]} == 0)) && [ "$(id -u)" -ne 0 ] && have sudo; then
    SUDO=(sudo)
  fi
  stop_systemd_units
  stop_checkout_processes
  remove_docker
  strip_shell_path
  (( PURGE_DATA )) && purge_data
  (( REMOVE_FILES )) && remove_files
  print_summary
}

main "$@"
