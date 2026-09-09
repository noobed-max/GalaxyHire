"""Resume renderers (docs/05 §6).

All renderers emit ATS-safe output: single column, standard headings, real text (no tables,
text-boxes, or images-as-text), contact in the body. Four formats, all pure-Python (no browser):
Markdown, HTML, .docx (python-docx), PDF (fpdf2), plus LaTeX source (see `latex.py`).
"""

from __future__ import annotations

import html
import io

from galaxy.generation.models import ResumeDoc


def _contact_line(doc: ResumeDoc) -> str:
    c = doc.contact
    parts = [c.get("email"), c.get("phone"), c.get("location"), *(c.get("links") or [])]
    return " · ".join(p for p in parts if p)


# --- Markdown --------------------------------------------------------------


def to_markdown(doc: ResumeDoc) -> str:
    lines: list[str] = []
    if doc.name:
        lines.append(f"# {doc.name}")
    contact = _contact_line(doc)
    if contact:
        lines.append(contact)
    lines.append("")
    if doc.summary:
        lines += ["## Summary", doc.summary, ""]
    if doc.skills:
        lines += ["## Skills", ", ".join(doc.skills), ""]
    if doc.experience:
        lines.append("## Experience")
        for e in doc.experience:
            head = " — ".join(x for x in [e.get("title"), e.get("company"), e.get("dates")] if x)
            lines.append(f"**{head}**" if head else "")
            for b in e.get("bullets") or []:
                lines.append(f"- {b}")
            lines.append("")
    if doc.projects:
        lines.append("## Projects")
        for p in doc.projects:
            lines.append(f"**{p.title}**")
            for b in p.bullets:
                lines.append(f"- {b}")
            lines.append("")
    if doc.education:
        lines.append("## Education")
        for ed in doc.education:
            head = " — ".join(str(ed.get(k, "")) for k in ("degree", "school", "dates") if ed.get(k))
            if head:
                lines.append(f"- {head}")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


# --- HTML (ATS-safe, single column) ----------------------------------------


def to_html(doc: ResumeDoc) -> str:
    def esc(s: str) -> str:
        return html.escape(s or "")

    out = [
        "<!doctype html><html><head><meta charset='utf-8'>",
        "<style>body{font-family:Arial,Helvetica,sans-serif;font-size:11pt;max-width:720px;"
        "margin:2em auto;color:#111;line-height:1.4}h1{font-size:18pt;margin:0}h2{font-size:12pt;"
        "border-bottom:1px solid #999;margin-top:1.2em}ul{margin:.3em 0}li{margin:.15em 0}"
        ".contact{color:#333}</style></head><body>",
    ]
    if doc.name:
        out.append(f"<h1>{esc(doc.name)}</h1>")
    contact = _contact_line(doc)
    if contact:
        out.append(f"<div class='contact'>{esc(contact)}</div>")
    if doc.summary:
        out.append(f"<h2>Summary</h2><p>{esc(doc.summary)}</p>")
    if doc.skills:
        out.append(f"<h2>Skills</h2><p>{esc(', '.join(doc.skills))}</p>")
    if doc.experience:
        out.append("<h2>Experience</h2>")
        for e in doc.experience:
            head = " — ".join(x for x in [e.get("title"), e.get("company"), e.get("dates")] if x)
            out.append(f"<p><strong>{esc(head)}</strong></p>")
            bl = "".join(f"<li>{esc(b)}</li>" for b in e.get("bullets") or [])
            if bl:
                out.append(f"<ul>{bl}</ul>")
    if doc.projects:
        out.append("<h2>Projects</h2>")
        for p in doc.projects:
            out.append(f"<p><strong>{esc(p.title)}</strong></p>")
            bl = "".join(f"<li>{esc(b)}</li>" for b in p.bullets)
            if bl:
                out.append(f"<ul>{bl}</ul>")
    if doc.education:
        out.append("<h2>Education</h2><ul>")
        for ed in doc.education:
            head = " — ".join(str(ed.get(k, "")) for k in ("degree", "school", "dates") if ed.get(k))
            if head:
                out.append(f"<li>{esc(head)}</li>")
        out.append("</ul>")
    out.append("</body></html>")
    return "".join(out)


# --- .docx (python-docx) ---------------------------------------------------


def to_docx(doc: ResumeDoc) -> bytes:
    from docx import Document
    from docx.shared import Pt

    d = Document()
    if doc.name:
        h = d.add_heading(doc.name, level=0)
        h.runs[0].font.size = Pt(18)
    contact = _contact_line(doc)
    if contact:
        d.add_paragraph(contact)

    def section(title: str):
        d.add_heading(title, level=1)

    if doc.summary:
        section("Summary")
        d.add_paragraph(doc.summary)
    if doc.skills:
        section("Skills")
        d.add_paragraph(", ".join(doc.skills))
    if doc.experience:
        section("Experience")
        for e in doc.experience:
            head = " — ".join(x for x in [e.get("title"), e.get("company"), e.get("dates")] if x)
            if head:
                d.add_paragraph(head).runs[0].bold = True
            for b in e.get("bullets") or []:
                d.add_paragraph(b, style="List Bullet")
    if doc.projects:
        section("Projects")
        for p in doc.projects:
            d.add_paragraph(p.title).runs[0].bold = True
            for b in p.bullets:
                d.add_paragraph(b, style="List Bullet")
    if doc.education:
        section("Education")
        for ed in doc.education:
            head = " — ".join(str(ed.get(k, "")) for k in ("degree", "school", "dates") if ed.get(k))
            if head:
                d.add_paragraph(head, style="List Bullet")

    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


# --- PDF (fpdf2, single column, core fonts) --------------------------------


def to_pdf(doc: ResumeDoc) -> bytes:
    from fpdf import FPDF

    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_margins(15, 15, 15)
    w = pdf.epw  # effective page width

    def _t(s: str) -> str:
        # fpdf core fonts are latin-1; drop unencodable chars rather than crash
        return (s or "").encode("latin-1", "replace").decode("latin-1")

    if doc.name:
        pdf.set_font("Helvetica", "B", 18)
        pdf.multi_cell(w, 9, _t(doc.name))
    contact = _contact_line(doc)
    if contact:
        pdf.set_font("Helvetica", "", 9)
        pdf.multi_cell(w, 5, _t(contact))
    pdf.ln(1)

    def heading(title: str):
        pdf.ln(2)
        pdf.set_font("Helvetica", "B", 12)
        pdf.multi_cell(w, 6, _t(title))
        pdf.set_draw_color(150, 150, 150)
        pdf.line(pdf.l_margin, pdf.get_y(), pdf.l_margin + w, pdf.get_y())
        pdf.ln(1)

    def body(text: str, bold: bool = False, bullet: bool = False):
        pdf.set_font("Helvetica", "B" if bold else "", 10.5)
        prefix = "  •  " if bullet else ""
        pdf.multi_cell(w, 5, _t(prefix + text))

    if doc.summary:
        heading("Summary")
        body(doc.summary)
    if doc.skills:
        heading("Skills")
        body(", ".join(doc.skills))
    if doc.experience:
        heading("Experience")
        for e in doc.experience:
            head = " — ".join(x for x in [e.get("title"), e.get("company"), e.get("dates")] if x)
            if head:
                body(head, bold=True)
            for b in e.get("bullets") or []:
                body(b, bullet=True)
    if doc.projects:
        heading("Projects")
        for p in doc.projects:
            body(p.title, bold=True)
            for b in p.bullets:
                body(b, bullet=True)
    if doc.education:
        heading("Education")
        for ed in doc.education:
            head = " — ".join(str(ed.get(k, "")) for k in ("degree", "school", "dates") if ed.get(k))
            if head:
                body(head, bullet=True)

    return bytes(pdf.output())


# LaTeX lives in its own module: unlike these renderers it emits *source* the user compiles, so it
# carries escaping rules (a stray `&` is a build failure) that nothing else here needs.
from galaxy.generation.latex import to_latex  # noqa: E402

RENDERERS = {
    "md": (to_markdown, "text/markdown", "md"),
    "markdown": (to_markdown, "text/markdown", "md"),
    "html": (to_html, "text/html", "html"),
    "docx": (to_docx, "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "docx"),
    "pdf": (to_pdf, "application/pdf", "pdf"),
    # §5: the user downloads the .tex to edit and compile themselves.
    "tex": (to_latex, "application/x-tex", "tex"),
    "latex": (to_latex, "application/x-tex", "tex"),
}
