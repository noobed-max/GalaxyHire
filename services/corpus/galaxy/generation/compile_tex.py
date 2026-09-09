"""Compiling user-edited LaTeX (§5).

§5 asks for an in-UI editor so the user can "fix errors or make edits before using it" — which only
means anything if they can *see* the errors. So this compiles their edited source and returns either
a PDF or the log excerpt that explains the failure.

Two things shape the implementation:

  * **The input is LaTeX, and LaTeX is a programming language.** `\\write18` can execute shell
    commands and `\\input{/etc/passwd}` reads arbitrary files. The user is compiling their own résumé
    on their own machine, so this is not a privilege boundary in the usual sense — but it is still
    worth closing, because the source did not start out as theirs: it is generated from job-posting
    text, and an editor is an obvious place for a prompt-injected instruction to land. Hence
    `-no-shell-escape` and compiling inside a throwaway directory.
  * **A missing toolchain is a normal state, not an error.** Most machines have no TeX install, so
    absence is reported as an actionable message rather than an exception, and the UI keeps offering
    the `.tex` download, which works regardless.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

# A résumé is one or two pages; anything slower than this is a runaway macro, not slow hardware.
COMPILE_TIMEOUT_S = 60
# LaTeX resolves references in a second pass, which the \hfill-based layout needs.
PASSES = 2
MAX_SOURCE_BYTES = 512 * 1024

# Engines in preference order. lualatex and xelatex handle UTF-8 natively; pdflatex is the most
# commonly installed.
ENGINES = ("pdflatex", "lualatex", "xelatex")


@dataclass
class CompileResult:
    ok: bool
    pdf: bytes | None = None
    log: str | None = None
    error: str | None = None
    engine: str | None = None


def available_engine() -> str | None:
    """The first LaTeX engine on PATH, or None."""
    for engine in ENGINES:
        if shutil.which(engine):
            return engine
    return None


def _extract_errors(log: str) -> str:
    """Pull the useful lines out of a LaTeX log.

    A failing run emits hundreds of lines of package chatter around a handful of real errors. Showing
    the raw log in a UI is the same as showing nothing, so this keeps the `!`-prefixed error lines
    and their line-number markers — which is what a user needs to find the problem in their source.
    """
    interesting: list[str] = []
    for line in log.splitlines():
        if line.startswith("!") or line.startswith("l.") or "Undefined control sequence" in line:
            interesting.append(line.rstrip())
        elif re.match(r"^.*?:\d+:", line):
            interesting.append(line.rstrip())
    if not interesting:
        # No recognisable error markers: fall back to the tail, where TeX usually stops.
        return "\n".join(log.splitlines()[-40:])
    return "\n".join(interesting[:60])


def compile_latex(source: str) -> CompileResult:
    """Compile LaTeX source to PDF bytes.

    Never raises. Every failure mode — no toolchain, oversized input, a syntax error, a runaway
    macro — comes back as a `CompileResult` the UI can render, because each one needs a different
    message to the user and none of them is exceptional.
    """
    if not source or not source.strip():
        return CompileResult(ok=False, error="Nothing to compile.")

    encoded = source.encode("utf-8")
    if len(encoded) > MAX_SOURCE_BYTES:
        return CompileResult(
            ok=False,
            error=f"Source is {len(encoded) // 1024} KB; the limit is {MAX_SOURCE_BYTES // 1024} KB.",
        )

    engine = available_engine()
    if engine is None:
        return CompileResult(
            ok=False,
            error=(
                "No LaTeX engine found. Install TeX Live (`apt install texlive-latex-recommended`) "
                "or MiKTeX to preview here — or download the .tex and compile it wherever you like."
            ),
        )

    with tempfile.TemporaryDirectory(prefix="galaxyhire-tex-") as tmp:
        workdir = Path(tmp)
        tex = workdir / "resume.tex"
        tex.write_bytes(encoded)

        last_log = ""
        for _ in range(PASSES):
            try:
                proc = subprocess.run(
                    [
                        engine,
                        "-interaction=nonstopmode",
                        "-halt-on-error",
                        # Refuse \write18. The source is generated from untrusted job text and then
                        # hand-edited, so shell escape has no business being reachable from it.
                        "-no-shell-escape",
                        tex.name,
                    ],
                    cwd=workdir,
                    capture_output=True,
                    text=True,
                    timeout=COMPILE_TIMEOUT_S,
                )
            except subprocess.TimeoutExpired:
                return CompileResult(
                    ok=False,
                    engine=engine,
                    error=f"Compilation timed out after {COMPILE_TIMEOUT_S}s — check for a runaway macro.",
                )
            last_log = proc.stdout or ""
            if proc.returncode != 0:
                return CompileResult(
                    ok=False,
                    engine=engine,
                    log=_extract_errors(last_log),
                    error="LaTeX reported an error.",
                )

        pdf = workdir / "resume.pdf"
        if not pdf.is_file():
            return CompileResult(
                ok=False,
                engine=engine,
                log=_extract_errors(last_log),
                error="Compilation finished but produced no PDF.",
            )
        return CompileResult(ok=True, pdf=pdf.read_bytes(), engine=engine)
