"""CLI tests for ``convert --verify-run`` wiring and scoped ``--root`` discovery.

Two slices, both this agent's:

* **verify wiring** -- the CLI stages -> lints -> (opt-in) executes each staged
  script and folds a run-time failure into the SAME gate lint uses, so ``apply``
  refuses it. The *execution* itself lives in ``skill_to_code.verify`` (a sibling
  module, tested in ``test_verify.py``); here we inject a controllable fake
  ``verify_script`` so the gate behavior is deterministic and independent of the
  host's interpreters. A pair of real end-to-end checks (guarded by
  ``importorskip``) exercise the real ``verify`` module when it is present.

* **scoping** -- ``scan --root PATH`` REPLACES the default discovery roots so only
  the given root is scanned (no real-home skill leaks in); ``--include`` stays
  additive; ``--root`` is repeatable.

Nothing here executes anything outside a tmp dir: the fake never runs a process,
and the real e2e checks run tiny Python via the verify module's own tempdir.
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

from skill_to_code import cli
from skill_to_code.types import VerifyResult


# ── fixtures on disk ─────────────────────────────────────────────────────────

_PY_SKILL = """---
name: pyskill
description: mechanical python.
---

# pyskill

Run:

```python
print("converted ok")
```
"""

_WRAP_SKILL = """---
name: multi
description: two mechanical languages.
---

# multi

First:

```bash
echo hi
```

Then:

```python
print("ok")
```
"""


def _write_skill(skill_dir: Path, content: str) -> Path:
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")
    return skill_dir


def _py_skill_root(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "roots"
    skill_dir = _write_skill(root / "pyskill", _PY_SKILL)
    return root, skill_dir


# ── fake verify module (deterministic gate control) ──────────────────────────


def _make_fake_verify(outcome):
    """A stand-in ``skill_to_code.verify`` whose ``verify_script`` is scripted.

    ``outcome`` is either a fixed ``VerifyResult`` or ``callable(path)->VerifyResult``.
    Every call is recorded on ``mod.calls`` so tests can assert flag forwarding.
    """
    mod = types.ModuleType("skill_to_code.verify")
    mod.calls = []

    def verify_script(script_path, *, fixture=None, timeout_s=20.0):
        mod.calls.append(
            {"path": script_path, "fixture": fixture, "timeout_s": timeout_s}
        )
        return outcome(script_path) if callable(outcome) else outcome

    mod.verify_script = verify_script
    return mod


def _inject_verify(monkeypatch, fake) -> None:
    import skill_to_code

    monkeypatch.setitem(sys.modules, "skill_to_code.verify", fake)
    monkeypatch.setattr(skill_to_code, "verify", fake, raising=False)


def _table_line(out: str, skill_name: str) -> str:
    return next(l for l in out.splitlines() if l.startswith(skill_name))


def _names(out: str) -> set[str]:
    return {s["name"] for s in json.loads(out)["skills"]}


_OK = VerifyResult(ran=True, ok=True, returncode=0, duration_s=0.01, interpreter="python")
_FAIL = VerifyResult(
    ran=True, ok=False, returncode=1, stderr="boom\n", duration_s=0.02, interpreter="python"
)
_TIMEOUT = VerifyResult(
    ran=True, ok=False, timed_out=True, returncode=None, duration_s=1.0, interpreter="python"
)
_SKIP = VerifyResult(
    ran=False, ok=False, skipped_reason="interpreter not found: bash", interpreter="bash"
)


# ── verify gate: pass / fail / timeout / skip ────────────────────────────────


def test_verify_pass_allows_apply(tmp_path, monkeypatch, capsys):
    root, skill_dir = _py_skill_root(tmp_path)
    original = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    fake = _make_fake_verify(_OK)
    _inject_verify(monkeypatch, fake)

    rc = cli.main(
        ["convert", "--root", str(root), "--name", "pyskill",
         "--bucket", "replace", "--verify-run", "--apply"]
    )
    assert rc == 0
    out = capsys.readouterr().out

    assert fake.calls, "verify_script should have been called"
    assert "converted" in _table_line(out, "pyskill")
    # applied: real SKILL.md rewritten to the stub, script placed as a sibling
    assert (skill_dir / "SKILL.md").read_text(encoding="utf-8") != original
    assert (skill_dir / "pyskill.py").is_file()
    # VERIFY section reports the ok run
    assert "VERIFY" in out
    assert "ok (rc=0" in out


def test_verify_failure_blocks_apply_and_leaves_original(tmp_path, monkeypatch, capsys):
    root, skill_dir = _py_skill_root(tmp_path)
    original = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    fake = _make_fake_verify(_FAIL)
    _inject_verify(monkeypatch, fake)

    rc = cli.main(
        ["convert", "--root", str(root), "--name", "pyskill",
         "--bucket", "replace", "--verify-run", "--apply"]
    )
    assert rc == 0
    out = capsys.readouterr().out

    assert "unsafe" in _table_line(out, "pyskill")
    # --apply refused: real SKILL.md untouched, no sibling script written
    assert (skill_dir / "SKILL.md").read_text(encoding="utf-8") == original
    assert not (skill_dir / "pyskill.py").exists()
    assert "FAIL (rc=1" in out


def test_verify_timeout_blocks_apply(tmp_path, monkeypatch, capsys):
    root, skill_dir = _py_skill_root(tmp_path)
    original = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    fake = _make_fake_verify(_TIMEOUT)
    _inject_verify(monkeypatch, fake)

    rc = cli.main(
        ["convert", "--root", str(root), "--name", "pyskill",
         "--bucket", "replace", "--verify-run", "--apply"]
    )
    assert rc == 0
    out = capsys.readouterr().out

    assert "unsafe" in _table_line(out, "pyskill")
    assert (skill_dir / "SKILL.md").read_text(encoding="utf-8") == original
    assert not (skill_dir / "pyskill.py").exists()
    assert "timed out" in out


def test_verify_skip_does_not_block_apply(tmp_path, monkeypatch, capsys):
    # A skip (interpreter absent) mirrors lint's tool-absent path: warn, apply.
    root, skill_dir = _py_skill_root(tmp_path)
    fake = _make_fake_verify(_SKIP)
    _inject_verify(monkeypatch, fake)

    rc = cli.main(
        ["convert", "--root", str(root), "--name", "pyskill",
         "--bucket", "replace", "--verify-run", "--apply"]
    )
    assert rc == 0
    out = capsys.readouterr().out

    assert "converted" in _table_line(out, "pyskill")
    assert (skill_dir / "pyskill.py").is_file()
    assert "skipped (interpreter not found: bash)" in out


def test_verify_wrap_any_script_failure_blocks(tmp_path, monkeypatch, capsys):
    # A WRAP skill stages one helper per language; the gate blocks if ANY fails.
    root = tmp_path / "roots"
    skill_dir = _write_skill(root / "multi", _WRAP_SKILL)
    original = (skill_dir / "SKILL.md").read_text(encoding="utf-8")

    def outcome(path: str):
        # The .py passes, the .sh fails -> the second script trips the gate.
        if path.endswith(".sh"):
            return VerifyResult(ran=True, ok=False, returncode=2, interpreter="bash")
        return VerifyResult(ran=True, ok=True, returncode=0, interpreter="python")

    fake = _make_fake_verify(outcome)
    _inject_verify(monkeypatch, fake)

    rc = cli.main(
        ["convert", "--root", str(root), "--name", "multi",
         "--bucket", "wrap", "--verify-run", "--apply"]
    )
    assert rc == 0
    out = capsys.readouterr().out

    assert len(fake.calls) == 2  # both helpers were verified
    assert "unsafe" in _table_line(out, "multi")
    assert (skill_dir / "SKILL.md").read_text(encoding="utf-8") == original
    assert not (skill_dir / "multi.sh").exists()
    assert not (skill_dir / "multi.py").exists()


# ── flag forwarding + opt-in gating ──────────────────────────────────────────


def test_verify_timeout_flag_is_forwarded(tmp_path, monkeypatch, capsys):
    root, _ = _py_skill_root(tmp_path)
    fake = _make_fake_verify(_OK)
    _inject_verify(monkeypatch, fake)

    cli.main(
        ["convert", "--root", str(root), "--name", "pyskill",
         "--bucket", "replace", "--verify-run", "--verify-timeout", "5"]
    )
    assert fake.calls[0]["timeout_s"] == 5.0


def test_verify_timeout_defaults_to_twenty(tmp_path, monkeypatch, capsys):
    root, _ = _py_skill_root(tmp_path)
    fake = _make_fake_verify(_OK)
    _inject_verify(monkeypatch, fake)

    cli.main(
        ["convert", "--root", str(root), "--name", "pyskill",
         "--bucket", "replace", "--verify-run"]
    )
    assert fake.calls[0]["timeout_s"] == 20.0


def test_fixture_flag_is_forwarded(tmp_path, monkeypatch, capsys):
    root, _ = _py_skill_root(tmp_path)
    fixture_dir = tmp_path / "fx"
    fixture_dir.mkdir()
    fake = _make_fake_verify(_OK)
    _inject_verify(monkeypatch, fake)

    cli.main(
        ["convert", "--root", str(root), "--name", "pyskill",
         "--bucket", "replace", "--verify-run", "--fixture", str(fixture_dir)]
    )
    assert fake.calls[0]["fixture"] == str(fixture_dir)


def test_without_verify_run_nothing_executes(tmp_path, monkeypatch, capsys):
    # No --verify-run: verify is never imported or called; lint-only gate applies.
    root, skill_dir = _py_skill_root(tmp_path)
    fake = _make_fake_verify(_FAIL)  # would block IF it ran
    _inject_verify(monkeypatch, fake)

    rc = cli.main(
        ["convert", "--root", str(root), "--name", "pyskill",
         "--bucket", "replace", "--apply"]
    )
    assert rc == 0
    out = capsys.readouterr().out

    assert fake.calls == []  # verify never invoked
    assert "converted" in _table_line(out, "pyskill")
    assert (skill_dir / "pyskill.py").is_file()  # applied on the lint-only gate
    assert "VERIFY" not in out


def test_verify_run_prints_execution_warning(tmp_path, monkeypatch, capsys):
    root, _ = _py_skill_root(tmp_path)
    fake = _make_fake_verify(_OK)
    _inject_verify(monkeypatch, fake)

    cli.main(
        ["convert", "--root", str(root), "--name", "pyskill",
         "--bucket", "replace", "--verify-run"]
    )
    err = capsys.readouterr().err
    assert "WARNING" in err
    assert "EXECUTES" in err


# ── scoped --root discovery ──────────────────────────────────────────────────


def test_scan_root_scopes_to_only_that_root(tmp_path, capsys):
    root = tmp_path / "roots"
    _write_skill(root / "scoped-only-xyz", "# s\n\n```bash\necho hi\n```\n")

    rc = cli.main(["scan", "--root", str(root), "--format", "json"])
    assert rc == 0
    names = _names(capsys.readouterr().out)
    # ONLY the scoped skill -- no user/plugin/project skill from the real machine.
    assert names == {"scoped-only-xyz"}


def test_scan_include_stays_additive_under_root(tmp_path, capsys):
    root = tmp_path / "roots"
    _write_skill(root / "root-skill", "# r\n\n```bash\necho r\n```\n")
    extra = tmp_path / "extra"
    _write_skill(extra / "extra-skill", "# e\n\n```bash\necho e\n```\n")

    rc = cli.main(
        ["scan", "--root", str(root), "--include", str(extra), "--format", "json"]
    )
    assert rc == 0
    names = _names(capsys.readouterr().out)
    assert names == {"root-skill", "extra-skill"}


def test_scan_root_is_repeatable(tmp_path, capsys):
    root_a = tmp_path / "A"
    _write_skill(root_a / "alpha", "# a\n\n```bash\necho a\n```\n")
    root_b = tmp_path / "B"
    _write_skill(root_b / "beta", "# b\n\n```bash\necho b\n```\n")

    rc = cli.main(
        ["scan", "--root", str(root_a), "--root", str(root_b), "--format", "json"]
    )
    assert rc == 0
    names = _names(capsys.readouterr().out)
    assert names == {"alpha", "beta"}


def test_default_scan_sees_home_but_scoped_scan_excludes_it(tmp_path, capsys):
    # The default scan walks the real machine; the scoped scan must not.
    root = tmp_path / "roots"
    _write_skill(root / "scoped-only-xyz", "# s\n\n```bash\necho hi\n```\n")

    cli.main(["scan", "--format", "json"])
    default_names = _names(capsys.readouterr().out)

    cli.main(["scan", "--root", str(root), "--format", "json"])
    scoped_names = _names(capsys.readouterr().out)

    assert scoped_names == {"scoped-only-xyz"}
    assert "scoped-only-xyz" not in default_names
    assert default_names, "the real machine is expected to have skills"
    # A real skill the default scan surfaces is absent from the scoped scan.
    assert next(iter(default_names)) not in scoped_names


# ── real end-to-end (uses the actual verify module when present) ─────────────


def test_real_verify_pass_applies(tmp_path, capsys):
    pytest.importorskip("skill_to_code.verify")
    root = tmp_path / "roots"
    skill_dir = _write_skill(
        root / "realok",
        "---\nname: realok\n---\n\n# realok\n\n```python\nprint('real ok')\n```\n",
    )

    rc = cli.main(
        ["convert", "--root", str(root), "--name", "realok",
         "--bucket", "replace", "--verify-run", "--apply"]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "converted" in _table_line(out, "realok")
    assert (skill_dir / "realok.py").is_file()


def test_real_verify_failure_blocks(tmp_path, capsys):
    pytest.importorskip("skill_to_code.verify")
    root = tmp_path / "roots"
    skill_dir = _write_skill(
        root / "realbad",
        "---\nname: realbad\n---\n\n# realbad\n\n```python\nimport sys\nsys.exit(4)\n```\n",
    )
    original = (skill_dir / "SKILL.md").read_text(encoding="utf-8")

    rc = cli.main(
        ["convert", "--root", str(root), "--name", "realbad",
         "--bucket", "replace", "--verify-run", "--apply"]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "unsafe" in _table_line(out, "realbad")
    assert (skill_dir / "SKILL.md").read_text(encoding="utf-8") == original
    assert not (skill_dir / "realbad.py").exists()
