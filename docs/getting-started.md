# Getting started

This guide is for running GalaxyHire locally from a checkout.

## Requirements

- Docker with Compose support
- Python 3.13 and [uv](https://docs.astral.sh/uv/)
- Node.js 20 or newer
- [Bun](https://bun.sh/) (the web test and build scripts invoke `bun`)
- A Chromium-based browser if you want to use the fill-only extension

The web workspace uses Bun-compatible scripts for its fastest test/build workflow, but the repository's supported dependency installation command uses `npm`.

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
