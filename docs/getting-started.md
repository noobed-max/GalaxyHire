# Getting started

This guide is for running GalaxyHire locally from a checkout.

## Requirements

The installer installs these for you when they are missing; they are listed for manual setups:

- Docker with Compose support
- Python 3.13 and [uv](https://docs.astral.sh/uv/)
- Node.js 20.19 or newer (22 LTS recommended)
- [Bun](https://bun.sh/) (the web test and build scripts invoke `bun`)
- A Chromium-based browser if you want to use the fill-only extension

The web workspace uses Bun-compatible scripts for its fastest test/build workflow, but the repository's supported dependency installation command uses `npm`.

## One-command install

From the repository root:

```bash
./scripts/install.sh          # install everything; start later with ./scripts/start.sh
./scripts/install.sh --start  # install, then start the API, corpus, and built UI in the background
./scripts/install.sh --dev    # install, then start with the hot-reload dev server
```

Nothing checked out yet? The script also works as a one-liner. It downloads the repository to `~/GalaxyHire` (no git required) and then installs and starts:

```bash
curl -fsSL https://raw.githubusercontent.com/noobed-max/GalaxyHire/master/scripts/install.sh | bash -s -- --start
```

Set `GALAXYHIRE_DIR` to choose the checkout location and `GALAXYHIRE_REF` to test a branch:

```bash
GALAXYHIRE_DIR=~/code/GalaxyHire GALAXYHIRE_REF=master \
  curl -fsSL https://raw.githubusercontent.com/noobed-max/GalaxyHire/master/scripts/install.sh | bash -s -- --start
```

The script detects your distribution, checks what is already installed, and only installs what is missing (Docker + Compose, uv, Node.js 22 LTS, Bun). It then installs every workspace's dependencies, creates local configuration from the committed examples, starts Postgres and Redis, applies the corpus schema, and builds the web UI and extension. It is idempotent: re-running skips what is already installed and never replaces an existing `.env`.

Useful options:

- `--dry-run` — print every action without changing the system
- `--deps-only` — install dependencies only (no Docker, schema, or builds)
- `--service` — install and start a systemd user service (Linux)
- `--skip-docker` — never install Docker; it must already be running
- `-y` — assume yes for all prompts

Full output is written to `.run/install-*.log`.

In the bash installer, `--start`, `--dev`, and `--service` are Linux-only. On macOS the installer still prepares Docker (via Colima), the schema, and the builds; start the services with `make corpus` and `make api` in two terminals.

## Windows (native PowerShell)

Native Windows installs use the PowerShell installer. With no checkout yet, this one-liner downloads the repository to `%USERPROFILE%\GalaxyHire` and then installs and starts:

```powershell
& ([scriptblock]::Create((irm https://raw.githubusercontent.com/noobed-max/GalaxyHire/master/scripts/install.ps1))) -Start
```

From an existing checkout:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install.ps1          # install; start later
powershell -ExecutionPolicy Bypass -File scripts\install.ps1 -Start   # install and start
```

Set `GALAXYHIRE_DIR` to choose the checkout location and `GALAXYHIRE_REF` to test a branch before running the one-liner.

It checks what is already installed and only installs what is missing: Git, Node.js LTS, Bun, uv, and — when missing — Docker Desktop via winget. It then installs the workspace dependencies, creates the local configuration, deploys Postgres/Redis, applies the corpus schema, and builds the UI. The same options exist as on Linux/macOS: `-DryRun`, `-DepsOnly`, `-SkipDocker`, `-Yes`, and `-Help`.

Docker Desktop must be able to start (WSL 2 backend or Hyper-V); sign out or reboot if the installer asks. Start and stop the stack later with:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\start.ps1
powershell -ExecutionPolicy Bypass -File scripts\start.ps1 -Stop
```

`start.ps1` writes logs and pid files to `.run\` and serves the app at `http://127.0.0.1:8000` (`-Dev` starts the hot-reload UI on 1420; open the URL in your browser). `-Stop` terminates the GalaxyHire processes; the Postgres and Redis containers keep running (`docker compose down` stops the infrastructure).

The `make` commands below are the manual alternative. They require a POSIX shell (WSL qualifies), so native Windows users should prefer the PowerShell scripts.

## Install and start

From the repository root:

```bash
make install
make infra-up
cp services/corpus/.env.example services/corpus/.env
make migrate
make up
```

`make up` starts the local infrastructure, corpus service, API, and built UI through the repository startup script. Open `http://127.0.0.1:8000` when it reports that the API is ready.

For frontend development with hot reload:

```bash
make up-dev
```

Alternatively, run the services individually:

```bash
make corpus   # corpus service on 127.0.0.1:8100
make api      # API and built UI on 127.0.0.1:8000
make web      # development UI server
```

Run `make build` before `make api` if you want the API process to serve the production UI. Use `make down` to stop services started by `make up`.

## First launch

1. Open Settings and choose an AI provider. See [AI provider configuration](ai-providers.md).
2. Add a preferred role and, optionally, country/city filters on Home.
3. Open **Add experience** and upload a résumé, or enter profile information manually.
4. Assign uploaded documents and profile points to tags such as `SDE` or `ML` when you use multiple career tracks.
5. Select **Find jobs** and search for a role.

## Build and load the extension

```bash
make ext-build
```

Open your browser's extension developer page, enable developer mode, choose **Load unpacked**, and select `apps/extension/dist`. Keep GalaxyHire running while you use the extension.

The extension fills fields on the active career-site page. It does not submit the application.

## Verify a checkout

```bash
make test
make lint
```

`make test` runs the workspace test suites, typechecks, the production build, and the vendor check. Some corpus tests require Docker/Postgres; start the infrastructure first.
