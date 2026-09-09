# scraper-node

Hosts the **career-ops** job providers in their native runtime (vendored, with credential literals
externalized) and posts
normalized envelopes to the corpus. This is the whole scraper layer — the ever-jobs NestJS
connectors and the in-process Python adapters that used to live beside it are no longer part of
this worker; it hosts one connector family and is well-tested.

```
career-ops   ~80 providers   plain ESM .mjs, {id, detect?, fetch(entry, ctx) -> Job[]}
```

The providers come from `career-ops/providers/` via `make vendor-check`'s rsync procedure; the
tree under `vendor/career-ops-providers/` stays a verbatim snapshot except for the intentional
credential externalization in `arbeitsagentur.mjs` and `vdab.mjs`. Everything GalaxyHire
adds — recency policy, role gate, wall-clock guard, description enrichment — runs on the
`Job[]` a provider returned, never inside the provider file.

## Run

```bash
npm start -- --role "software engineer" --hours 24        # config defaults (gh.toggle) select portals
npm start -- --role swe --list                            # every valid portal id + policy + selection
npm start -- --role swe --portals arbeitnow,wttj,greenhouse:anthropic
npm start -- --role swe --dry-run --report /tmp/r.json   # fetch + validate, post nothing
npm run probe                                             # load-and-run smoke test, hits the network
```

Portal ids are `provider` for board-wide feeds and `provider:slug` for company tenants — the
same ids the UI's toggle column (GET /corpus/sources) speaks. Unknown ids are a hard error, never
a silently-short run.

## Portal configuration

`config/portals.yml` is career-ops' format — providers were written against these field names —
plus one GalaxyHire-namespaced block per entry that providers never read:

```yaml
- name: GitLab
  provider: greenhouse
  api: https://boards-api.greenhouse.io/v1/boards/gitlab/jobs
  gh:                              # ours; opaque to the provider
    recency: first_seen            # strict | first_seen | off  (measured, not guessed)
    toggle: false                  # default state in the UI column
    region: us                     # display grouping
    timeout_ms: 45000              # optional per-portal wall clock
```

Resolution order: `--config <path>` → `config/portals.yml` → `config/portals.example.yml` (the
career-ops-format seed, so a fresh checkout can scrape immediately). `enabled: false`,
websearch-only, and `parser:` (shell-out) entries are filtered before anything is fetched —
see `isDirectlyFetchable`.

Corpus target comes from `CORPUS_URL` (default `http://127.0.0.1:8100`) and `CORPUS_API_KEY`.
The two national providers also require runtime environment variables:
`ARBEITSAGENTUR_API_KEY` and `VDAB_VEJ_KEY_MONITOR`. Copy
`services/scraper-node/.env.example` as a local reference, but keep actual values
outside version control.

## The module map

| File | Job |
|---|---|
| `src/index.ts` | CLI, portal selection, bounded pool, corpus post, run report |
| `src/adapters/career-ops.ts` | run ONE entry end-to-end: route → inject role → fetch → enrich → recency → gate → envelopes. Never throws; a dead portal is a reported failure |
| `src/config.ts` | portals.yml loading, `isDirectlyFetchable`, `resolveEntryId` (mirrored in Python — see fixtures note) |
| `src/recency.ts` | the three policies; labels every kept row `posted` or `first_seen` |
| `src/query-injection.ts` | the server-side role hooks (verified key-by-key against provider source) |
| `src/audit/relevance.ts` | `titlePassesRoleGate` — the ingest-time analogue of career-ops' `title_filter` |
| `src/url-key.ts` | port of career-ops' `url-key.mjs`; makes `(site, source_job_id)` stable |
| `src/enrich/descriptions.ts` | one-extra-request-per-board description fetchers (solid.jobs today) |
| `src/map/career-ops.ts` | `Job` → `JobEnvelope` — where correctness actually lives |
| `src/corpus.ts` | batched, failure-isolated POSTs to `/ingest/observations`, embed + count |
| `src/http.ts` | the `ctx` transport career-ops providers expect |

## Testing

```bash
npm test            # unit suites (vitest, mocked transport — no network)
npm run typecheck   # strict on src/ + test/, vendor tree excluded on purpose
npm run probe       # network smoke: loads the real provider tree and fetches one board
```

- `test/fixtures/entry-ids.json` is the **shared golden** for portal ids: the TS
  (`src/config.ts::resolveEntryId`) and Python (`galaxy/scrape/catalog.py`) implementations must
  agree — the UI sends ids across the process boundary. Add cases there, update both sides in
  the same commit.
- career-ops ships unit tests for its providers in `career-ops/tests/providers/` (mocked `ctx`).
  When a provider misbehaves, check that suite first — it documents the intended shape.
- A provider failing live is normal operating condition, not a bug to hide: the run report must
  say `failed: <error>`, never `0 jobs ok`.

## Known constraints

- The vendored tree imports `js-yaml` at module load (via `_profile-keywords.mjs`, used by the
  keyword-driven national boards). That dependency is load-bearing for `--list`, not decoration.
- Providers deliberately omit descriptions when the board doesn't ship them for free — the
  zero-token design. Descriptions GalaxyHire needs beyond that come from `enrich/`, one extra
  request per BOARD, never per job. Per-job detail fetches belong behind career-ops' opt-in
  `fetchDetails`/`detailLimit`, not here.
- `local-parser` is never loaded (it shells out); it is skipped in the adapter AND filtered from
  config entries. Keep both guards.
