"""Resume → Profile draft via the LLM (docs/03 §7, docs/10 Phase 3).

Parses pasted resume text into a structured Profile the user then reviews and saves. The LLM
only *extracts* — it never invents; anything it can't find is left blank. Returns a draft;
persistence is an explicit `PUT /profile` after the user confirms.
"""

from __future__ import annotations

import json
from uuid import UUID

import structlog

from galaxy.llm.client import LLMClient
from galaxy.models.profile import Identity, Profile, Project, Skill

log = structlog.get_logger(__name__)

_EXTRACT_TOOL = {
    "type": "function",
    "function": {
        "name": "extract_profile",
        "description": "Extract structured profile fields from a resume. Omit anything not present.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "email": {"type": "string"},
                "phone": {"type": "string"},
                "links": {"type": "array", "items": {"type": "string"}},
                "locations": {"type": "array", "items": {"type": "string"}},
                "skills": {"type": "array", "items": {"type": "string"}},
                "projects": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "bullets": {"type": "array", "items": {"type": "string"}},
                            "skills": {"type": "array", "items": {"type": "string"}},
                            "role_tags": {
                                "type": "array", "items": {"type": "string"},
                                "description": "1-3 field/domain tags, e.g. 'software', 'data'",
                            },
                        },
                    },
                },
                "experience": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "company": {"type": "string"},
                            "dates": {"type": "string"},
                            "bullets": {"type": "array", "items": {"type": "string"}},
                            "tags": {
                                "type": "array", "items": {"type": "string"},
                                "description": "1-3 field/domain tags, e.g. 'software', 'nursing'",
                            },
                        },
                    },
                },
                "education": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "institution": {"type": "string"},
                            "degree": {"type": "string"},
                            "dates": {"type": "string"},
                        },
                    },
                },
                "certifications": {"type": "array", "items": {"type": "string"}},
                "publications": {"type": "array", "items": {"type": "string"}},
                "anything_else": {
                    "type": "string",
                    "description": "facts that fit nowhere else — work auth/visa, notice period, salary",
                },
            },
        },
    },
}

_SYSTEM = (
    "You extract structured data from a resume. Call extract_profile exactly once. "
    "Copy facts verbatim; never invent names, dates, employers, or metrics. Leave a field out "
    "if the resume does not contain it. Preserve project and experience bullet wording as written. "
    "For each project and job, infer 1-3 short tags naming its field/domain (e.g. 'software', "
    "'nursing', 'accounting') so the same person's different-field experience can be told apart."
)


def _strs(seq) -> list[str]:
    return [str(x).strip() for x in (seq or []) if str(x).strip()]


def _to_profile(user_id: UUID, data: dict) -> Profile:
    identity = Identity(
        name=data.get("name") or None,
        email=data.get("email") or None,
        phone=data.get("phone") or None,
        links=[str(x) for x in (data.get("links") or [])],
        locations=[str(x) for x in (data.get("locations") or [])],
    )
    skills = [Skill(name=str(s).strip()) for s in (data.get("skills") or []) if str(s).strip()]
    projects = [
        Project(
            title=str(p.get("title", "")).strip(),
            bullets=[str(b) for b in (p.get("bullets") or [])],
            skills=_strs(p.get("skills")),
            role_tags=_strs(p.get("role_tags")),
            verbatim=True,  # pasted from a real resume → use as-is (docs/05 §3)
        )
        for p in (data.get("projects") or [])
        if isinstance(p, dict) and str(p.get("title", "")).strip()
    ]
    # experience carries per-role field `tags` so multi-field people can be sliced per job (docs/11)
    experience = [
        {
            "title": str(e.get("title", "")).strip(),
            "company": str(e.get("company", "")).strip(),
            "dates": str(e.get("dates", "")).strip(),
            "bullets": [str(b) for b in (e.get("bullets") or [])],
            "tags": _strs(e.get("tags")),
        }
        for e in (data.get("experience") or [])
        if isinstance(e, dict) and (str(e.get("title", "")).strip() or str(e.get("company", "")).strip())
    ]
    # store education under "school" (what the renderers read) from the LLM's "institution"
    education = [
        {
            "school": str(ed.get("institution") or ed.get("school") or "").strip(),
            "degree": str(ed.get("degree", "")).strip(),
            "dates": str(ed.get("dates", "")).strip(),
        }
        for ed in (data.get("education") or [])
        if isinstance(ed, dict) and (ed.get("institution") or ed.get("school") or ed.get("degree"))
    ]
    certifications = [{"name": c} for c in _strs(data.get("certifications"))]
    publications = [{"title": p} for p in _strs(data.get("publications"))]
    return Profile(
        user_id=user_id,
        identity=identity,
        skills=skills,
        projects=projects,
        experience=experience,
        education=education,
        certifications=certifications,
        publications=publications,
        anything_else=str(data.get("anything_else") or "").strip(),
    )


def extract_resume_text(filename: str, data: bytes) -> str:
    """Best-effort plain-text extraction from an uploaded resume (docs/11 §W1.2).

    Handles .pdf (pypdf), .docx (python-docx), and .txt/.md (decode). Capped to ~15k chars.
    """
    import io

    name = (filename or "").lower()
    text = ""
    if name.endswith(".pdf"):
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        text = "\n".join((page.extract_text() or "") for page in reader.pages)
    elif name.endswith(".docx"):
        from docx import Document

        doc = Document(io.BytesIO(data))
        text = "\n".join(p.text for p in doc.paragraphs)
    else:
        text = data.decode("utf-8", errors="replace")
    return text[:15000]


def parse_tool_json(raw: str) -> dict:
    """Lenient parse of the extract_profile arguments (models wrap JSON in prose/fences)."""
    if not raw or not raw.strip():
        return {}
    for candidate in (raw, raw[raw.find("{") : raw.rfind("}") + 1]):
        try:
            return json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
    return {}


async def import_from_text(user_id: UUID, text: str, client: LLMClient | None = None) -> Profile:
    """Return a Profile draft extracted from resume text (not persisted)."""
    client = client or LLMClient()
    resp = await client.chat(
        [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": text[:12000]},
        ],
        tools=[_EXTRACT_TOOL],
        max_tokens=2000,
    )
    message = resp.get("choices", [{}])[0].get("message", {})
    raw = ""
    tool_calls = message.get("tool_calls") or []
    if tool_calls:
        raw = tool_calls[0].get("function", {}).get("arguments", "")
    raw = raw or message.get("content") or ""
    data = parse_tool_json(raw)
    return _to_profile(user_id, data)
