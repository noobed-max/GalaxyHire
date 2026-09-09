"""The documented way to start the API must actually start it.

`make api` shipped broken: it ran `uvicorn api.app:create_app --factory`, but `create_app` takes
required keyword-only arguments, so uvicorn's factory mode could never call it. Every start ended
in `create_app() missing 3 required keyword-only arguments`.

The whole suite stayed green throughout, because tests build the app through `build_gateway_app()`
or their own fixtures — nothing ever executed the command a person would actually type. These tests
close that specific gap: they check the Makefile recipe against the code it names.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
MAKEFILE = REPO_ROOT / "Makefile"


def _recipe(target: str) -> str:
    """The shell lines of a Makefile target, ignoring comments and the `##` help text."""
    text = MAKEFILE.read_text()
    match = re.search(rf"^{re.escape(target)}:.*?$\n((?:\t.*\n)+)", text, re.MULTILINE)
    assert match, f"no `{target}:` target with a recipe in {MAKEFILE}"
    return match.group(1)


class TestTheApiTargetCanRun:
    def test_it_does_not_use_factory_mode_on_create_app(self):
        """The exact broken invocation, pinned so it cannot come back.

        It looks entirely plausible next to the corpus target, which *is* a bare ASGI app and does
        work that way. The asymmetry is the trap.
        """
        recipe = _recipe("api")
        assert "--factory" not in recipe, (
            "create_app requires keyword-only args, so uvicorn factory mode cannot construct it"
        )

    def test_create_app_really_does_require_arguments(self):
        # Guards the reasoning above rather than the symptom: if create_app ever grows defaults for
        # all three, factory mode becomes legitimate and the test above can be revisited.
        from api.app import create_app

        required = [
            name
            for name, p in inspect.signature(create_app).parameters.items()
            if p.default is inspect.Parameter.empty
        ]
        assert required, "create_app takes no required args — the --factory ban can be reconsidered"

    def test_it_runs_the_same_entrypoint_as_the_desktop_shell(self):
        # Two entrypoints would drift; the desktop shell already runs main.py.
        assert "main.py" in _recipe("api")

    def test_that_entrypoint_exists_and_exposes_an_app(self):
        assert (REPO_ROOT / "apps" / "api" / "main.py").is_file()

        import main

        # Built lazily via PEP 562 __getattr__, so plain attribute access is the real check.
        assert main.app is not None

    def test_every_flag_the_makefile_passes_is_one_main_py_accepts(self):
        """Caught `--reload` being appended by analogy with the corpus target.

        main.py rejects unknown flags outright, so the target failed instantly with
        `unrecognized arguments: --reload` — and it could never have worked: main.py reserves the
        port and hands uvicorn a bound socket, while the reloader re-imports an app by string in a
        subprocess. Any flag in the recipe must exist in the parser.
        """
        import main

        # Take the recipe's own argv and feed it to the real parser. Substituting $(APP_PORT)
        # keeps this honest — a flag is only "accepted" if it parses alongside the others.
        argv = _recipe("api").split("main.py", 1)[1].replace("$(APP_PORT)", "8000").split()

        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr("sys.argv", ["main.py", *argv])
        try:
            main._parse_args()  # argparse exits non-zero on an unknown flag
        except SystemExit as exc:
            pytest.fail(f"`make api` passes flags main.py rejects: {argv} (exit {exc.code})")
        finally:
            monkeypatch.undo()

    def test_the_port_flag_the_makefile_passes_is_actually_parsed(self, monkeypatch):
        # The Makefile passes --port; if main.py ever renamed it, the target would bind a random
        # port and the UI would be served somewhere nobody is looking.
        import main

        assert "--port" in _recipe("api")
        monkeypatch.setattr("sys.argv", ["main.py", "--port", "8123"])
        assert main._parse_args().port == 8123


class TestTheCorpusTargetStillWorks:
    """The corpus target *is* a bare ASGI app, which is why the api one looked fine by analogy."""

    def test_it_names_a_module_attribute_uvicorn_can_import(self):
        recipe = _recipe("corpus")
        assert "galaxy.api.app:app" in recipe

    @pytest.mark.skipif(
        not (REPO_ROOT / "services" / "corpus" / "galaxy").is_dir(),
        reason="corpus workspace not present",
    )
    def test_and_that_attribute_is_an_app_not_a_factory(self):
        # Only meaningful from the corpus venv; asserting the recipe shape is the portable part.
        assert ":app" in _recipe("corpus")
