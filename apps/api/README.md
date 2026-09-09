# JustHireMe Backend

Python backend sidecar for JustHireMe.

## Responsibilities

- FastAPI HTTP/WebSocket API
- local CRM persistence
- source scraping
- lead quality gating
- deterministic and semantic ranking
- profile graph/vector ingestion
- resume, cover letter, and outreach generation

## Setup

From the repository root:

```bash
cd backend
uv sync --dev
```

## Tests

Windows:

```bash
backend/.venv/Scripts/python.exe -m pytest backend/tests
```

macOS/Linux:

```bash
backend/.venv/bin/python -m pytest backend/tests
```

## Notes

The backend stores local user data through SQLite, Kuzu, LanceDB, and generated files. Do not commit local app data, vector stores, graph databases, generated PDFs, API keys, cookies, or private resumes.
