"""Document text extraction for profile ingestion.

Reads raw text out of uploaded resume files (PDF via pypdf, DOCX via the docx
XML, plain .txt/.md) and provides the markdown-strip / PDF-spacing-repair
cleaners used on extracted text. No dependency on the rest of the ingestor.
"""

import re
import zipfile
from pathlib import Path

import defusedxml.ElementTree as ET

from core.logging import get_logger

_log = get_logger(__name__)

# Ceiling for a single decompressed DOCX member: guards against zip bombs where
# a tiny archive expands to gigabytes. A real resume's document.xml is well
# under this.
_MAX_DOCX_MEMBER_BYTES = 64 * 1024 * 1024

# Bounds on PDF extraction: a résumé is a handful of pages. These stop a
# decompression-bomb / thousands-of-pages PDF from hanging pypdf or exhausting
# memory and stalling the sidecar.
_MAX_PDF_PAGES = 300
_MAX_PDF_TEXT_CHARS = 200_000

# PDF text extractors often lose the spacing around a run boundary (for
# example ``inGo`` or ``fromOpenBao``) and return a wrapped continuation as a
# second physical line.  Keep this vocabulary intentionally small and
# technology-oriented: a generic camel-case splitter would corrupt ordinary
# names and prose.
_PDF_KNOWN_TOKENS = (
    "OpenSearch", "Longhorn", "OpenBao", "Milvus", "Valkey", "Kubernetes",
    "PostgreSQL", "MongoDB", "FastAPI", "NumPy", "Nginx", "Kong", "Kafka",
    "Garage", "Rust", "Go", "GCP", "AWS", "S3", "WAF", "JWT", "RS256",
    "LMDB", "SQLite", "Redis", "Docker", "Tokio", "Python", "React", "k3s",
    "Linux", "Kaf ka",  # one common glyph-position split in the supplied PDF
)
_PDF_TOKEN_SUFFIXES = (
    "serving", "pipelines", "ahead", "ingress", "gateway", "for", "with",
    "from", "into", "merged", "tuned", "and", "on", "at", "to",
)
_PDF_TOKEN_PREFIXES = (
    "on", "with", "in", "path", "putting", "of", "from", "node", "at",
    "into", "to", "using", "pairing", "behind", "asymmetric", "recall,", "keyword,",
)

_RESUME_BULLET_RE = re.compile(r"^(?P<indent>\s*)(?P<marker>(?:[-*•‣◦]|â€¢|\d+[.)]))\s+(?P<body>.*)$")
_RESUME_HEADING_RE = re.compile(
    r"(?i)^(?:#+\s*)?(?:summary|profile|objective|skills?|technical skills|"
    r"experience|work experience|employment|projects?|personal projects|"
    r"selected work|portfolio|education|certifications?|certificates|"
    r"achievements?|awards|research publications)\s*:?[\s|]*$"
)
_RESUME_DATE_ONLY_RE = re.compile(
    r"(?i)^\s*(?:(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|"
    r"jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|"
    r"nov(?:ember)?|dec(?:ember)?)\s+)?(?:19|20)\d{2}"
    r"(?:\s*[-–—/]\s*(?:(?:[A-Za-z]{3,9}\s+)?(?:19|20)\d{2}|present|current))?\s*$"
)


def _read_zip_member(archive: zipfile.ZipFile, name: str) -> bytes:
    info = archive.getinfo(name)
    if info.file_size > _MAX_DOCX_MEMBER_BYTES:
        raise ValueError(f"DOCX member {name!r} too large: {info.file_size} bytes")
    return archive.read(name)


def _docx(path: str) -> str:
    try:
        with zipfile.ZipFile(path) as archive:
            xml = _read_zip_member(archive, "word/document.xml")
        root = ET.fromstring(xml)
        ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        paragraphs = []
        for paragraph in root.findall(".//w:p", ns):
            text = "".join(node.text or "" for node in paragraph.findall(".//w:t", ns))
            if text.strip():
                paragraphs.append(text)
        return "\n".join(paragraphs)
    except Exception as exc:
        _log.error("DOCX read error for %s: %s", path, exc)
        return ""


def _text_file(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8", errors="ignore")
    except Exception as exc:
        _log.error("text resume read error for %s: %s", path, exc)
        return ""


def _document(path: str) -> str:
    suffix = Path(path).suffix.lower()
    if suffix == ".pdf":
        return _pdf(path)
    if suffix == ".docx":
        return _docx(path)
    if suffix in {".txt", ".md"}:
        return _text_file(path)
    if suffix == ".doc":
        _log.error("Legacy .doc resume uploads are not supported; export the resume as PDF or DOCX")
        return ""
    return _text_file(path)


def _pdf(path: str) -> str:
    try:
        from pypdf import PdfReader
        # strict=False: tolerate the malformed-but-readable PDFs real users upload
        # instead of raising on the first spec violation.
        reader = PdfReader(path, strict=False)
        parts: list[str] = []
        total = 0
        truncated = False
        for index, page in enumerate(reader.pages):
            if index >= _MAX_PDF_PAGES:
                _log.warning("PDF exceeds %d pages; reading only the first %d: %s", _MAX_PDF_PAGES, _MAX_PDF_PAGES, path)
                truncated = True
                break
            try:
                chunk = page.extract_text() or ""
            except Exception as exc:
                # One bad page must not abort extraction of the rest.
                _log.warning("PDF page %d extract error (%s): %s", index, path, exc)
                continue
            parts.append(chunk)
            total += len(chunk)
            if total >= _MAX_PDF_TEXT_CHARS:
                _log.warning("PDF text exceeded %d chars; truncating: %s", _MAX_PDF_TEXT_CHARS, path)
                truncated = True
                break
        text = preprocess_resume_text("\n".join(parts))
        if not text.strip() and not truncated:
            _log.warning("PDF has no extractable text (may be scanned/image-only): %s", path)
        return text
    except Exception as exc:
        _log.error("PDF read error for %s: %s", path, exc)
        return ""


def _strip_md(text: str) -> str:
    text = re.sub(r"`([^`]+)`", r"\1", text or "")
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"\*([^*]+)\*", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = text.replace("→", "->").replace("·", "-")
    text = re.sub(r"^\s*(?:[-*]|•|â€¢)\s*", "", text)
    text = _repair_pdf_spacing(text)
    return re.sub(r"\s+", " ", text).strip()


def _repair_pdf_spacing(text: str) -> str:
    """Repair a few deterministic PDF glyph/word-boundary artefacts.

    This is deliberately not a general word segmentation algorithm.  Only
    boundaries involving a known resume token are repaired, plus the highly
    characteristic ``Kaf ka`` split.  That prevents us from turning a proper
    name or an ordinary word into two invented tokens.
    """
    value = str(text or "")
    value = re.sub(r"\bKaf\s+ka\b", "Kafka", value, flags=re.I)
    # PDF extraction occasionally inserts a space after a single-letter
    # acronym (``A. I.``-style output).  Keep the historical conservative
    # repairs while avoiding a broad whitespace collapse here.
    value = re.sub(r"\b([A-Z]\.[A-Z])\s+([a-z]{2,})\b", r"\1\2", value)
    value = re.sub(r"\b([A-Z])\.\s+([A-Za-z]{2,})\b", r"\1.\2", value)
    value = re.sub(r"\b([BFV])\s+([a-z]{2,})\b", r"\1\2", value)

    # Split a known token from a preceding/following word only when the
    # extractor has clearly omitted the boundary.  Longest-first matters for
    # tokens such as OpenSearch and PostgreSQL.
    for token in sorted({t for t in _PDF_KNOWN_TOKENS if " " not in t}, key=len, reverse=True):
        escaped = re.escape(token)
        prefixes = "|".join(re.escape(prefix) for prefix in _PDF_TOKEN_PREFIXES)
        value = re.sub(rf"(?i)\b(?:{prefixes})(?={escaped}(?![A-Za-z0-9]))", lambda match: f"{match.group(0)} ", value)
        # ``GoLang`` is a legitimate single token; do not split it while
        # repairing ``inGo``.  Other known technology names followed by a
        # lowercase prose word are safe boundary candidates (``S3serving``).
        if token != "Go":
            suffixes = "|".join(re.escape(word) for word in _PDF_TOKEN_SUFFIXES)
            value = re.sub(rf"(?<={escaped})(?=(?:{suffixes})\b)", " ", value, flags=re.I)
    # Metrics are the only numeric boundaries worth repairing generically;
    # requiring a scale suffix avoids corrupting technologies such as S3,
    # RS256, and P99.
    value = re.sub(r"(?<=[a-z])(?=\d+(?:k|m|g)\b)", " ", value, flags=re.I)
    value = re.sub(r"([,;)])(?=[A-Za-z])", r"\1 ", value)
    return value


def _line_is_boundary(line: str) -> bool:
    """Return whether a non-bullet line must remain independent."""
    clean = str(line or "").strip()
    if not clean:
        return True
    # Contact rows must never be joined to the candidate name immediately
    # above them. Keep this narrow so URLs inside explicit accomplishment
    # bullets remain ordinary bullet content.
    if (
        re.search(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", clean, re.I)
        or re.search(r"\b(?:https?://|www\.|linkedin\.com/|github\.com/)", clean, re.I)
        or re.fullmatch(r"[+()\d][\d\s().+-]{7,}", clean)
    ):
        return True
    if _RESUME_HEADING_RE.match(clean) or _RESUME_DATE_ONLY_RE.match(clean):
        return True
    # A compact role/company row is metadata even when it has no date.  Do not
    # let an unmarked LLM line such as ``built the API`` attach to it.
    if not re.match(r"^\s*(?:[-*•‣◦]|â€¢|\d+[.)])\s+", clean) and (
        # ``Role at Company`` is an entity row only when it looks like a
        # compact header, not an accomplishment sentence such as ``aimed at
        # machine-learning workloads``.
        (re.search(r"\s+at\s+[A-Z]", clean) and len(clean.split()) <= 7 and not re.match(
            r"(?i)^(?:built|created|developed|designed|engineered|implemented|"
            r"integrated|launched|shipped|automated|managed|drove|wrote|"
            r"improved|reduced|increased|optimized|aimed|sustained|held|"
            r"maintained|achieved|delivered)\b",
            clean,
        ))
        or re.search(r"\s+[|–—]\s*", clean)
        or (re.search(r"\b(?:19|20)\d{2}\b|\b(?:present|current)\b", clean, re.I)
            and re.search(r"[-–—|]", clean))
    ):
        return True
    # Category/skill rows and compact project/entity headers are not wrapped
    # bullet prose.  A colon in a sentence is allowed when it follows a verb.
    if re.match(r"^[A-Za-z][A-Za-z0-9 /&+.-]{1,35}:\s*\S", clean) and not re.match(
        r"(?i)^(?:built|implemented|designed|created|led|drove|wrote|improved|"
        r"developed|deployed|hardened|managed|reduced|increased|optimized)\b",
        clean,
    ):
        return True
    return bool(re.match(r"^[A-Z][\w.+-]*(?:\s+[A-Z][\w.+-]*){0,8}\s*:\s*", clean))


def _should_join_continuation(previous: str, current: str, indent: int) -> bool:
    """Conservatively identify a wrapped continuation after an explicit bullet."""
    if _line_is_boundary(current):
        return False
    previous = previous.strip()
    current = current.strip()
    if not previous or not current or _line_is_boundary(previous) or re.match(r"^(?:[-*•‣◦]|â€¢|\d+[.)])\s+", current):
        return False
    # Layout-aware extractors retain indentation for wrapped lines.  This is
    # the strongest signal and is safe even when the preceding line is short.
    if indent > 0:
        return True
    # pypdf's plain mode strips indentation.  Join only obvious fragments:
    # unfinished conjunction/preposition, a lower-case continuation, or an
    # acronym/metric at the physical line boundary.  A complete sentence plus
    # another capitalized line therefore remains two points.
    if re.search(r"(?:\b(?:and|or|but|to|for|with|from|where|than|rather|at|into|of|by|on|in|a|an|the)\s*)$", previous, re.I):
        return True
    if current[:1].islower() and not re.search(r"[.!?;:]\s*$", previous):
        return True
    return bool(re.search(r"\b[A-Z][A-Z0-9-]{1,}\s*$", previous) and not re.search(r"[.!?;:]\s*$", previous))


def preprocess_resume_text(text: str) -> str:
    """Normalize extracted resume text while preserving semantic line units.

    Explicit bullet markers start a new unit.  An indented or unmistakably
    unfinished following line is appended to that unit with a space.  Headers,
    dates, skill rows, and entity/project rows always reset the unit, so this
    does not flatten the resume's section structure.
    """
    raw = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    output: list[str] = []
    active_index: int | None = None
    active_body = ""

    def flush() -> None:
        nonlocal active_index, active_body
        if active_index is not None:
            output[active_index] = active_body.strip()
        active_index = None
        active_body = ""

    for raw_line in raw.split("\n"):
        if not raw_line.strip():
            flush()
            if output and output[-1] != "":
                output.append("")
            continue
        line = _repair_pdf_spacing(raw_line).strip()
        match = _RESUME_BULLET_RE.match(line)
        if match:
            flush()
            # Keep the explicit marker (downstream cleaners can remove it),
            # while canonicalizing only the marker's surrounding whitespace.
            active_body = f"{match.group('marker')} {match.group('body').strip()}".strip()
            output.append(active_body)
            active_index = len(output) - 1
            continue

        if active_index is not None and _should_join_continuation(active_body, line, len(raw_line) - len(raw_line.lstrip())):
            # A hyphen at the physical edge is usually a line-break marker,
            # not part of the word (``interrup-`` + ``tion``).  Remove only
            # when the continuation begins lowercase; intentional hyphenated
            # compounds followed by a new capitalized phrase are preserved.
            if active_body.rstrip().endswith("-") and line[:1].islower():
                active_body = f"{active_body.rstrip()[:-1]}{line}".strip()
            else:
                active_body = f"{active_body} {line}".strip()
            continue

        flush()
        output.append(line)

        # Some LLM providers return the semantic array as newline-delimited
        # prose without retaining the source marker.  Join only the same
        # unmistakable unfinished forms as above, deliberately ignoring
        # indentation here: an indented line after an unmarked entity/header
        # row is not enough evidence that it is a bullet continuation.
        if len(output) >= 2 and output[-2] and _should_join_continuation(output[-2], line, 0):
            previous = output[-2].rstrip()
            if previous.endswith("-") and line[:1].islower():
                output[-2] = f"{previous[:-1]}{line}".strip()
            else:
                output[-2] = f"{previous} {line}".strip()
            output.pop()

    flush()
    # Avoid accumulating blank lines at either edge; retain interior blanks as
    # useful section boundaries for the heuristic parser.
    while output and output[0] == "":
        output.pop(0)
    while output and output[-1] == "":
        output.pop()
    return "\n".join(output)
