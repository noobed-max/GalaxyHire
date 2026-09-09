# Privacy and security

GalaxyHire is designed to run locally, but it can send selected data to services you configure. Treat résumé text, contact details, job descriptions, email content, and API credentials as sensitive.

## What stays on your machine

The local API stores profile data, uploaded documents, generated assets, settings, activity, and application records in its application-data area. On Linux the default location is under `~/.local/share/JustHireMe`; Windows and macOS use their normal per-user application-data locations. You can override the location with `JHM_APP_DATA_DIR`.

The browser UI talks to the local API. The API protects application routes with a local bearer token and binds the normal development services to loopback addresses.

## What can leave your machine

- Résumé/profile text and job context sent to the AI provider selected in Settings.
- Search requests sent to the enabled job-source connectors.
- Optional email content sent through the mailbox connection you configure.
- Optional provider, embedding, or enrichment requests enabled in your local settings.

Read the terms and privacy policy of every provider you connect. Ollama and subscription CLI providers can keep model calls local to the tools you run, but their own integrations and logs still apply.

## Credentials

Keep `.env`, API keys, cookies, bearer tokens, and mailbox credentials out of Git. The repository ignores local `.env` files and runtime databases. GalaxyHire masks stored secrets in the settings UI, but local filesystem access remains powerful: protect your operating-system account and application-data directory.

If a key is exposed, revoke or rotate it immediately and replace it in Settings. Do not upload credentials in screenshots or bug reports.

## Browser extension

The extension is intentionally fill-only. It may read and write fields on a supported career-site page when you invoke it, and it can attach the generated résumé where the page exposes a file input. It does not press Submit automatically. Review the browser's extension permissions and remove the extension when you no longer need it.

## Backups and deletion

Back up the local application-data directory if you need to preserve profile data, documents, generated assets, and settings. Deleting that data is local and irreversible unless you have a backup. Deleting a document does not necessarily remove profile points that were already imported from it; review the profile separately.

Before sharing diagnostics, remove résumé text, contact details, job URLs containing private information, provider responses, and all tokens. Use the Activity log's high-level messages instead of raw request bodies.
