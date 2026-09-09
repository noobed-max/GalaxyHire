# @galaxyhire/contract

The boundary between scrapers and the corpus: **one job shape, nothing else.**

The contract intentionally contains only the wire format between the scraper and corpus
processes. Connector selection and portal configuration live with the scraper and application;
they are not duplicated here, which keeps this package from drifting away from its consumers.

## The envelope

`JobEnvelope` is *not* a new format. It is the JSON serialization of the corpus service's existing
`SourceObservation` pydantic model (`services/corpus/galaxy/models/job.py`), field for field. So
the Python side needs no translation layer:

```python
SourceObservation.model_validate(envelope)   # just works
```

That is why the field names are `snake_case` in TypeScript. They are the contract, not a style
slip — don't "fix" them.

```ts
import { validateEnvelope, partitionEnvelopes } from '@galaxyhire/contract';

const { valid, rejected } = partitionEnvelopes(rows);   // never throws
```

`validateEnvelope` checks only what the corpus cannot recover on its own: the identity triple
(`site` + `source_job_id` + `url`), a usable title, and well-formed timestamps. Everything else is
optional because the corpus normalizer derives it at ingest — a source that can't supply a salary
is normal, not an error. `fields.company` may legitimately be empty; several list-page sources
cannot expose it at that level and the corpus backfills it.

`site` is an open string by design: it is a career-ops **provider id** (the vendored tree ships
~80), the corpus stores it as TEXT, and the Python `Site` enum admits any well-formed id via
`_missing_`. Do not pin a copy of the provider list here — the vendor tree is the source of
truth, and a second list is a drift invitation.

`fields.extra` is the extension point. GalaxyHire writes `extra.freshness`
(`"posted" | "first_seen"`) there, so the 24h claim survives to the UI and can be badge-labeled
honestly; it also carries career-ops' trust signals and `location_raw`. Adding a field goes in
`extra`, never by widening the identity triple.

`partitionEnvelopes` isolates bad rows instead of failing the batch, matching the failure
isolation the ingest pipeline already applies to whole sources.

## Test

```bash
npm test          # envelope contract
npm run typecheck
```
