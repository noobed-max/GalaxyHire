# Troubleshooting

## The page is blank or returns 404

Build the UI before starting the API:

```bash
make build
make api
```

For frontend development, use `make web` or `make up-dev`. Confirm that the API is listening on port 8000 and the corpus service on port 8100.

## The API or corpus will not start

Check Docker first:

```bash
docker compose ps
make infra-up
make migrate
```

If a port is already in use, stop the previous GalaxyHire process or choose a different `APP_PORT`/`CORPUS_PORT` when invoking the Make target. `make down` stops services started by the repository startup script.

## Find jobs returns no useful results

Confirm that a role is saved on Home, the enabled source portals are not all disabled, and the corpus service is running. Use the Activity log to distinguish an empty source from a scraper or network failure. Search again to start a fresh collection.

Some sources do not publish a job inside every time window. Try a broader role or location, then remove overly narrow filters in Find jobs.

## Résumé parsing is slow or appears stuck

Open **Add experience** and check the ingestion progress card. Parsing continues when you change screens. Use **Activity log** for stage details and errors. Check the AI provider with **Check API**, confirm the model name, and verify that the uploaded file is readable and within your provider's context limits.

If parsing fails after a provider timeout, retry after checking the provider's status and rate limits. Large or image-only PDFs may need a provider with document/image support; a text-extraction fallback is used when possible.

## OpenCode Go says `x-opencode-session` is missing

Open Settings and select the separate **OpenCode** provider. Use the default OpenCode Go endpoint and **Responses** format, then save the settings and restart GalaxyHire. Older installations may need their configuration migration to run once. Do not use the generic OpenAI-compatible panel for OpenCode Go.

Rotate any key that was exposed in a terminal, screenshot, issue, or chat history.

## Duplicate review is empty or cannot be completed

Refresh the page after the ingestion task reaches review state. The dialog reads staged pairs from the active task and requires a decision for each pair. If the task completed without reviewable pairs, the message is informational. If it says review is required but shows no pairs, inspect Activity for the ingestion task error and retry the upload.

## The wrong points appear for a tag

Open **Add experience**, check the document's upload tag and then inspect the profile-point tag controls. A tag on a document is the starting provenance; individual points can have their own tags. In **Any**, shared points appear in multiple columns but remain one export item. General should contain only untagged material.

## The extension does not fill a page

Build and reload the extension:

```bash
make ext-build
```

On the browser's extension page, reload the unpacked extension and refresh the career-site tab. Make sure the site is supported and that the extension has permission for that site. Fill page by page; some fields are hidden until earlier answers are entered.

The extension never submits. If a field is wrong, correct it on the page before submitting yourself.

## A provider returns 401, 403, 404, or 429

- `401`/`403`: the key is invalid, expired, scoped incorrectly, or not accepted by that endpoint.
- `404`: the base URL, request format, or model identifier is wrong.
- `429`: the provider rate limit or account quota was reached; wait or use a permitted model.
- `5xx`: the provider is unavailable; retry later and check its status page.

Use **Check API** after correcting settings. Do not include the full key or private provider response in bug reports.
