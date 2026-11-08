"""CLI-level tests for the ``convert`` subcommand.

Drives the REAL pipeline end to end through cli.main: discovery -> analyze ->
convert.plan_conversion -> render/stage -> lint (lint.py) -> optional diff
(diffing.py) -> optional apply. Fixtures are written to a tmp dir and discovered
via ``--include`` with the real machine masked out (empty home, no project
roots), so nothing from ~/.claude leaks in.

Covers this agent's slice: the CLI orchestration + table, the ``--diff`` output,
the ``--apply`` backup + write-through, ``--name`` narrowing, and the lint gate
that blocks ``--apply`` on a broken script. Plus direct unit coverage of lint.py
and diffing.py.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from skill_to_code import cli


# ── fixtures on disk ─────────────────────────────────────────────────────────

_REPLACE_SKILL = """---
name: git-plumbing
description: mechanical git.
---

# git-plumbing

Run the commands:

```bash
$ git add -A
$ git commit -m "$MESSAGE"
```

Then push:

```bash
$ git push origin "$BRANCH"
```
"""

_NOOP_SKILL = """---
name: data-only
description: dense but only data blocks.
---

# data-only

Config:

```json
{"a": 1, "b": 2}
```

Schema:

```yaml
name: x
value: y
```
"""

_BAD_SHELL_SKILL = """---
name: broken-shell
description: has a bash syntax error.
---

# broken-shell

Run it:

```bash
if [ -f foo
then echo hi
```
"""


@pytest.fixture
def skills_root(tmp_path):
    """A root with three skills: a replace, a noop (data-only), a broken shell."""
    root = tmp_path / "skills"
    for name, content in (
        ("git-plumbing", _REPLACE_SKILL),
        ("data-only", _NOOP_SKILL),
        ("broken-shell", _BAD_SHELL_SKILL),
    ):
        d = root / name
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(content, encoding="utf-8")
    return root


def _run(monkeypatch, argv):
    """Run the CLI with discovery pinned to only the --include paths."""
    from skill_to_code import discovery as _discovery

    real = _discovery.discover_skills

    def _pinned(includes=None, extra_project_roots=None, use_cache=True, **kw):
        return real(
            includes=includes,
            use_cache=False,
            home=str(Path(includes[0]).parent / "no-home"),
            project_roots=[],
        )

    monkeypatch.setattr(cli, "discover_skills", _pinned)
    return cli.main(argv)


def _table_line(out: str, skill_name: str) -> str:
    return next(l for l in out.splitlines() if l.startswith(skill_name))


# ── convert dry run ──────────────────────────────────────────────────────────


def test_dry_run_stages_without_touching_originals(skills_root, monkeypatch, capsys):
    original = (skills_root / "git-plumbing" / "SKILL.md").read_text(encoding="utf-8")

    rc = _run(monkeypatch, ["convert", "--include", str(skills_root),
                            "--name", "git-plumbing"])
    assert rc == 0
    out = capsys.readouterr().out

    # summary table shape
    assert "SKILL" in out and "BUCKET" in out and "STATUS" in out
    assert "git-plumbing" in out
    assert "converted" in _table_line(out, "git-plumbing")

    # original SKILL.md untouched
    assert (skills_root / "git-plumbing" / "SKILL.md").read_text(
        encoding="utf-8"
    ) == original

    # staged artifacts under .converted, NOT as siblings
    staged = skills_root / "git-plumbing" / ".converted"
    assert (staged / "SKILL.md").is_file()
    assert (staged / "git-plumbing.sh").is_file()
    assert (staged / "plan.json").is_file()
    assert not (skills_root / "git-plumbing" / "git-plumbing.sh").exists()

    # verbatim, prompt-stripped shell, with the bash header
    script = (staged / "git-plumbing.sh").read_text(encoding="utf-8")
    assert script.startswith("#!/usr/bin/env bash\nset -euo pipefail")
    assert "git add -A" in script
    assert "git push origin" in script
    assert "$ git add" not in script  # prompts stripped


def test_noop_skill_emits_no_script(skills_root, monkeypatch, capsys):
    rc = _run(monkeypatch, ["convert", "--include", str(skills_root),
                            "--name", "data-only"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "noop" in _table_line(out, "data-only")
    # honesty guard: nothing staged for a data-only skill
    assert not (skills_root / "data-only" / ".converted").exists()


def test_name_narrows_to_one_skill(skills_root, monkeypatch, capsys):
    rc = _run(monkeypatch, ["convert", "--include", str(skills_root),
                            "--name", "git-plumbing"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "git-plumbing" in out
    assert "data-only" not in out
    assert "broken-shell" not in out


def test_bucket_override_forces_wrap(skills_root, monkeypatch, capsys):
    rc = _run(monkeypatch, ["convert", "--include", str(skills_root),
                            "--name", "git-plumbing", "--bucket", "wrap"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "wrap" in _table_line(out, "git-plumbing")
    # WRAP keeps prose + adds step references
    rewritten = (skills_root / "git-plumbing" / ".converted" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    assert "Run step" in rewritten
    assert ".converted/git-plumbing.sh" in rewritten


# ── diff ─────────────────────────────────────────────────────────────────────


def test_diff_emits_unified_diff(skills_root, monkeypatch, capsys):
    rc = _run(monkeypatch, ["convert", "--include", str(skills_root),
                            "--name", "git-plumbing", "--bucket", "replace",
                            "--diff"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "--- a/git-plumbing/SKILL.md" in out
    assert "+++ b/git-plumbing/SKILL.md" in out
    assert "@@" in out
    assert "This skill runs `.converted/git-plumbing.sh`." in out


# ── apply ────────────────────────────────────────────────────────────────────


def test_apply_writes_through_and_backs_up(skills_root, monkeypatch, capsys):
    original = (skills_root / "git-plumbing" / "SKILL.md").read_text(encoding="utf-8")

    rc = _run(monkeypatch, ["convert", "--include", str(skills_root),
                            "--name", "git-plumbing", "--bucket", "replace",
                            "--apply"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "converted" in _table_line(out, "git-plumbing")

    real_md = skills_root / "git-plumbing" / "SKILL.md"
    rewritten = real_md.read_text(encoding="utf-8")
    assert "This skill runs `.converted/git-plumbing.sh`." in rewritten
    assert rewritten != original

    # backup holds the ORIGINAL byte-for-byte
    backup = skills_root / "git-plumbing" / ".converted" / ".backup" / "SKILL.md"
    assert backup.is_file()
    assert backup.read_text(encoding="utf-8") == original

    # script placed as a sibling of the real SKILL.md
    assert (skills_root / "git-plumbing" / "git-plumbing.sh").is_file()


def test_lint_failure_blocks_apply(skills_root, monkeypatch, capsys):
    if shutil.which("bash") is None:
        pytest.skip("bash unavailable: `bash -n` is skipped, cannot fail the lint")

    original = (skills_root / "broken-shell" / "SKILL.md").read_text(encoding="utf-8")

    rc = _run(monkeypatch, ["convert", "--include", str(skills_root),
                            "--name", "broken-shell", "--apply"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "unsafe" in _table_line(out, "broken-shell")

    # --apply refused: real SKILL.md untouched, no sibling script written
    assert (skills_root / "broken-shell" / "SKILL.md").read_text(
        encoding="utf-8"
    ) == original
    assert not (skills_root / "broken-shell" / "broken-shell.sh").exists()


def test_diff_after_apply_uses_backup_as_original(skills_root, monkeypatch, capsys):
    # --diff together with --apply must still show the real change (backup ->
    # rewritten), not an empty diff of the already-overwritten file against itself.
    rc = _run(monkeypatch, ["convert", "--include", str(skills_root),
                            "--name", "git-plumbing", "--bucket", "replace",
                            "--apply", "--diff"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "@@" in out
    assert "-$ git add -A" in out  # a line from the ORIGINAL body
    assert "This skill runs `.converted/git-plumbing.sh`." in out


# ── idempotency (unchanged) ──────────────────────────────────────────────────


def test_second_run_reports_unchanged(skills_root, monkeypatch, capsys):
    argv = ["convert", "--include", str(skills_root), "--name", "git-plumbing"]
    assert _run(monkeypatch, argv) == 0
    capsys.readouterr()  # drain first run's output
    assert _run(monkeypatch, argv) == 0
    out = capsys.readouterr().out
    assert "unchanged" in _table_line(out, "git-plumbing")


# ── lint.py unit coverage ────────────────────────────────────────────────────


def test_lint_python_ok_and_fail(tmp_path):
    from skill_to_code.lint import lint_script

    good = tmp_path / "good.py"
    good.write_text("x = 1\nprint(x)\n", encoding="utf-8")
    ok, out = lint_script(str(good))
    assert ok, out

    bad = tmp_path / "bad.py"
    bad.write_text("def (:\n", encoding="utf-8")
    ok, out = lint_script(str(bad))
    assert not ok
    assert "FAILED" in out


def test_lint_unknown_ext_is_skip(tmp_path):
    from skill_to_code.lint import lint_script

    f = tmp_path / "thing.js"
    f.write_text("var x = 1;\n", encoding="utf-8")
    ok, out = lint_script(str(f))
    assert ok
    assert "no linter" in out


def test_lint_scripts_ands_results(tmp_path):
    from skill_to_code.lint import lint_scripts

    good = tmp_path / "g.py"
    good.write_text("y = 2\n", encoding="utf-8")
    bad = tmp_path / "b.py"
    bad.write_text("=\n", encoding="utf-8")
    ok, out = lint_scripts([str(good), str(bad)])
    assert not ok
    assert "g.py" in out and "b.py" in out


def test_lint_shell_skip_note_when_bash_absent(tmp_path, monkeypatch):
    import skill_to_code.lint as lint_mod

    monkeypatch.setattr(lint_mod.shutil, "which", lambda name: None)
    f = tmp_path / "x.sh"
    f.write_text("echo hi\n", encoding="utf-8")
    ok, out = lint_mod.lint_script(str(f))
    assert ok
    assert "bash unavailable" in out


# ── diffing.py unit coverage ─────────────────────────────────────────────────


def test_unified_diff_basic():
    from skill_to_code.diffing import unified_diff

    a = "line1\nline2\n"
    b = "line1\nCHANGED\n"
    d = unified_diff(a, b, "x/SKILL.md")
    assert "--- a/x/SKILL.md" in d
    assert "+++ b/x/SKILL.md" in d
    assert "-line2" in d
    assert "+CHANGED" in d


def test_unified_diff_identical_is_empty():
    from skill_to_code.diffing import unified_diff

    assert unified_diff("same\n", "same\n", "x") == ""
