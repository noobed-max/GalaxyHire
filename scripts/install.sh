#!/usr/bin/env bash
#
# GalaxyHire one-command installer.
#
# Installs and deploys everything a local GalaxyHire needs on Linux or macOS:
#
#   1. system packages (curl, git, make, lsof, build tools)
#   2. Docker Engine + Compose v2 (started and health-checked)
#   3. toolchains: uv (Python), Node.js 22 LTS, Bun
#   4. workspace dependencies (corpus + API via uv, web/extension/scraper/contract via npm/bun)
#   5. local configuration (.env files, generated from the committed examples)
#   6. Postgres + Redis containers and the corpus schema
#   7. production web UI and browser-extension builds
#   8. optionally starts the stack or installs a systemd user service
#
# Design rules this script follows:
#   • Idempotent — safe to re-run; existing config files are never overwritten.
#   • Non-destructive — it never deletes databases, configs, or build output.
#   • Honest — it fails with the exact command and log line instead of continuing half-installed.
#   • No secrets — nothing is written to the repository; .env files are gitignored and chmod 600.
#
# Usage (from the repository root):
#   ./scripts/install.sh                 install everything, don't start the app
#   ./scripts/install.sh --start         install, then start the API/corpus/UI in the background
#   ./scripts/install.sh --dev           install, then start with the hot-reload dev server
#   ./scripts/install.sh --service       install a systemd user service running the stack (Linux)
#   ./scripts/install.sh --deps-only     only install dependencies (no Docker, no builds)
#   ./scripts/install.sh --dry-run       print every action without changing the system
#
# Run ./scripts/install.sh --help for all options.
set -Eeuo pipefail

# ── paths ─────────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
CORPUS_DIR="${REPO_ROOT}/services/corpus"
API_DIR="${REPO_ROOT}/apps/api"
WEB_DIR="${REPO_ROOT}/apps/web"
EXT_DIR="${REPO_ROOT}/apps/extension"
SCRAPER_DIR="${REPO_ROOT}/services/scraper-node"
CONTRACT_DIR="${REPO_ROOT}/packages/contract"
RUN_DIR="${REPO_ROOT}/.run"
LOG_FILE="${RUN_DIR}/install-$(date +%Y%m%d-%H%M%S).log"

# Vite 7 (web + extension) requires Node ^20.19 || >=22.12.
NODE_MIN_MAJOR_LOW=20
NODE_MIN_MINOR_LOW=19
NODE_MIN_MAJOR_HIGH=22
NODE_MIN_MINOR_HIGH=12
NODE_INSTALL_MAJOR=22

TOTAL_STEPS=8
STEP=0

# ── options ───────────────────────────────────────────────────────────────────
ASSUME_YES=0
START_APP=0
DEV_MODE=0
INSTALL_SERVICE=0
DEPS_ONLY=0
SKIP_DOCKER=0
DRY_RUN=0
ALLOW_ROOT=0
NEED_RELOGIN=0
SHELL_PATH_UPDATED=0

# shellcheck disable=SC2034
NO_COLOR="${NO_COLOR:-}"

if [ -t 1 ] && [ -z "${NO_COLOR}" ]; then
  C_RESET=$'\033[0m'; C_BOLD=$'\033[1m'; C_DIM=$'\033[2m'
  C_RED=$'\033[31m'; C_GREEN=$'\033[32m'; C_YELLOW=$'\033[33m'; C_CYAN=$'\033[36m'
else
  C_RESET=''; C_BOLD=''; C_DIM=''; C_RED=''; C_GREEN=''; C_YELLOW=''; C_CYAN=''
fi

usage() {
  cat <<'EOF'
GalaxyHire installer

Usage: ./scripts/install.sh [options]

Options:
  -y, --yes          assume "yes" for all prompts (non-interactive installs)
      --start        start GalaxyHire in the background after installing (Linux)
      --dev          start with the hot-reload dev server (Linux; implies --start)
      --service      install and start a systemd user service (Linux only)
      --deps-only    only install dependencies; skip Docker, schema, and builds
      --skip-docker  never install Docker (it must already be present and running)
      --dry-run      print what would happen without changing anything
      --allow-root   permit running as root (not recommended)
  -h, --help         show this help

Exit status: 0 on success, non-zero on the first failed step.
Full output is mirrored to .run/install-*.log.
EOF
}

parse_args() {
  while (($#)); do
    case "$1" in
      -y|--yes) ASSUME_YES=1 ;;
      --start) START_APP=1 ;;
      --dev) DEV_MODE=1; START_APP=1 ;;
      --service) INSTALL_SERVICE=1; START_APP=1 ;;
      --deps-only) DEPS_ONLY=1 ;;
      --skip-docker) SKIP_DOCKER=1 ;;
      --dry-run) DRY_RUN=1 ;;
      --allow-root) ALLOW_ROOT=1 ;;
      -h|--help) usage; exit 0 ;;
      *) printf 'Unknown option: %s (try --help)\n' "$1" >&2; exit 2 ;;
    esac
    shift
  done
  if (( DEPS_ONLY )) && { (( START_APP )) || (( INSTALL_SERVICE )); }; then
    printf '%s\n' '--deps-only cannot be combined with --start/--dev/--service' >&2
    exit 2
  fi
}

# ── logging ───────────────────────────────────────────────────────────────────
_log() { printf '[%s] %s\n' "$(date '+%H:%M:%S')" "$*" >>"${LOG_FILE}" 2>/dev/null || true; }
info() { printf '%s\n' "  ${C_CYAN}▸${C_RESET} $*"; _log "INFO  $*"; }
ok()   { printf '%s\n' "  ${C_GREEN}✓${C_RESET} $*"; _log "OK    $*"; }
warn() { printf '%s\n' "  ${C_YELLOW}!${C_RESET} $*" >&2; _log "WARN  $*"; }
die()  { printf '%s\n' "  ${C_RED}✗${C_RESET} $*" >&2; _log "ERROR $*"; exit 1; }

section() {
  STEP=$((STEP + 1))
  printf '\n%s\n' "${C_BOLD}==> [${STEP}/${TOTAL_STEPS}] $*${C_RESET}"
  _log "== $*"
}

on_error() {
  local code=$?
  printf '\n%s\n' "${C_RED}${C_BOLD}Installation failed (exit ${code}).${C_RESET}" >&2
  local i
  for ((i = 1; i < ${#FUNCNAME[@]}; i++)); do
    printf '  at %s:%s (%s)\n' "${BASH_SOURCE[$i]##*/}" "${BASH_LINENO[$i - 1]}" "${FUNCNAME[$i]}" >&2
  done
  printf '%s\n' "  Review the log and re-run; completed steps are skipped automatically." >&2
  printf '%s\n' "  Log: ${LOG_FILE}" >&2
  exit "$code"
}
trap on_error ERR

# Run a command, honoring --dry-run, and tee its output into the log.
run() {
  if (( DRY_RUN )); then
    printf '      %s[dry-run]%s %s\n' "${C_DIM}" "${C_RESET}" "$*"
    _log "DRY   $*"
    return 0
  fi
  _log "RUN   $*"
  "$@" 2>&1 | tee -a "${LOG_FILE}"
}

have() { command -v "$1" >/dev/null 2>&1; }

confirm() {
  local prompt="$1"
  if (( ASSUME_YES )); then return 0; fi
  if [ ! -t 0 ] && [ ! -r /dev/tty ]; then
    die "Cannot prompt for confirmation without a terminal. Re-run with --yes. (${prompt})"
  fi
  local reply=''
  printf '  %s? %s [Y/n] ' "${C_BOLD}" "${prompt}"
  read -r reply < /dev/tty || reply=''
  case "${reply:-y}" in
    [Yy]*) return 0 ;;
    *) return 1 ;;
  esac
}

# ── host detection ────────────────────────────────────────────────────────────
SUDO=()
PKG_MANAGER=''
OS_NAME=''

preflight() {
  if [ "$(id -u)" -eq 0 ] && (( ! ALLOW_ROOT )); then
    die "Run this script as a normal user, not root (it uses sudo internally). Override with --allow-root."
  fi
  if [ ! -f "${REPO_ROOT}/docker-compose.yml" ] || [ ! -d "${CORPUS_DIR}" ]; then
    die "This does not look like the GalaxyHire repository (expected docker-compose.yml + services/corpus)."
  fi
  OS_NAME="$(uname -s)"
  case "${OS_NAME}" in
    Linux|Darwin) ;;
    *) die "Unsupported operating system: ${OS_NAME}. Linux or macOS is required." ;;
  esac
  if grep -qi microsoft /proc/version 2>/dev/null; then
    warn "WSL detected — Docker Desktop integration must be enabled for the containers to start."
  fi
  if [ "$(id -u)" -ne 0 ]; then
    have sudo || die "sudo is required to install system packages and Docker."
    SUDO=(sudo)
  fi

  if (( ! DRY_RUN )); then
    mkdir -p "${RUN_DIR}"
    : >"${LOG_FILE}"
  fi
  _log "GalaxyHire installer starting (dry-run=${DRY_RUN}, os=${OS_NAME})"

  local free_kb
  free_kb="$(df -Pk "${REPO_ROOT}" | awk 'NR==2 {print $4}')"
  if [ -n "${free_kb}" ] && [ "${free_kb}" -lt 12582912 ]; then
    warn "Less than 12 GB free on this filesystem (${free_kb} KB). Dependencies, models, and the corpus may not fit."
  fi
}

detect_pkg_manager() {
  if have apt-get; then PKG_MANAGER=apt
  elif have dnf; then PKG_MANAGER=dnf
  elif have pacman; then PKG_MANAGER=pacman
  elif have zypper; then PKG_MANAGER=zypper
  elif have brew; then PKG_MANAGER=brew
  else PKG_MANAGER=unknown
  fi
  _log "package manager: ${PKG_MANAGER}"
}

# ── 1. system packages ────────────────────────────────────────────────────────
install_base_packages() {
  section "System packages"
  local need=0 cmd
  for cmd in curl git make lsof unzip xz tar; do
    have "${cmd}" || need=1
  done
  have cc || have gcc || have clang || need=1

  if [ "${PKG_MANAGER}" = brew ]; then
    brew_missing=()
    for cmd in curl git make; do have "${cmd}" || brew_missing+=("${cmd}"); done
    if [ "${#brew_missing[@]}" -eq 0 ]; then
      ok "base tools already present"
      return 0
    fi
    warn "missing: ${brew_missing[*]}"
    run brew install "${brew_missing[@]}"
    ok "base tools installed"
    return 0
  fi

  if (( ! need )); then
    ok "base tools already present"
    return 0
  fi
  warn "Some base tools are missing."
  confirm "Install the missing system packages with ${PKG_MANAGER}?" || die "Required tools are missing."
  case "${PKG_MANAGER}" in
    apt)
      run "${SUDO[@]}" apt-get update
      run "${SUDO[@]}" env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        curl git make lsof unzip xz-utils ca-certificates build-essential
      ;;
    dnf)
      run "${SUDO[@]}" dnf install -y curl git make lsof unzip xz ca-certificates gcc gcc-c++
      ;;
    pacman)
      run "${SUDO[@]}" pacman -Sy --noconfirm curl git make lsof unzip xz ca-certificates base-devel
      ;;
    zypper)
      run "${SUDO[@]}" zypper --non-interactive install curl git make lsof unzip xz ca-certificates gcc gcc-c++
      ;;
    *)
      die "No supported package manager found; install curl, git, make, lsof, unzip, and a C compiler manually."
      ;;
  esac
  ok "base tools installed"
}

# ── 2. Docker ─────────────────────────────────────────────────────────────────
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

install_compose_plugin() {
  info "Installing the Docker Compose v2 plugin..."
  local os arch url
  case "${OS_NAME}" in
    Linux) os=linux ;;
    Darwin) os=darwin ;;
  esac
  case "$(uname -m)" in
    x86_64|amd64) arch=x86_64 ;;
    aarch64|arm64) arch=aarch64 ;;
    *) die "Unsupported architecture for Compose plugin: $(uname -m)" ;;
  esac
  url="https://github.com/docker/compose/releases/latest/download/docker-compose-${os}-${arch}"
  if [ "${OS_NAME}" = Linux ] && ((${#SUDO[@]})); then
    run "${SUDO[@]}" mkdir -p /usr/local/lib/docker/cli-plugins
    run "${SUDO[@]}" curl -fsSL "${url}" -o /usr/local/lib/docker/cli-plugins/docker-compose
    run "${SUDO[@]}" chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
  else
    run mkdir -p "${HOME}/.docker/cli-plugins"
    run curl -fsSL "${url}" -o "${HOME}/.docker/cli-plugins/docker-compose"
    run chmod +x "${HOME}/.docker/cli-plugins/docker-compose"
  fi
}

ensure_docker() {
  section "Docker"
  if ! have docker; then
    if (( SKIP_DOCKER )); then
      die "Docker is not installed and --skip-docker was given. Install Docker, then re-run."
    fi
    if (( DRY_RUN )); then
      info "[dry-run] would install Docker Engine + the Compose v2 plugin and start the daemon"
      return 0
    fi
    warn "Docker is not installed."
    confirm "Install Docker now?" || die "Docker is required to run Postgres and Redis."
    if [ "${OS_NAME}" = Darwin ]; then
      have brew || die "Install Docker Desktop (https://www.docker.com/products/docker-desktop/) or Homebrew, then re-run."
      run brew install docker docker-compose colima
      run colima start
    else
      local tmp
      tmp="$(mktemp "${TMPDIR:-/tmp}/galaxyhire-docker.XXXXXX")"
      run curl -fsSL https://get.docker.com -o "${tmp}"
      run "${SUDO[@]}" sh "${tmp}"
      run rm -f "${tmp}"
    fi
  fi

  # Make sure the daemon is actually running before anything tries to talk to it.
  if [ "${OS_NAME}" = Linux ] && have systemctl && ! systemctl is-active --quiet docker 2>/dev/null; then
    info "Starting the Docker daemon..."
    run "${SUDO[@]}" systemctl enable --now docker || warn "Could not start Docker via systemd; start it manually."
  fi

  # Membership in the docker group removes the need for sudo on future runs.
  if [ "${OS_NAME}" = Linux ] && ! id -nG 2>/dev/null | tr ' ' '\n' | grep -qx docker; then
    info "Adding $(id -un) to the 'docker' group..."
    run "${SUDO[@]}" usermod -aG docker "$(id -un)" || warn "Could not update the docker group; this run will use sudo."
    NEED_RELOGIN=1
  fi

  discover_docker || die "The Docker daemon is not reachable. Start Docker (or log out and back in after the group change) and re-run."
  if ! "${DOCKER[@]}" compose version >/dev/null 2>&1; then
    install_compose_plugin
  fi
  "${DOCKER[@]}" compose version >/dev/null 2>&1 || die "Docker Compose v2 is required but not available."
  ok "Docker and Compose v2 are ready"
}

# ── 3. toolchains ─────────────────────────────────────────────────────────────
ensure_shell_path() {
  # Persist the toolchain locations for future shells. Only touches files that already exist.
  # shellcheck disable=SC2016  # $HOME must stay literal: this line is written into rc files.
  local line='export PATH="$HOME/.local/bin:$HOME/.bun/bin:$PATH"'
  local rc
  for rc in "${HOME}/.bashrc" "${HOME}/.zshrc" "${HOME}/.profile"; do
    [ -f "${rc}" ] || continue
    grep -qF '.bun/bin' "${rc}" 2>/dev/null && continue
    if (( DRY_RUN )); then
      info "[dry-run] would add the toolchain PATH to ${rc}"
    else
      printf '\n# GalaxyHire toolchain (added by scripts/install.sh)\n%s\n' "${line}" >>"${rc}"
      SHELL_PATH_UPDATED=1
    fi
  done
}

ensure_uv() {
  export PATH="${HOME}/.local/bin:${PATH}"
  if have uv; then
    ok "uv $(uv --version 2>/dev/null | awk '{print $2}')"
    return 0
  fi
  info "Installing uv (Python toolchain)..."
  if (( DRY_RUN )); then
    info "[dry-run] would install uv from https://astral.sh/uv"
    ok "uv would be installed"
    return 0
  fi
  run bash -c 'curl -LsSf https://astral.sh/uv/install.sh | sh' \
    || die "uv installation failed. Install it manually from https://docs.astral.sh/uv/"
  export PATH="${HOME}/.local/bin:${PATH}"
  have uv || die "uv was installed but is not on PATH (expected ${HOME}/.local/bin/uv)."
  ok "uv installed"
}

node_version_ok() {
  have node || return 1
  local v major minor
  v="$(node -v 2>/dev/null)" || return 1
  v="${v#v}"
  major="${v%%.*}"
  minor="${v#*.}"; minor="${minor%%.*}"
  # Unquoted on purpose: bash 3.2 (macOS default) treats a quoted left side as a literal.
  [[ ${major} =~ ^[0-9]+$ ]] || return 1
  [[ ${minor} =~ ^[0-9]+$ ]] || minor=0
  if (( major > NODE_MIN_MAJOR_HIGH )); then return 0; fi
  if (( major == NODE_MIN_MAJOR_LOW )) && (( minor >= NODE_MIN_MINOR_LOW )); then return 0; fi
  if (( major == NODE_MIN_MAJOR_HIGH )) && (( minor >= NODE_MIN_MINOR_HIGH )); then return 0; fi
  return 1
}

ensure_node() {
  export PATH="${HOME}/.local/bin:${PATH}"
  if node_version_ok; then
    ok "Node.js $(node -v)"
    return 0
  fi
  have node && warn "Node.js $(node -v) is older than required (need 20.19+/22.12+); installing Node ${NODE_INSTALL_MAJOR} LTS."
  if (( DRY_RUN )); then
    info "[dry-run] would install Node.js ${NODE_INSTALL_MAJOR} LTS (via brew on macOS, official tarball elsewhere)"
    ok "Node.js would be installed"
    return 0
  fi

  if [ "${PKG_MANAGER}" = brew ] && have brew; then
    run brew install "node@${NODE_INSTALL_MAJOR}" || die "brew install node@${NODE_INSTALL_MAJOR} failed."
    run brew link --overwrite --force "node@${NODE_INSTALL_MAJOR}" || true
    node_version_ok || die "Node installation did not produce a usable 'node'."
    ok "Node.js $(node -v)"
    return 0
  fi

  local os arch name ver url tmp dest
  case "${OS_NAME}" in
    Linux) os=linux ;;
    Darwin) os=darwin ;;
  esac
  case "$(uname -m)" in
    x86_64|amd64) arch=x64 ;;
    aarch64|arm64) arch=arm64 ;;
    *) die "Unsupported architecture for the Node.js tarball: $(uname -m)" ;;
  esac
  info "Resolving the latest Node ${NODE_INSTALL_MAJOR}.x release..."
  name="$(curl -fsSL "https://nodejs.org/dist/latest-v${NODE_INSTALL_MAJOR}.x/" 2>/dev/null \
    | grep -oE "node-v${NODE_INSTALL_MAJOR}[0-9.]+-${os}-${arch}\.tar\.xz" | head -n1 || true)"
  [ -n "${name}" ] || die "Could not resolve a Node.js ${NODE_INSTALL_MAJOR}.x release for ${os}-${arch} (is nodejs.org reachable?)."
  ver="${name#node-}"; ver="${ver%-"${os}"-"${arch}".tar.xz}"
  dest="${HOME}/.local/opt/node-${ver}-${os}-${arch}"
  url="https://nodejs.org/dist/latest-v${NODE_INSTALL_MAJOR}.x/${name}"

  tmp="$(mktemp -d "${TMPDIR:-/tmp}/galaxyhire-node.XXXXXX")"
  info "Downloading ${name}..."
  curl -fsSL "${url}" -o "${tmp}/${name}" || die "Node.js download failed."
  mkdir -p "${HOME}/.local/opt" "${HOME}/.local/bin"
  rm -rf "${dest}"
  tar -xJf "${tmp}/${name}" -C "${HOME}/.local/opt" || die "Could not extract the Node.js archive."
  rm -rf "${tmp}"
  ln -sf "${dest}/bin/node" "${HOME}/.local/bin/node"
  ln -sf "${dest}/bin/npm" "${HOME}/.local/bin/npm"
  ln -sf "${dest}/bin/npx" "${HOME}/.local/bin/npx"
  export PATH="${HOME}/.local/bin:${PATH}"
  node_version_ok || die "Node.js installation failed; expected ${HOME}/.local/bin/node."
  ok "Node.js $(node -v)"
}

ensure_bun() {
  export BUN_INSTALL="${BUN_INSTALL:-${HOME}/.bun}"
  export PATH="${BUN_INSTALL}/bin:${PATH}"
  if have bun; then
    ok "Bun $(bun --version 2>/dev/null)"
    return 0
  fi
  info "Installing Bun..."
  if (( DRY_RUN )); then
    info "[dry-run] would install Bun from https://bun.sh"
    ok "Bun would be installed"
    return 0
  fi
  if ! run bash -c 'curl -fsSL https://bun.sh/install | bash'; then
    warn "The Bun installer failed; trying npm."
    have npm || die "Could not install Bun. Install it from https://bun.sh and re-run."
    if ! run npm install -g bun; then
      ((${#SUDO[@]})) || die "Could not install Bun."
      run "${SUDO[@]}" npm install -g bun || die "Could not install Bun."
    fi
  fi
  export PATH="${BUN_INSTALL}/bin:${PATH}"
  have bun || die "Bun was installed but is not on PATH (expected ${BUN_INSTALL}/bin/bun)."
  ok "Bun installed"
}

# ── 3b. toolchain bundle ──────────────────────────────────────────────────────
ensure_toolchains() {
  section "Toolchains (Python, Node.js, Bun)"
  ensure_uv
  ensure_node
  ensure_bun
  ensure_shell_path
  ok "toolchains ready"
}

# ── 4. workspace dependencies ─────────────────────────────────────────────────
run_uv_sync() {
  local dir=$1; shift
  ( cd "${dir}" && run uv sync "$@" )
}

run_node_install() {
  local dir=$1
  # Lockfiles are committed: the installer must not silently rewrite them with unpinned versions.
  # A failure here means package.json and its lockfile disagree, which the user must resolve.
  if [ "${dir}" = "${WEB_DIR}" ] && [ -f "${WEB_DIR}/bun.lock" ]; then
    ( cd "${dir}" && run bun install --frozen-lockfile ) \
      || die "bun install failed in ${dir}. Run 'bun install' there to refresh bun.lock, then re-run."
  elif [ -f "${dir}/package-lock.json" ]; then
    ( cd "${dir}" && run npm ci ) \
      || die "npm ci failed in ${dir}. Run 'npm install' there to refresh the lockfile, then re-run."
  else
    die "No lockfile found in ${dir}; refusing an unpinned install."
  fi
}

install_workspace_deps() {
  section "Workspace dependencies"
  info "corpus Python environment (services/corpus)..."
  run_uv_sync "${CORPUS_DIR}" --extra embed
  info "API Python environment (apps/api)..."
  run_uv_sync "${API_DIR}"
  info "web UI dependencies (apps/web)..."
  run_node_install "${WEB_DIR}"
  info "browser extension dependencies (apps/extension)..."
  run_node_install "${EXT_DIR}"
  info "scraper dependencies (services/scraper-node)..."
  run_node_install "${SCRAPER_DIR}"
  info "contract dependencies (packages/contract)..."
  run_node_install "${CONTRACT_DIR}"
  ok "all workspace dependencies installed"
}

# ── 5. configuration ──────────────────────────────────────────────────────────
prepare_env() {
  section "Configuration"
  run mkdir -p "${CORPUS_DIR}/data/objects" "${CORPUS_DIR}/data/resumes" "${RUN_DIR}"

  local corpus_env="${CORPUS_DIR}/.env"
  if [ -f "${corpus_env}" ]; then
    info "Keeping the existing services/corpus/.env"
  else
    run cp "${CORPUS_DIR}/.env.example" "${corpus_env}"
    (( DRY_RUN )) || ok "Created services/corpus/.env from the committed example"
  fi
  if [ -f "${corpus_env}" ] && (( ! DRY_RUN )); then chmod 600 "${corpus_env}" 2>/dev/null || true; fi

  local scraper_env="${SCRAPER_DIR}/.env"
  if [ -f "${scraper_env}" ]; then
    info "Keeping the existing services/scraper-node/.env"
  else
    run cp "${SCRAPER_DIR}/.env.example" "${scraper_env}"
    (( DRY_RUN )) || ok "Created services/scraper-node/.env from the committed example"
  fi
  if [ -f "${scraper_env}" ] && (( ! DRY_RUN )); then chmod 600 "${scraper_env}" 2>/dev/null || true; fi

  if [ -f "${corpus_env}" ] && ! grep -q 'localhost:5433' "${corpus_env}" 2>/dev/null; then
    warn "services/corpus/.env does not reference localhost:5433; the Docker Postgres is published there."
    warn "Edit DATABASE_URL if the corpus cannot connect."
  fi
  ok "configuration ready (no secrets are written to the repository)"
}

# ── 6. infrastructure ─────────────────────────────────────────────────────────
wait_for_postgres() {
  (( DRY_RUN )) && { info "[dry-run] would wait for Postgres"; return 0; }
  for _ in $(seq 1 90); do
    if "${DOCKER[@]}" compose exec -T postgres pg_isready -U galaxy >/dev/null 2>&1; then
      ok "Postgres ready"
      return 0
    fi
    sleep 1
  done
  die "Postgres became ready-checkable but never accepted connections. Inspect: ${DOCKER[*]} compose logs postgres"
}

wait_for_redis() {
  (( DRY_RUN )) && { info "[dry-run] would wait for Redis"; return 0; }
  for _ in $(seq 1 60); do
    if "${DOCKER[@]}" compose exec -T redis redis-cli ping 2>/dev/null | grep -q PONG; then
      ok "Redis ready"
      return 0
    fi
    sleep 1
  done
  die "Redis did not answer its health check. Inspect: ${DOCKER[*]} compose logs redis"
}

deploy_infra() {
  section "Infrastructure (Postgres + Redis)"
  if ! have docker; then
    if (( DRY_RUN )); then
      info "[dry-run] would start Postgres + Redis via docker compose"
      return 0
    fi
    (( SKIP_DOCKER )) && die "Docker is required for the database; cannot skip it."
    die "Docker is required. Re-run without --skip-docker."
  fi
  discover_docker || die "The Docker daemon is not reachable. Start Docker and re-run."
  info "Starting containers..."
  ( cd "${REPO_ROOT}" && run "${DOCKER[@]}" compose up -d )
  wait_for_postgres
  wait_for_redis
}

# ── 7. schema ─────────────────────────────────────────────────────────────────
run_migrations() {
  section "Database schema"
  info "Applying corpus migrations..."
  ( cd "${REPO_ROOT}" && run make migrate )
  ok "schema is up to date"
}

# ── 8. builds ─────────────────────────────────────────────────────────────────
build_artifacts() {
  section "Production builds"
  info "Building the web UI and browser extension..."
  ( cd "${REPO_ROOT}" && run make build )
  ok "builds complete"
}

# ── 9. run / service ──────────────────────────────────────────────────────────
wait_http() { # url label tries
  local url=$1 label=$2 tries=${3:-120}
  (( DRY_RUN )) && return 0
  for _ in $(seq 1 "${tries}"); do
    if curl -fsS "${url}" >/dev/null 2>&1; then
      ok "${label} is answering on ${url}"
      return 0
    fi
    sleep 1
  done
  die "${label} did not become healthy; see ${RUN_DIR}/*.log"
}

# Detach so the supervisor survives this shell. setsid exists on Linux but not macOS.
launch_detached() {
  if have setsid; then
    setsid nohup "$@" >"${RUN_DIR}/start.log" 2>&1 </dev/null &
  else
    nohup "$@" >"${RUN_DIR}/start.log" 2>&1 </dev/null &
  fi
}

detach_start() {
  if (( NEED_RELOGIN )) && have sg; then
    # A user added to the docker group during this install does not have that group in the current
    # session. `sg docker` applies it without a re-login; it accepts only a single command string,
    # so the arguments are quoted into it.
    local cmd
    printf -v cmd '%q ' "${REPO_ROOT}/scripts/start.sh" "$@"
    launch_detached sg docker -c "exec ${cmd% }"
  else
    launch_detached "${REPO_ROOT}/scripts/start.sh" "$@"
  fi
}

start_stack() {
  section "Starting GalaxyHire"
  if curl -fsS http://127.0.0.1:8000/health >/dev/null 2>&1; then
    ok "GalaxyHire is already running on http://127.0.0.1:8000"
    return 0
  fi
  if [ "${OS_NAME}" = Darwin ]; then
    # scripts/start.sh uses setsid and GNU `env -C`, which stock macOS does not provide. The
    # installer can prepare everything, but the two services must be started manually there.
    warn "Automatic start is supported on Linux only."
    printf '%s\n' "  Start the services in two terminals instead:"
    printf '%s\n' "    make corpus    # corpus service on :8100"
    printf '%s\n' "    make api       # API + built UI on :8000"
    return 0
  fi
  if (( NEED_RELOGIN )) && ! have sg; then
    warn "Docker group membership is not active in this session and 'sg' is unavailable."
    printf '%s\n' "  Log out and back in, then start GalaxyHire with ./scripts/start.sh"
    return 0
  fi
  info "Launching scripts/start.sh in the background (logs in ${RUN_DIR}/)..."
  if (( DRY_RUN )); then
    if (( DEV_MODE )); then
      info "[dry-run] would run scripts/start.sh --dev"
    else
      info "[dry-run] would run scripts/start.sh"
    fi
    return 0
  fi
  if (( DEV_MODE )); then
    detach_start --dev
  else
    detach_start
  fi
  sleep 1
  if (( DEV_MODE )); then
    wait_http http://127.0.0.1:1420 "web UI" 180
  else
    wait_http http://127.0.0.1:8000/health "GalaxyHire API"
  fi
}

install_systemd_service() {
  section "systemd user service"
  [ "${OS_NAME}" = Linux ] || die "--service is only supported on Linux; use --start on macOS."
  have systemctl || die "--service requires systemd; use --start instead."
  if ! systemctl --user show-environment >/dev/null 2>&1; then
    die "The systemd user instance is not available; use --start instead."
  fi

  local unit_dir="${HOME}/.config/systemd/user"
  local unit="${unit_dir}/galaxyhire.service"
  local node_bin bun_bin uv_bin path_line
  node_bin="$(dirname "$(command -v npm 2>/dev/null || command -v node 2>/dev/null || echo "${HOME}/.local/bin/node")")"
  bun_bin="$(dirname "$(command -v bun 2>/dev/null || echo "${HOME}/.bun/bin/bun")")"
  uv_bin="$(dirname "$(command -v uv 2>/dev/null || echo "${HOME}/.local/bin/uv")")"
  path_line="${node_bin}:${bun_bin}:${uv_bin}:${HOME}/.local/bin:${HOME}/.bun/bin:/usr/local/bin:/usr/bin:/bin"

  if [ -f "${RUN_DIR}/supervisor" ] || compgen -G "${RUN_DIR}/*.pid" >/dev/null; then
    info "Stopping the manually started instance before handing over to systemd..."
    run "${REPO_ROOT}/scripts/start.sh" --stop || true
    sleep 2
  fi

  run mkdir -p "${unit_dir}"
  if (( DRY_RUN )); then
    info "[dry-run] would write ${unit} and enable it"
    return 0
  fi
  if [ -f "${unit}" ]; then
    cp "${unit}" "${unit}.bak-$(date +%Y%m%d-%H%M%S)"
  fi
  cat >"${unit}" <<EOF
[Unit]
Description=GalaxyHire local application (API, corpus, and built web UI)
Documentation=file://${REPO_ROOT}/docs/getting-started.md
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=${REPO_ROOT}
Environment=PATH=${path_line}
Environment=HOME=${HOME}
# Docker socket access without requiring a logout after the installer added the docker group.
SupplementaryGroups=docker
ExecStart=${REPO_ROOT}/scripts/start.sh
Restart=on-failure
RestartSec=10
TimeoutStopSec=30
KillMode=mixed

[Install]
WantedBy=default.target
EOF
  chmod 644 "${unit}"
  run systemctl --user daemon-reload
  run systemctl --user enable galaxyhire.service
  run systemctl --user restart galaxyhire.service

  if ((${#SUDO[@]})); then
    run "${SUDO[@]}" loginctl enable-linger "$(id -un)" \
      || warn "Could not enable lingering; the service will stop when you log out."
  fi
  wait_http http://127.0.0.1:8000/health "GalaxyHire API"
  ok "systemd user service installed: systemctl --user status galaxyhire"
}

print_summary() {
  if (( DRY_RUN )); then
    printf '\n%s\n' "${C_BOLD}Dry run complete — no changes were made.${C_RESET}"
    printf '%s\n' "  Re-run without --dry-run to perform the install (add --start to launch afterwards)."
    return 0
  fi
  printf '\n%s\n' "${C_GREEN}${C_BOLD}GalaxyHire is installed.${C_RESET}"
  printf '%s\n' "  Web + API   http://127.0.0.1:8000"
  printf '%s\n' "  Corpus      http://127.0.0.1:8100"
  printf '%s\n' "  Logs        ${RUN_DIR}/*.log"
  printf '\n%s\n' "  Start       ./scripts/start.sh"
  printf '%s\n' "  Dev mode    ./scripts/start.sh --dev"
  printf '%s\n' "  Stop        ./scripts/start.sh --stop"
  printf '%s\n' "  Docs        docs/getting-started.md"
  if (( INSTALL_SERVICE )); then
    printf '%s\n' "  Service     systemctl --user status galaxyhire   (journalctl --user -u galaxyhire -f)"
  fi
  if (( START_APP )) && (( ! INSTALL_SERVICE )); then
    printf '%s\n' "  Started in the background; stop with ./scripts/start.sh --stop"
  fi
  if (( ! START_APP )); then
    printf '\n%s\n' "  Start it when ready:  ./scripts/start.sh"
  fi
  printf '\n%s\n' "  Next: open the app, go to Settings, and configure an AI provider (docs/ai-providers.md)."
  if (( NEED_RELOGIN )); then
    printf '\n%s\n' "  Note: you were added to the 'docker' group. Log out and back in (or run 'newgrp docker')"
    printf '%s\n' "        to use Docker without sudo."
  fi
  if (( ! INSTALL_SERVICE )) && (( ! START_APP )) && (( SHELL_PATH_UPDATED )); then
    printf '\n%s\n' "  Open a new terminal (or source your shell profile) before running './scripts/start.sh',"
    printf '%s\n' "  so the just-installed toolchains are on PATH."
  fi
  printf '\n%s\n' "  Full install log: ${LOG_FILE}"
}

set_total_steps() {
  TOTAL_STEPS=8
  if (( INSTALL_SERVICE || START_APP )); then
    TOTAL_STEPS=9
  fi
  if (( DEPS_ONLY )); then
    TOTAL_STEPS=4
  fi
}

main() {
  parse_args "$@"
  set_total_steps
  preflight
  detect_pkg_manager

  install_base_packages
  ensure_toolchains
  install_workspace_deps
  prepare_env

  if (( DEPS_ONLY )); then
    printf '\n%s\n' "${C_BOLD}==> Dependencies only — finished${C_RESET}"
    ok "Dependencies are installed. Re-run without --deps-only to deploy Docker, the schema, and builds."
    printf '%s\n' "  Log: ${LOG_FILE}"
    exit 0
  fi

  ensure_docker
  deploy_infra
  run_migrations
  build_artifacts

  if (( INSTALL_SERVICE )); then
    install_systemd_service
  elif (( START_APP )); then
    start_stack
  fi

  print_summary
}

main "$@"

