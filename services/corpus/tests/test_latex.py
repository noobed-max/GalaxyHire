"""LaTeX résumé rendering (§5).

Unlike the other renderers, this one emits *source* the user will compile, so the failure mode is a
document that won't build. Escaping is therefore the bulk of these tests — and where a real LaTeX
install is present, one test compiles the output for real, because "looks escaped" and "compiles"
are not the same claim.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from galaxy.generation.latex import escape_latex, sanitize_url, to_latex
from galaxy.generation.models import ResumeDoc, SelectedProject


def doc(**over) -> ResumeDoc:
    base = dict(
        name="Alex Mercer",
        contact={
            "email": "alex@example.com",
            "phone": "+1 555 0192",
            "location": "Berlin, Germany",
            "links": {"GitHub": "https://github.com/alex", "Portfolio": "https://alex.dev"},
        },
        summary="Platform engineer with 6 years building Kubernetes tooling.",
        skills=["Python", "Go", "Kubernetes"],
        projects=[
            SelectedProject(
                project_id="p1",
                title="K8s autoscaling operator",
                bullets=["Cut cluster cost 30%", "Wrote the controller in Go"],
                relevance=0.9,
                matched_skills=["Go", "Kubernetes"],
            )
        ],
        experience=[
            {
                "role": "Platform Engineer",
                "company": "Acme & Co",
                "period": "2022 -- present",
                "bullets": ["Ran 12 EKS clusters", "Reduced p99 latency 40%"],
            }
        ],
        education=[{"school": "TU Berlin", "degree": "BSc Computer Science", "year": "2021"}],
    )
    base.update(over)
    return ResumeDoc(**base)


class TestEscaping:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("50% growth", r"50\% growth"),
            ("Acme & Co", r"Acme \& Co"),
            ("cost_center", r"cost\_center"),
            ("C#", r"C\#"),
            ("$100k budget", r"\$100k budget"),
            ("a{b}c", r"a\{b\}c"),
            ("x^2", r"x\textasciicircum{}2"),
            ("~approx", r"\textasciitilde{}approx"),
            ("back\\slash", r"back\textbackslash{}slash"),
        ],
    )
    def test_escapes_every_latex_metacharacter(self, raw: str, expected: str):
        # Any one of these unescaped is a compile failure, not a cosmetic issue.
        assert escape_latex(raw) == expected

    def test_converts_symbols_that_would_need_maths_mode(self):
        assert escape_latex("±5") == r"$\pm$5"
        assert escape_latex("a → b") == r"a $\rightarrow$ b"

    def test_normalises_smart_punctuation(self):
        # Pasted from a word processor; TeX renders the ASCII forms correctly.
        assert escape_latex("don’t") == "don't"
        assert escape_latex("“quoted”") == "``quoted''"
        assert escape_latex("2020–2024") == "2020--2024"

    def test_non_strings_become_empty_rather_than_raising(self):
        # A résumé missing a phone number beats generation 500ing.
        assert escape_latex(None) == ""
        assert escape_latex(42) == ""


class TestUrlSanitising:
    @pytest.mark.parametrize("url", ["https://x.dev", "http://x.dev/p", "mailto:a@b.com"])
    def test_allows_web_and_mail_schemes(self, url: str):
        assert sanitize_url(url) == url

    @pytest.mark.parametrize("url", ["javascript:alert(1)", "file:///etc/passwd", "data:text/html,x", ""])
    def test_rejects_everything_else(self, url: str):
        assert sanitize_url(url) == ""

    @pytest.mark.parametrize("url", ["https://x.dev/}\\input{/etc/passwd}", "https://x.dev/%00", "https://x.dev/#{"])
    def test_rejects_urls_that_could_break_out_of_the_href_argument(self, url: str):
        # URLs aren't escaped (escaping corrupts them), so a `}` or `\` would end the \href
        # argument and inject LaTeX into a document the user is about to compile.
        assert sanitize_url(url) == ""


class TestRendering:
    def test_produces_a_complete_document(self):
        tex = to_latex(doc())
        assert tex.startswith("\\documentclass")
        assert "\\begin{document}" in tex and "\\end{document}" in tex

    def test_includes_the_selected_content(self):
        tex = to_latex(doc())
        assert "Alex Mercer" in tex
        assert "K8s autoscaling operator" in tex
        assert "Platform Engineer" in tex
        assert "TU Berlin" in tex
        assert "Kubernetes" in tex

    def test_escapes_content_in_context(self):
        tex = to_latex(doc())
        # "Acme & Co" is a company name; unescaped, the & is an alignment tab.
        assert r"Acme \& Co" in tex
        assert r"Reduced p99 latency 40\%" in tex

    def test_avoids_packages_missing_from_a_base_tex_install(self):
        # A missing package is a hard compile error, so a résumé the user cannot build is worse than
        # a plain one. fontawesome5/XCharter/glyphtounicode are not in a base install.
        tex = to_latex(doc())
        for package in ("fontawesome5", "XCharter", "glyphtounicode"):
            assert package not in tex

    def test_omits_empty_sections_rather_than_emitting_empty_itemize(self):
        # \begin{itemize}\end{itemize} with no \item is a LaTeX error.
        tex = to_latex(doc(projects=[], experience=[], skills=[], summary=""))
        assert "\\begin{itemize}\n\\end{itemize}" not in tex
        assert "Projects" not in tex
        assert "\\begin{document}" in tex

    def test_survives_a_profile_full_of_missing_fields(self):
        tex = to_latex(
            ResumeDoc(
                name="", contact={}, summary="", skills=[], projects=[], experience=[], education=[]
            )
        )
        assert "\\begin{document}" in tex and "\\end{document}" in tex

    def test_drops_malformed_entries_instead_of_rendering_junk(self):
        tex = to_latex(doc(experience=[{"period": "2020"}, "not-a-dict"], education=[{"year": "2021"}]))
        assert "not-a-dict" not in tex

    def test_links_are_labelled_in_text_for_ats_parsers(self):
        tex = to_latex(doc())
        # An icon glyph carries no extractable text; a label does.
        assert "GitHub" in tex
        assert "\\href{https://github.com/alex}" in tex

    def test_a_hostile_link_does_not_reach_href(self):
        tex = to_latex(doc(contact={"links": {"X": "javascript:alert(1)"}}))
        assert "javascript" not in tex


@pytest.mark.skipif(
    shutil.which("pdflatex") is None, reason="no LaTeX toolchain installed; escaping is covered by unit tests"
)
def test_output_actually_compiles():
    """Compile the generated source for real.

    The unit tests above assert the output *looks* escaped. This asserts it *builds*, which is the
    claim §5 actually makes — the user downloads this .tex and compiles it. Adversarial content is
    included so the escaping is exercised, not just the happy path.
    """
    hostile = doc(
        name="Alex & Mercer",
        summary="Cut cost 50% using C# and $AWS_REGION; math^2 ~ approx",
        skills=["C++", "C#", "Node.js", "50%_growth"],
    )
    tex = to_latex(hostile)
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "resume.tex"
        source.write_text(tex, encoding="utf-8")
        result = subprocess.run(
            ["pdflatex", "-interaction=nonstopmode", "-halt-on-error", source.name],
            cwd=tmp,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, f"pdflatex failed:\n{result.stdout[-3000:]}"
        assert (Path(tmp) / "resume.pdf").is_file()
