<p align="center">
  <img src="apps/web/public/galaxyhire-logo.png" alt="GalaxyHire" width="360" />
</p>

<h1 align="center">GalaxyHire</h1>

<p align="center">
  Search for jobs, keep your experience organized, create truthful job-specific documents, and fill applications with your review at every step.
</p>

GalaxyHire is a local-first job-search and application workspace. It collects current roles from configured sources, helps you compare them with your profile, lets you select the experience and projects to use, and prepares the application. The browser extension fills forms; it never submits an application for you.

## What you can do

- Search for fresh roles and filter them by location, seniority, source, and match quality.
- Import résumés and other career material into a reusable profile.
- Tag skills, experience, projects, and uploaded documents for different career tracks.
- Review duplicate or similar résumé points before they are merged.
- Select the exact points and title variants to use for a job.
- Generate a résumé and cover letter, then prepare an application with the fill-only extension.
- Track applications, email updates, activity, and outcomes in one place.

## Start here

- [Documentation index](docs/index.md)
- [Getting started](docs/getting-started.md)
- [User guide](docs/user-guide.md)
- [AI provider configuration](docs/ai-providers.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Privacy and security](docs/privacy-security.md)

## Development quick start

One command installs the whole local stack — Docker infrastructure, toolchains, workspace dependencies, the database schema, and production builds:

```bash
./scripts/install.sh --start
```

Nothing checked out yet? This downloads the repository to `~/GalaxyHire`, then installs and starts it:

```bash
curl -fsSL https://raw.githubusercontent.com/noobed-max/GalaxyHire/master/scripts/install.sh | bash -s -- --start
```

On Windows (no checkout needed either):

```powershell
& ([scriptblock]::Create((irm https://raw.githubusercontent.com/noobed-max/GalaxyHire/master/scripts/install.ps1))) -Start
```

Both installers detect what is already on the machine — Docker, Git, Node.js, uv, Bun — and only install what is missing. They are safe to re-run, and use `--dry-run`/`-DryRun` to preview. Details: [Getting started](docs/getting-started.md).

To uninstall, run `./scripts/uninstall.sh` (`scripts\uninstall.ps1` on Windows); add `--purge-data`/`-PurgeData` and `--remove-files`/`-RemoveFiles` to also remove your local data and the checkout.

To update an existing installation to the latest source and restart it, run `./scripts/update.sh`, or use the one-liner:

```bash
curl -fsSL https://raw.githubusercontent.com/noobed-max/GalaxyHire/master/scripts/update.sh | bash
```

Your `.env` configuration and local data are preserved.

Prefer manual control? The make workflow below is the supported alternative. It requires Docker, Python 3.13 with [uv](https://docs.astral.sh/uv/), Node.js 20.19 or newer, and [Bun](https://bun.sh/) for the web test/build scripts.

```bash
make install
make infra-up
cp services/corpus/.env.example services/corpus/.env
make migrate
make up
```

Open `http://127.0.0.1:8000`. For frontend work, use `make up-dev` or run `make web` in a second terminal. The corpus service runs on port 8100 by default; the application API and built web UI run on port 8000.

Useful commands:

```bash
make build       # production web UI and extension bundles
make ext-build   # build the fill-only browser extension
make test        # all workspace tests, typechecks, lint checks, and builds
make lint        # Python lint plus frontend ESLint
make vendor-check
```

To load the extension during development, build it with `make ext-build`, open your browser's extension developer page, enable developer mode, and load `apps/extension/dist` as an unpacked extension.

## Safety defaults

GalaxyHire runs the API locally and keeps application actions reviewable. AI output is grounded in the profile and job data you provide; inspect generated documents before using them. The extension can fill fields on a career site, but the final Submit action remains yours.

## License

GalaxyHire is licensed under the **GNU Affero General Public License, version 3 only (AGPL-3.0-only)**. See [LICENSE](LICENSE) for the complete terms. The public project is maintained at [noobed-max/GalaxyHire](https://github.com/noobed-max/GalaxyHire).
