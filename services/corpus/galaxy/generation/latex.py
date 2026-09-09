"""LaTeX résumé rendering (§5).

The brief asks for LaTeX specifically, so the user can download the `.tex`, edit it, and compile it
themselves. The other renderers in `render.py` (Markdown, HTML, .docx, PDF) all produce finished
artifacts; this one produces *source*, which changes what matters:

  * **Escaping is correctness, not cosmetics.** A single unescaped `&`, `%`, `$`, `#`, or `_` in a
    user's job title or bullet makes the document fail to compile — or worse, compile into something
    subtly wrong. Every value interpolated here goes through `escape_latex`.
  * **The preamble has to compile on a normal TeX install.** The reference template used
    `fontawesome5`, `XCharter`, and `glyphtounicode`; a missing package is a hard error, and a
    résumé the user cannot compile is worse than a plain one. This preamble sticks to packages in
    every TeX Live/MiKTeX base install, and uses text labels rather than icon glyphs — which is also
    better for ATS parsing, since an icon font carries no extractable text.

Structure follows the same ATS-safe rules as the other renderers: single column, standard section
headings, real text, contact details in the body, no tables or text boxes.
"""

from __future__ import annotations

from galaxy.generation.models import ResumeDoc

# Characters that change meaning in LaTeX. Order matters only for the backslash, which is handled
# first inside the loop so its replacement isn't re-escaped.
_ESCAPES = {
    "\\": r"\textbackslash{}",
    "{": r"\{",
    "}": r"\}",
    "^": r"\textasciicircum{}",
    "~": r"\textasciitilde{}",
    "_": r"\_",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    # Symbols people paste into bullets that are maths-mode in LaTeX.
    "±": r"$\pm$",
    "→": r"$\rightarrow$",
    "←": r"$\leftarrow$",
    "≤": r"$\leq$",
    "≥": r"$\geq$",
    # Smart quotes and dashes compile, but the ASCII forms are what TeX renders correctly.
    "‘": "`",
    "’": "'",
    "“": "``",
    "”": "''",
    "–": "--",
    "—": "---",
}

_ALLOWED_URL_SCHEMES = ("http://", "https://", "mailto:")


def escape_latex(text: object) -> str:
    """Make arbitrary user text safe to interpolate into LaTeX.

    Everything the user typed passes through here. Non-strings become empty rather than raising:
    a résumé that renders without a missing phone number beats one that 500s during generation.
    """
    if not isinstance(text, str):
        return ""
    return "".join(_ESCAPES.get(ch, ch) for ch in text)


def sanitize_url(url: object) -> str:
    """Return a URL safe for `\\href{}`, or "" if it isn't one.

    URLs are *not* LaTeX-escaped — escaping would corrupt them — so they are instead restricted by
    scheme. Without that, a `\\href{javascript:...}` or a URL containing `}` could break out of the
    macro argument and inject arbitrary LaTeX into a document the user is about to compile.
    """
    if not isinstance(url, str):
        return ""
    cleaned = url.strip()
    if not cleaned or not cleaned.lower().startswith(_ALLOWED_URL_SCHEMES):
        return ""
    # A closing brace or backslash would terminate or hijack the \href argument.
    if any(ch in cleaned for ch in "{}\\%#"):
        return ""
    return cleaned


PREAMBLE = r"""\documentclass[10pt]{article}
\usepackage[letterpaper, top=0.5in, bottom=0.5in, left=0.6in, right=0.6in]{geometry}
\usepackage[T1]{fontenc}
\usepackage[utf8]{inputenc}
\usepackage{enumitem}
\usepackage[hidelinks]{hyperref}
\usepackage{titlesec}
\raggedright
\pagestyle{empty}

% Section headings: bold, with a rule under them. Standard names so ATS parsers recognise them.
\titleformat{\section}{\bfseries\large}{}{0pt}{}[\vspace{2pt}\titlerule\vspace{-6pt}]
\setlist[itemize]{itemsep=-2pt, leftmargin=12pt, topsep=2pt}
"""


def _contact_line(doc: ResumeDoc) -> str:
    """Contact details as plain text with linked URLs.

    Deliberately no icon font: `fontawesome5` isn't in a base TeX install, and an icon conveys
    nothing to a résumé parser. A label like "GitHub:" is both compilable everywhere and readable
    by an ATS.
    """
    contact = doc.contact or {}
    parts: list[str] = []
    for key in ("location", "phone"):
        value = escape_latex(contact.get(key))
        if value:
            parts.append(value)

    email = contact.get("email")
    if isinstance(email, str) and email.strip():
        url = sanitize_url(f"mailto:{email.strip()}")
        linked = f"\\href{{{url}}}{{{escape_latex(email.strip())}}}"
        parts.append(linked if url else escape_latex(email))

    links = contact.get("links") or {}
    if isinstance(links, dict):
        for label, raw in links.items():
            url = sanitize_url(raw)
            if url:
                shown = escape_latex(_display_url(url))
                parts.append(f"{escape_latex(str(label))}: \\href{{{url}}}{{{shown}}}")

    return " $|$ ".join(p for p in parts if p)


def _display_url(url: str) -> str:
    """Shorten a URL for display — the full thing wraps badly in a one-line contact header."""
    trimmed = url.split("://", 1)[-1]
    return trimmed[:-1] if trimmed.endswith("/") else trimmed


def _section(title: str, body: str) -> str:
    if not body.strip():
        return ""
    return f"\n\\section*{{{escape_latex(title)}}}\n{body}\n"


def _bullets(items: list) -> str:
    """An itemize block, or "" when there is nothing to list.

    An empty `itemize` is a LaTeX compile error, so the emptiness check is required, not tidiness.
    """
    lines = [escape_latex(i) for i in items or [] if isinstance(i, str) and i.strip()]
    if not lines:
        return ""
    inner = "\n".join(f"  \\item {line}" for line in lines)
    return f"\\begin{{itemize}}\n{inner}\n\\end{{itemize}}"


def _experience(entries: list) -> str:
    blocks: list[str] = []
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        role = escape_latex(entry.get("role") or entry.get("title"))
        company = escape_latex(entry.get("company"))
        period = escape_latex(entry.get("period") or entry.get("dates"))
        if not (role or company):
            continue
        heading = " -- ".join(p for p in (f"\\textbf{{{role}}}" if role else "", company) if p)
        if period:
            heading += f" \\hfill {period}"
        # `description` may be prose or a bullet list; both come straight from the user.
        raw = entry.get("bullets") or entry.get("description") or []
        items = raw if isinstance(raw, list) else [raw]
        blocks.append(f"{heading}\n{_bullets(items)}".rstrip())
    return "\n\n\\vspace{4pt}\n".join(blocks)


def _projects(projects: list) -> str:
    blocks: list[str] = []
    for project in projects or []:
        raw_title = getattr(project, "title", None)
        if raw_title is None and isinstance(project, dict):
            raw_title = project.get("title")
        title = escape_latex(raw_title)
        if not title:
            continue
        bullets = getattr(project, "bullets", None)
        if bullets is None and isinstance(project, dict):
            bullets = project.get("bullets")
        blocks.append(f"\\textbf{{{title}}}\n{_bullets(bullets or [])}".rstrip())
    return "\n\n\\vspace{4pt}\n".join(blocks)


def _education(entries: list) -> str:
    lines: list[str] = []
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        school = escape_latex(entry.get("school") or entry.get("institution"))
        degree = escape_latex(entry.get("degree") or entry.get("qualification"))
        year = escape_latex(entry.get("year") or entry.get("period"))
        if not (school or degree):
            continue
        line = " -- ".join(p for p in (f"\\textbf{{{school}}}" if school else "", degree) if p)
        if year:
            line += f" \\hfill {year}"
        lines.append(line)
    return " \\\\\n".join(lines)


def to_latex(doc: ResumeDoc) -> str:
    """Render an assembled résumé as compilable LaTeX source.

    Contains only content that was already in the user's profile — this renders the output of
    `select.py`, which chooses among real projects and skills and never authors new text (§5).
    """
    name = escape_latex(doc.name) or "Résumé"
    header = f"\\centerline{{\\Large \\textbf{{{name}}}}}"
    contact = _contact_line(doc)
    if contact:
        header += f"\n\\vspace{{3pt}}\n\\centerline{{{contact}}}"

    body = header + "\n"
    body += _section("Summary", escape_latex(doc.summary))
    if doc.skills:
        skills = ", ".join(escape_latex(s) for s in doc.skills if isinstance(s, str) and s.strip())
        body += _section("Skills", skills)
    body += _section("Experience", _experience(doc.experience))
    body += _section("Projects", _projects(doc.projects))
    body += _section("Education", _education(doc.education))

    return f"{PREAMBLE}\n\\begin{{document}}\n{body}\n\\end{{document}}\n"
