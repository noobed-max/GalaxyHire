"""Miscellaneous user-data singleton (Add experience → Misc box).

Update semantics, not accumulation: one record per user, each upload merged with the
previous text. Misc stays out of the profile graph/vectors/snapshot — it is untyped
prose for form-fill context, threaded into the extension fill path only.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest


APP_ROOT = Path(__file__).resolve().parents[1]


def run_misc_script(db_path: str, script: str) -> str:
    """Run sqlite assertions in a fresh interpreter.

    The subprocess preserves the repository's real-SQLite isolation convention and
    prevents connection-pool state from leaking between test modules.
    """
    prog = (
        "import sys; sys.path.insert(0, '.');"
        "from data.sqlite.connection import init_sql;"
        f"db_path = {db_path!r};"
        "init_sql(db_path);"
        "from data.sqlite import misc as m;"
        + script
    )
    result = subprocess.run(
        [sys.executable, "-c", prog],
        cwd=str(APP_ROOT),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


class TestMiscStore:
    def test_starts_empty(self, tmp_path):
        db = str(tmp_path / "misc.db")
        out = run_misc_script(db, "print(repr(m.get_misc(db_path)['text'])); print(repr(bool(m.get_misc(db_path)['updated_at'])))")
        assert out.splitlines() == ["''", "True"]

    def test_save_replaces_never_appends(self, tmp_path):
        db = str(tmp_path / "misc.db")
        out = run_misc_script(
            db,
            "m.save_merged('visa: UK citizen', db_path);"
            "print(repr(m.get_misc(db_path)['text']));"
            "m.save_merged('visa: UK citizen. Notice: 1 month.', db_path);"
            "r = m.get_misc(db_path); print(repr(r['text'])); print(repr(bool(r['updated_at'])))",
        )
        assert out.splitlines() == ["'visa: UK citizen'", "'visa: UK citizen. Notice: 1 month.'", "True"]

    def test_clear_empties_but_keeps_the_singleton(self, tmp_path):
        db = str(tmp_path / "misc.db")
        out = run_misc_script(
            db,
            "m.save_merged('x', db_path); m.clear_misc(db_path); print(repr(m.get_misc(db_path)['text']))",
        )
        assert out == "''"


class TestMiscMerger:
    def test_empty_incoming_is_rejected_before_any_llm_call(self):
        from profile.misc_merger import merge_misc

        with pytest.raises(ValueError, match="nothing to merge"):
            merge_misc("previous", "   ")

    def test_merge_calls_the_model_with_both_sides(self, monkeypatch):
        import profile.misc_merger as merger

        calls: dict = {}

        def fake_call_llm(s, u, m, step=None):
            calls["system"] = s
            calls["user"] = u
            calls["step"] = step
            return m(merged_text="MERGED")

        # merge_misc does `from llm import call_llm` at call time, so patching the
        # package attribute is what takes effect.
        import llm

        monkeypatch.setattr(llm, "resolve_config", lambda step=None: ("openai", "k", "m"))
        monkeypatch.setattr(llm, "provider_needs_key", lambda p: True)
        monkeypatch.setattr(llm, "call_llm", fake_call_llm)

        assert merger.merge_misc("old fact", "new fact") == "MERGED"
        assert "old fact" in calls["user"] and "new fact" in calls["user"]
        assert calls["step"] == "ingestor"

    def test_missing_key_keeps_previous_by_raising(self, monkeypatch):
        import profile.misc_merger as merger
        import llm

        monkeypatch.setattr(llm, "resolve_config", lambda step=None: ("openai", "", "m"))
        monkeypatch.setattr(llm, "provider_needs_key", lambda p: True)

        with pytest.raises(RuntimeError, match="no API key"):
            merger.merge_misc("previous", "incoming")


class TestFillProseIncludesMisc:
    def test_misc_block_appended_only_when_present(self):
        from api.routers.apply import _fill_profile_prose

        assert "MISCELLANEOUS" not in _fill_profile_prose({})
        out = _fill_profile_prose({}, "UK citizen, no visa required")
        assert "MISCELLANEOUS USER DATA" in out
        assert "UK citizen" in out
