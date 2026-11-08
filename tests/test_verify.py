"""Verify-run tests (M4): execute-in-tempdir smoke test + graceful skips.

Everything here executes ONLY inside the runner's own temp dir. Scripts are
tiny Python (always present) unless a test specifically exercises another
interpreter's absence. No test writes or runs anything against a real skill dir.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from skill_to_code import convert, verify
from skill_to_code.parser import parse_skill
from skill_to_code.types import DiscoveredSkill


def _write(dir_: Path, name: str, text: str) -> Path:
    p = dir_ / name
    p.write_text(text, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# verify_script: run outcomes
# ---------------------------------------------------------------------------


def test_passing_script_is_ok(tmp_path):
    script = _write(tmp_path, "ok.py", "print('hello')\n")
    vr = verify.verify_script(str(script))
    assert vr.ran is True
    assert vr.ok is True
    assert vr.returncode == 0
    assert vr.timed_out is False
    assert vr.interpreter == "python"
    assert "hello" in vr.stdout
    assert vr.duration_s >= 0.0


def test_failing_script_is_not_ok(tmp_path):
    script = _write(tmp_path, "fail.py", "import sys\nsys.exit(1)\n")
    vr = verify.verify_script(str(script))
    assert vr.ran is True
    assert vr.ok is False
    assert vr.returncode == 1
    assert vr.timed_out is False


def test_nonzero_stderr_captured(tmp_path):
    script = _write(tmp_path, "boom.py", "import sys\nsys.stderr.write('nope\\n')\nsys.exit(3)\n")
    vr = verify.verify_script(str(script))
    assert vr.ran is True
    assert vr.ok is False
    assert vr.returncode == 3
    assert "nope" in vr.stderr


def test_infinite_script_times_out(tmp_path):
    script = _write(tmp_path, "spin.py", "import time\ntime.sleep(30)\n")
    vr = verify.verify_script(str(script), timeout_s=1.0)
    assert vr.ran is True
    assert vr.timed_out is True
    assert vr.ok is False
    assert vr.returncode is None
    assert vr.duration_s < 20.0


def test_timeout_alias_matches_timeout_s(tmp_path):
    script = _write(tmp_path, "spin2.py", "import time\ntime.sleep(30)\n")
    vr = verify.verify_script(str(script), timeout=1.0)
    assert vr.timed_out is True
    assert vr.ok is False


# ---------------------------------------------------------------------------
# Isolation: runs in a fresh temp cwd that is removed afterward
# ---------------------------------------------------------------------------


def test_runs_in_tempdir_not_script_dir(tmp_path):
    # The script records its own cwd; assert it is NOT the script's home dir.
    marker = tmp_path / "cwd.txt"
    script = _write(
        tmp_path,
        "where.py",
        "import os\n"
        f"open(r'{marker}', 'w').write(os.getcwd())\n",
    )
    vr = verify.verify_script(str(script))
    assert vr.ok is True
    recorded = marker.read_text(encoding="utf-8")
    assert Path(recorded).resolve() != tmp_path.resolve()


def test_tempdir_removed_after_success(tmp_path, monkeypatch):
    created: list[str] = []
    real = tempfile.mkdtemp

    def spy(*a, **k):
        d = real(*a, **k)
        created.append(d)
        return d

    monkeypatch.setattr(verify.tempfile, "mkdtemp", spy)
    script = _write(tmp_path, "ok.py", "print('x')\n")
    vr = verify.verify_script(str(script))
    assert vr.ok is True
    assert created
    for d in created:
        assert not os.path.exists(d)


def test_tempdir_removed_after_failure(tmp_path, monkeypatch):
    created: list[str] = []
    real = tempfile.mkdtemp

    def spy(*a, **k):
        d = real(*a, **k)
        created.append(d)
        return d

    monkeypatch.setattr(verify.tempfile, "mkdtemp", spy)
    script = _write(tmp_path, "fail.py", "import sys\nsys.exit(2)\n")
    vr = verify.verify_script(str(script))
    assert vr.ok is False
    assert created
    for d in created:
        assert not os.path.exists(d)


def test_fixture_is_seeded_into_cwd(tmp_path):
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    _write(fixture, "seed.txt", "data")
    # Script passes only if the seeded file is present in its cwd.
    script = _write(
        tmp_path,
        "needseed.py",
        "import os, sys\nsys.exit(0 if os.path.exists('seed.txt') else 1)\n",
    )
    without = verify.verify_script(str(script))
    assert without.ok is False  # not seeded -> file missing -> exit 1
    withf = verify.verify_script(str(script), fixture=str(fixture))
    assert withf.ok is True


# ---------------------------------------------------------------------------
# Graceful skips: missing interpreter / unknown extension
# ---------------------------------------------------------------------------


def test_unknown_extension_skips(tmp_path):
    script = _write(tmp_path, "mystery.weird", "whatever\n")
    vr = verify.verify_script(str(script))
    assert vr.ran is False
    assert vr.ok is False
    assert "no interpreter for extension" in vr.skipped_reason
    assert ".weird" in vr.skipped_reason
    assert vr.returncode is None


def test_missing_bash_skips_without_running(tmp_path, monkeypatch):
    # Simulate a box without bash: which returns None -> skip, no temp dir.
    created: list[str] = []
    real = tempfile.mkdtemp

    def spy(*a, **k):
        d = real(*a, **k)
        created.append(d)
        return d

    monkeypatch.setattr(verify.tempfile, "mkdtemp", spy)
    monkeypatch.setattr(verify.shutil, "which", lambda name: None)
    script = _write(tmp_path, "thing.sh", "echo hi\n")
    vr = verify.verify_script(str(script))
    assert vr.ran is False
    assert vr.ok is False
    assert vr.skipped_reason == "interpreter not found: bash"
    assert vr.interpreter == "bash"
    assert created == []  # never created a temp dir for a skip


def test_missing_powershell_skips(tmp_path, monkeypatch):
    monkeypatch.setattr(verify.shutil, "which", lambda name: None)
    script = _write(tmp_path, "thing.ps1", "Write-Output hi\n")
    vr = verify.verify_script(str(script))
    assert vr.ran is False
    assert vr.skipped_reason == "interpreter not found: powershell"
    assert vr.interpreter == "powershell"


# ---------------------------------------------------------------------------
# verify_conversion over a real staged ConversionResult
# ---------------------------------------------------------------------------


def _discovered(skill_dir: Path) -> DiscoveredSkill:
    skill_md = skill_dir / "SKILL.md"
    return DiscoveredSkill(
        path=str(skill_md),
        dir=str(skill_dir),
        name=skill_dir.name,
        source="include",
        plugin=None,
        mtime=skill_md.stat().st_mtime,
    )


class _Analysis:
    def __init__(self, bucket: str) -> None:
        self.bucket = bucket


def _stage_python_skill(make_skill, body: str):
    skill_dir = make_skill("pyskill", body=body)
    disc = _discovered(skill_dir)
    parsed = parse_skill(disc)
    plan = convert.plan_conversion(disc, parsed, _Analysis("replace"))
    scripts = convert.render_scripts(plan)
    rewritten = convert.rewrite_skill_md(plan, parsed, (skill_dir / "SKILL.md").read_text(encoding="utf-8"))
    return convert.stage(plan, scripts, rewritten, str(skill_dir / "SKILL.md"))


def test_verify_conversion_passes_for_clean_python(make_skill):
    body = "# Do\n\n```python\nprint('converted ok')\n```\n"
    result = _stage_python_skill(make_skill, body)
    assert result.script_paths  # a .py was staged
    vr = verify.verify_conversion(result)
    assert vr.ran is True
    assert vr.ok is True
    assert vr.interpreter == "python"


def test_verify_conversion_fails_for_erroring_python(make_skill):
    body = "# Do\n\n```python\nimport sys\nsys.exit(4)\n```\n"
    result = _stage_python_skill(make_skill, body)
    vr = verify.verify_conversion(result)
    assert vr.ran is True
    assert vr.ok is False
    assert vr.returncode == 4


def test_verify_conversion_skips_when_no_script(make_skill):
    # An ineligible plan (no executable code) stages nothing to verify.
    skill_dir = make_skill("prose", body="# Just prose\n\nNo code here.\n")
    disc = _discovered(skill_dir)
    parsed = parse_skill(disc)
    plan = convert.plan_conversion(disc, parsed, _Analysis("replace"))
    result = convert.stage(plan, {}, "", str(skill_dir / "SKILL.md"))
    vr = verify.verify_conversion(result)
    assert vr.ran is False
    assert vr.skipped_reason == "no staged script to verify"
