"""Compiling user-edited LaTeX (§5).

The in-UI editor is only useful if a failed compile explains itself, so these tests cover the error
paths as carefully as the success path — and confirm shell escape stays off, because this source is
generated from job-posting text before the user ever edits it.
"""

from __future__ import annotations

import shutil

import pytest

from galaxy.generation.compile_tex import (
    MAX_SOURCE_BYTES,
    available_engine,
    compile_latex,
)

MINIMAL = r"""\documentclass{article}
\begin{document}
Hello.
\end{document}
"""

has_latex = available_engine() is not None
needs_latex = pytest.mark.skipif(not has_latex, reason="no LaTeX engine installed")


class TestInputHandling:
    """These run without a toolchain, because they reject before invoking one."""

    @pytest.mark.parametrize("source", ["", "   ", "\n"])
    def test_empty_source_is_refused(self, source: str):
        result = compile_latex(source)
        assert result.ok is False
        assert "Nothing to compile" in result.error

    def test_oversized_source_is_refused_with_the_actual_limit(self):
        result = compile_latex("x" * (MAX_SOURCE_BYTES + 1))
        assert result.ok is False
        # The message should tell the user what the limit is, not just that they exceeded one.
        assert "limit" in result.error
        assert str(MAX_SOURCE_BYTES // 1024) in result.error

    def test_never_raises(self):
        # Every failure is a normal outcome of editing, so callers get a result, not an exception.
        for source in ["", "\\undefined", "x" * (MAX_SOURCE_BYTES + 1), "\\documentclass{nope}"]:
            assert compile_latex(source).ok in (True, False)


@pytest.mark.skipif(has_latex, reason="a LaTeX engine is installed here")
class TestWithoutAToolchain:
    def test_absence_is_reported_as_actionable_guidance(self):
        # Most machines have no TeX install; that is a normal state, and the .tex download still
        # works, so the message points at both options rather than looking like a crash.
        result = compile_latex(MINIMAL)
        assert result.ok is False
        assert "No LaTeX engine" in result.error
        assert ".tex" in result.error


@needs_latex
class TestCompilation:
    def test_compiles_minimal_source_to_a_pdf(self):
        result = compile_latex(MINIMAL)
        assert result.ok is True, result.error
        assert result.pdf and result.pdf.startswith(b"%PDF")
        assert result.engine

    def test_compiles_the_generated_resume(self):
        from galaxy.generation.latex import to_latex
        from galaxy.generation.models import ResumeDoc

        doc = ResumeDoc(
            name="Alex & Mercer",
            contact={"email": "a@b.com", "links": {"GitHub": "https://github.com/a"}},
            summary="Cut cost 50% with C# and $BUDGET",
            skills=["C++", "Go"],
            projects=[],
            experience=[
                {"role": "SRE", "company": "Acme_Co", "period": "2020--2024", "bullets": ["100% uptime"]}
            ],
            education=[{"school": "TU", "degree": "BSc", "year": "2020"}],
        )
        result = compile_latex(to_latex(doc))
        assert result.ok is True, f"{result.error}\n{result.log}"

    def test_a_syntax_error_returns_the_useful_log_lines(self):
        broken = r"""\documentclass{article}
\begin{document}
\undefinedmacro
\end{document}
"""
        result = compile_latex(broken)
        assert result.ok is False
        # Raw logs are hundreds of lines of package chatter; the extract is what makes this usable.
        assert result.log
        assert "Undefined control sequence" in result.log
        assert len(result.log.splitlines()) < 70

    def test_unbalanced_environment_is_reported(self):
        source = "\\documentclass{article}\n\\begin{document}\n\\begin{itemize}\n\\end{document}\n"
        result = compile_latex(source)
        assert result.ok is False
        assert result.log

    def test_shell_escape_is_refused(self):
        r"""`\write18` must not execute, even though the user "owns" this source.

        The document starts as generated output over job-posting text, so an injected
        `\immediate\write18{...}` is a realistic path from an untrusted posting to a shell command.
        With -no-shell-escape the engine refuses it.
        """
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "pwned.txt"
            source = (
                "\\documentclass{article}\n"
                "\\begin{document}\n"
                f"\\immediate\\write18{{touch {marker}}}\n"
                "ok\n"
                "\\end{document}\n"
            )
            compile_latex(source)
            assert not marker.exists(), "shell escape executed — -no-shell-escape is not in effect"


def test_engine_detection_matches_the_environment():
    engine = available_engine()
    if engine is None:
        assert all(shutil.which(e) is None for e in ("pdflatex", "lualatex", "xelatex"))
    else:
        assert shutil.which(engine)
