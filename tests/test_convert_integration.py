"""End-to-end regression backbone for M2 conversion.

Runs the real conversion pipeline (discover -> analyze -> extract -> plan ->
render -> rewrite -> stage -> lint -> apply) over dedicated, checked-in fixture
skills in ``tests/convert_fixtures`` and asserts the four locked behaviors:

    replace-deploy  REPLACE: one dominant-language script, frontmatter-preserving
                    stub, non-dominant language recorded as skipped.
    wrap-triage     WRAP: prose preserved, each code block replaced in place by a
                    ``Run step N of `.converted/<script>`.`` reference; one helper
                    script per language.
    decoy-config    DECOY (honesty guard): scores ``replace`` on density but every
                    fence is json/yaml -> NO extractable code -> status "noop",
                    no script emitted, nothing written to disk.
    console-status  console prompt heuristic: ``$``-prompted commands kept
                    (prompts stripped), interleaved output lines dropped.

Plus: idempotency (a second dry run over the same SKILL.md is "unchanged"),
apply-on-a-copy (creates ``.converted/.backup/SKILL.md`` and overwrites the real
SKILL.md), and static lint of a generated python script via ``py_compile``.

These fixtures live OUTSIDE ``tests/fixtures`` on purpose: the M1 integration
suite asserts the *exact* discovery set of ``tests/fixtures``, so conversion
fixtures must not leak into it.

Discovery is pinned to the fixtures with ``home=`` an empty dir and
``project_roots=[]`` so nothing from the real machine leaks in. Staging/applying
always happens on a *copy* under ``tmp_path`` so the checked-in fixtures are
never mutated.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from skill_to_code import convert, extract, lint
from skill_to_code.analyzer import analyze, load_rubric
from skill_to_code.discovery import discover_skills
from skill_to_code.parser import parse_skill
from skill_to_code.types import ConversionResult

CONVERT_FIXTURES = Path(__file__).resolve().parent / "convert_fixtures"

# Natural analyzer bucket per fixture. decoy-config scores in the replace band
# by density alone — that is the whole point of the honesty guard.
FIXTURE_BUCKETS = {
    "replace-deploy": "replace",
    "wrap-triage": "wrap",
    "decoy-config": "replace",
    "console-status": "replace",
}


# ── plumbing ─────────────────────────────────────────────────────────────────


@pytest.fixture
def empty_home(tmp_path: Path) -> Path:
    """A home with no ~/.claude, so user/plugin discovery finds nothing."""
    return tmp_path / "home"


def _discover_one(name: str, root: Path, empty_home: Path):
    """Discover the top-level fixture skill dir, with the real machine masked.

    Selects the skill whose directory is exactly ``root/name`` — discovery walks
    recursively, so a staged ``.converted/SKILL.md`` from a prior conversion
    would otherwise show up as a second skill and break the pick.
    """
    discovered = discover_skills(
        includes=[str(root / name)],
        home=str(empty_home),
        project_roots=[],
        use_cache=False,
    )
    target = str((root / name).resolve()).casefold()
    top = [d for d in discovered if str(Path(d.dir).resolve()).casefold() == target]
    assert len(top) == 1, (
        f"{name}: expected one top-level skill, got {[d.dir for d in discovered]}"
    )
    return top[0]


def _dry_run(name: str, root: Path, empty_home: Path, *, bucket=None) -> ConversionResult:
    """Full dry-run pipeline for one fixture: analyze -> plan -> render ->
    rewrite -> stage -> lint. Mirrors what ``convert`` does before ``--apply``.
    No backup, no write-through to the real SKILL.md.
    """
    rubric = load_rubric()
    discovered = _discover_one(name, root, empty_home)
    parsed = parse_skill(discovered)
    analysis = analyze(parsed, discovered, rubric)

    plan = convert.plan_conversion(discovered, parsed, analysis, bucket=bucket)
    scripts = convert.render_scripts(plan)
    original = discovered_text(discovered.path)
    rewritten = convert.rewrite_skill_md(plan, parsed, original)
    result = convert.stage(plan, scripts, rewritten, discovered.path)

    if result.status == "converted" and result.script_paths:
        ok, output = lint.lint_scripts(result.script_paths)
        result.lint_ok = ok
        result.lint_output = output
        if not ok:
            result.status = "unsafe"
    return result


def discovered_text(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


@pytest.fixture
def fixtures_copy(tmp_path: Path) -> Path:
    """A writable copy of the whole convert_fixtures tree under tmp.

    Staging writes ``<skill_dir>/.converted``; applying overwrites SKILL.md.
    Everything mutating runs against this copy so the checked-in fixtures stay
    pristine and tests never race on shared on-disk state.
    """
    dest = tmp_path / "convert_fixtures"
    shutil.copytree(CONVERT_FIXTURES, dest)
    return dest


# ── fixtures score into their expected analyzer bucket ───────────────────────


@pytest.mark.parametrize("name,expected", sorted(FIXTURE_BUCKETS.items()))
def test_fixture_lands_in_expected_bucket(name, expected, fixtures_copy, empty_home):
    rubric = load_rubric()
    d = _discover_one(name, fixtures_copy, empty_home)
    a = analyze(parse_skill(d), d, rubric)
    assert a.bucket == expected, f"{name}: score={a.score} bucket={a.bucket}"


# ── REPLACE: one dominant-language script + frontmatter-preserving stub ───────


def test_replace_stages_one_dominant_language_script(fixtures_copy, empty_home):
    result = _dry_run("replace-deploy", fixtures_copy, empty_home)

    assert result.status == "converted"
    assert result.plan.eligible is True
    assert result.plan.dominant_lang == "bash"

    # Exactly one script, the dominant (.sh) one.
    assert len(result.script_paths) == 1
    script = Path(result.script_paths[0])
    assert script.name == "replace-deploy.sh"

    text = script.read_text(encoding="utf-8")
    lines = text.splitlines()
    assert lines[0] == "#!/usr/bin/env bash"
    assert lines[1] == "set -euo pipefail"

    # Every bash command lifted verbatim, in document order (both bash blocks
    # concatenated). The python block is NOT in this script.
    expected_cmds = [
        'git fetch origin',
        'git checkout "$TAG"',
        'make build',
        'tar -czf release.tgz ./dist',
        'sha256sum release.tgz > release.sha256',
        'aws s3 cp release.tgz "s3://$BUCKET/release.tgz"',
        'aws s3 cp release.sha256 "s3://$BUCKET/release.sha256"',
    ]
    body_cmds = [ln for ln in lines[2:] if ln.strip()]
    assert body_cmds == expected_cmds
    assert "import json" not in text  # the python block did not leak in

    # The non-dominant python block is recorded as skipped, not emitted.
    assert "non-dominant language: python" in result.plan.skipped_blocks


def test_replace_rewrites_to_frontmatter_preserving_stub(fixtures_copy, empty_home):
    result = _dry_run("replace-deploy", fixtures_copy, empty_home)
    rewritten = Path(result.rewritten_skill_md).read_text(encoding="utf-8")

    # Original YAML frontmatter is preserved verbatim.
    assert rewritten.startswith("---\n")
    assert "name: replace-deploy" in rewritten
    assert (
        "description: Mechanically build, package, and upload the release artifact."
        in rewritten
    )

    # Body collapses to the one-line wrapper note pointing at the staged script.
    assert "This skill runs `.converted/replace-deploy.sh`." in rewritten
    # The stub does not carry the original procedure prose forward.
    assert "git fetch origin" not in rewritten
    assert "## Steps" not in rewritten


# ── WRAP: prose preserved, each segment replaced by a step reference ──────────


def test_wrap_preserves_prose_and_inserts_step_references(fixtures_copy, empty_home):
    result = _dry_run("wrap-triage", fixtures_copy, empty_home)

    assert result.status == "converted"
    assert result.plan.bucket == "wrap"
    # bash / python / bash -> three distinct segments.
    assert [s.lang for s in result.plan.segments] == ["bash", "python", "bash"]

    rewritten = Path(result.rewritten_skill_md).read_text(encoding="utf-8")

    # Judgment prose and structure survive untouched.
    assert "## Make the call" in rewritten
    assert "Use\njudgment here" in rewritten
    assert "Input: the `SERVICE` name to inspect." in rewritten
    assert "## Fetch the metrics" in rewritten
    # Prose paragraphs BETWEEN code blocks are preserved (not swallowed).
    assert "Now parse the JSON and pull out the p99 latency series." in rewritten
    assert "Finally sort and count the series into a small table." in rewritten

    # Each extracted code block is replaced in place by a step reference; the raw
    # commands no longer appear in the prose.
    assert "Run step 1 of `.converted/wrap-triage.sh`." in rewritten
    assert "Run step 2 of `.converted/wrap-triage.py`." in rewritten
    assert "Run step 3 of `.converted/wrap-triage.sh`." in rewritten
    assert 'curl -s "https://metrics.example.com/$SERVICE"' not in rewritten
    assert "sort -rn p99.txt" not in rewritten


def test_wrap_emits_one_helper_script_per_language(fixtures_copy, empty_home):
    result = _dry_run("wrap-triage", fixtures_copy, empty_home)

    names = {Path(p).name for p in result.script_paths}
    assert names == {"wrap-triage.sh", "wrap-triage.py"}

    by_name = {Path(p).name: Path(p).read_text(encoding="utf-8") for p in result.script_paths}

    sh = by_name["wrap-triage.sh"]
    assert sh.splitlines()[0] == "#!/usr/bin/env bash"
    # Both bash segments live in the .sh helper, each a labeled step section.
    assert "# --- step 1 ---" in sh
    assert "# --- step 3 ---" in sh
    assert 'curl -s "https://metrics.example.com/$SERVICE" > metrics.json' in sh
    assert "sort -rn p99.txt | head -20 > top.txt" in sh

    py = by_name["wrap-triage.py"]
    assert py.splitlines()[0] == "#!/usr/bin/env python3"
    assert "# --- step 2 ---" in py
    assert 'data = json.load(open("metrics.json"))' in py


# ── DECOY: honesty guard -> noop, nothing written ────────────────────────────


def test_decoy_is_noop_despite_replace_score(fixtures_copy, empty_home):
    result = _dry_run("decoy-config", fixtures_copy, empty_home)

    assert result.status == "noop"
    assert result.plan.eligible is False
    assert result.plan.reason == "no deterministically-extractable executable code"
    assert result.plan.segments == []
    assert result.script_paths == []

    # Every json/yaml fence is recorded as skipped with the standardized wording.
    assert result.plan.skipped_blocks == [
        "non-executable language: json",
        "non-executable language: yaml",
        "non-executable language: json",
    ]

    # A no-op writes nothing: no .converted/ dir appears in the skill dir.
    skill_dir = Path(result.plan.skill.dir)
    assert not (skill_dir / ".converted").exists()


def test_decoy_extractor_finds_zero_segments(fixtures_copy, empty_home):
    # Prove the guard fires at the extractor level, not just the planner.
    d = _discover_one("decoy-config", fixtures_copy, empty_home)
    segments = extract.extract_segments(parse_skill(d))
    assert segments == []


# ── console prompt heuristic: keep prompted commands, drop output ─────────────


def test_console_prompts_stripped_and_output_dropped(fixtures_copy, empty_home):
    result = _dry_run("console-status", fixtures_copy, empty_home)

    assert result.status == "converted"
    # Two consecutive console blocks -> one merged bash segment.
    assert len(result.plan.segments) == 1
    seg = result.plan.segments[0]
    assert seg.lang == "bash"

    # Only the $-prompted command lines survive, prompts removed. All of the
    # transcript OUTPUT lines are dropped.
    assert seg.lines == [
        "git status --short",
        "git add -A",
        "git status --short",
        "git diff --cached --name-only | wc -l",
        "git rev-parse --abbrev-ref HEAD",
    ]

    script_text = Path(result.script_paths[0]).read_text(encoding="utf-8")
    # No leftover prompt markers, no output lines.
    assert "$ " not in script_text
    for output_line in (" M src/app.py", "?? notes.txt", "A  notes.txt", "main"):
        assert output_line not in script_text


# ── idempotency: a second dry run over the same SKILL.md is "unchanged" ───────


def test_second_dry_run_is_idempotent(fixtures_copy, empty_home):
    first = _dry_run("replace-deploy", fixtures_copy, empty_home)
    assert first.status == "converted"

    skill_path = first.plan.skill.path
    # plan.json now holds the source sha256; re-running must detect no change.
    assert convert.is_unchanged(skill_path) is True

    # The higher-level convert path treats an unchanged skill as a no-op re-stage:
    # touching nothing new. We assert the idempotency token itself here (the CLI
    # maps this to status "unchanged").
    plan_json = json.loads(
        (Path(first.staged_dir) / "plan.json").read_text(encoding="utf-8")
    )
    assert "source_sha256" in plan_json

    # Editing SKILL.md breaks the token.
    p = Path(skill_path)
    p.write_text(p.read_text(encoding="utf-8") + "\nedited.\n", encoding="utf-8")
    assert convert.is_unchanged(skill_path) is False


# ── apply on a copy: backup created, real SKILL.md overwritten ────────────────


def test_apply_creates_backup_and_overwrites(fixtures_copy, empty_home):
    result = _dry_run("replace-deploy", fixtures_copy, empty_home)
    assert result.status == "converted"
    assert result.lint_ok is True  # bash present -> ok; bash absent -> skip-with-warning, still True

    skill_path = Path(result.plan.skill.path)
    original_text = skill_path.read_text(encoding="utf-8")

    applied = convert.apply(result)
    assert applied.applied is True
    assert applied.status == "converted"

    # Backup holds the ORIGINAL, byte-for-byte.
    backup = Path(applied.backup_path)
    assert backup.name == "SKILL.md"
    assert backup.parent.name == ".backup"
    assert backup.read_text(encoding="utf-8") == original_text

    # Real SKILL.md is now the stub.
    new_text = skill_path.read_text(encoding="utf-8")
    assert new_text != original_text
    assert "This skill runs `.converted/replace-deploy.sh`." in new_text
    assert new_text.startswith("---\n")  # frontmatter preserved

    # The generated script is placed as a sibling of SKILL.md.
    assert (skill_path.parent / "replace-deploy.sh").is_file()


def test_apply_refused_when_lint_fails(fixtures_copy, empty_home):
    result = _dry_run("replace-deploy", fixtures_copy, empty_home)
    skill_path = Path(result.plan.skill.path)
    original_text = skill_path.read_text(encoding="utf-8")

    # Simulate a lint failure and confirm apply refuses to write through.
    result.lint_ok = False
    result.lint_output = "py_compile FAILED (simulated)"

    refused = convert.apply(result)
    assert refused.status == "unsafe"
    assert refused.applied is False
    # The real SKILL.md is untouched.
    assert skill_path.read_text(encoding="utf-8") == original_text


# ── static lint runs on the generated python via py_compile ──────────────────


def test_generated_python_lints_clean_via_py_compile(fixtures_copy, empty_home):
    result = _dry_run("wrap-triage", fixtures_copy, empty_home)
    py_scripts = [p for p in result.script_paths if p.endswith(".py")]
    assert py_scripts, "wrap fixture should emit a python helper"

    ok, output = lint.lint_script(py_scripts[0])
    assert ok is True
    assert "py_compile ok" in output


def test_lint_catches_broken_python(tmp_path: Path):
    bad = tmp_path / "broken.py"
    bad.write_text("def (:\n", encoding="utf-8")  # syntactically invalid
    ok, output = lint.lint_script(str(bad))
    assert ok is False
    assert "FAILED" in output


# ── CLI smoke: the real `convert` entry point runs the whole pipeline ─────────


@pytest.fixture
def pinned_convert_cli(monkeypatch, empty_home, fixtures_copy):
    """Run the real cli.main convert pipeline but mask the machine: force
    discovery to an empty home and no project roots so only --include fixtures
    are found. Keeps the smoke test hermetic and off the real ~/.claude tree."""
    from skill_to_code import cli
    from skill_to_code import discovery as _discovery

    real = _discovery.discover_skills

    def _pinned(includes=None, extra_project_roots=None, use_cache=True, **kw):
        return real(
            includes=includes,
            use_cache=False,
            home=str(empty_home),
            project_roots=[],
        )

    monkeypatch.setattr(cli, "discover_skills", _pinned)
    return cli


def test_cli_convert_dry_run_prints_summary_table(pinned_convert_cli, capsys, fixtures_copy):
    cli = pinned_convert_cli
    rc = cli.main(["convert", "--include", str(fixtures_copy)])
    assert rc == 0
    out = capsys.readouterr().out
    assert out.strip()

    # Header row of the summary table.
    for col in ("SKILL", "BUCKET", "#SEG", "SCRIPT", "STATUS"):
        assert col in out

    # Each fixture appears with its expected end-state status.
    assert "replace-deploy" in out
    assert "wrap-triage" in out
    assert "console-status" in out
    # The decoy is a no-op (honesty guard) even though it scores replace.
    assert "decoy-config" in out
    assert "noop" in out
    assert "converted" in out


def test_cli_convert_name_filter_limits_to_one_skill(pinned_convert_cli, capsys, fixtures_copy):
    cli = pinned_convert_cli
    rc = cli.main(
        ["convert", "--name", "replace-deploy", "--include", str(fixtures_copy)]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "replace-deploy" in out
    assert "wrap-triage" not in out
    assert "decoy-config" not in out
