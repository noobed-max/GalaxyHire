# GalaxyHire — Corpus Backend

Python 3.13 + FastAPI. One image, two run modes: `api` serves the local corpus API and `worker`
runs scheduled ingestion.

## Dev setup

```bash
uv sync                       # create .venv, install deps + dev group
uv run pytest                 # run the test suite
uv run ruff check .           # lint
uv run uvicorn galaxy.api.app:app --reload   # run the API
```

## Layout

`galaxy/` mirrors the module map in [docs/07](../docs/07-BACKEND-SERVICES-AND-API.md):
`models · common · fetch · sources · ingestion · dedup · search · generation · llm · api · worker`.
