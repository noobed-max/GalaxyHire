"""LaTeX résumé source and compilation (§5).

The brief asks for the résumé in LaTeX so the user can download the `.tex` and edit it, plus an
in-UI editor to fix errors before using it. Both need the corpus, which owns generation — this
router is the app-side surface the browser talks to, since the UI never addresses the corpus
directly (D1).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from core.logging import get_logger
from corpus.client import CorpusUnavailable, create_corpus_client

_log = get_logger(__name__)


class CompileRequest(BaseModel):
    source: str


def create_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["latex"])

    @router.get("/leads/{job_id}/resume.tex")
    async def resume_tex(job_id: str) -> dict:
        """LaTeX source for this job's tailored résumé.

        Content is selected from the user's own profile, never authored — see
        `services/corpus/galaxy/generation/select.py`.
        """
        try:
            source = await create_corpus_client().render_latex(job_id)
        except CorpusUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        if source is None:
            raise HTTPException(status_code=404, detail="no résumé source for that job")
        return {"job_id": job_id, "source": source, "filename": f"resume-{job_id[:12]}.tex"}

    @router.post("/latex/compile")
    async def compile_latex(req: CompileRequest) -> dict:
        """Compile edited LaTeX, returning a base64 PDF or the errors that stopped it.

        Errors come back with 200 and `ok: false` rather than a 4xx: a syntax error is the expected
        result of editing LaTeX, and the editor needs to render the log inline instead of treating it
        as a request failure.
        """
        try:
            return await create_corpus_client().compile_latex(req.source)
        except CorpusUnavailable as exc:
            return {"ok": False, "error": str(exc)}

    @router.get("/latex/available")
    async def latex_available() -> dict:
        """Whether preview is possible, so the UI can hide the button instead of failing at it."""
        try:
            return await create_corpus_client().compile_available()
        except CorpusUnavailable:
            return {"available": False, "engine": None}

    return router
