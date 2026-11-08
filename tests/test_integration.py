"""End-to-end regression backbone for the M1 read-only audit.

Runs the real pipeline (discover -> parse -> analyze -> filter -> report) over
the checked-in fixture skills in tests/fixtures and asserts each named fixture
lands in its expected bucket, that the .skipconvert fixture is excluded, and
that every output format renders.

Discovery is pinned to the fixtures via ``includes=`` with an empty ``home`` and
``project_roots=[]`` so nothing from the real machine (~/.claude, ~/github,
~/code) leaks into the exact-set assertions. A separate smoke test drives the
actual CLI and only asserts the fixtures are a subset of whatever it finds.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from skill_to_code.analyzer import analyze, load_rubric
from skill_to_code.discovery import discover_skills
from skill_to_code.parser import parse_skill
from skill_to_code.report import (
    apply_filters,
    format_json,
    format_md,
    format_table,
)
from skill_to_code import cli

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

# Expected bucket per fixture. skip-me is intentionally absent: it carries a
# .skipconvert sentinel and must never be discovered.
EXPECTED_BUCKETS = {
    "git-plumbing": "replace",
    "log-triage": "wrap",
    "dep-upgrade": "wrap",
    "design-brainstorm": "skip",
    "code-review-mentor": "skip",
}
EXCLUDED_FIXTURE = "skip-me"


def _discover_fixtures(empty_home: Path):
    """Discover ONLY the fixtures, with the real machine masked out."""
    return discover_skills(
        includes=[str(FIXTURES_DIR)],
        home=str(empty_home),
        project_roots=[],
        use_cache=False,
    )


@pytest.fixture
def empty_home(tmp_path: Path) -> Path:
    """A home dir with no ~/.claude, so user/plugin discovery finds nothing."""
    return tmp_path / "home"


@pytest.fixture
def results(empty_home: Path):
    """Full pipeline output for the fixture set."""
    rubric = load_rubric()
    discovered = _discover_fixtures(empty_home)
    return [analyze(parse_skill(d), d, rubric) for d in discovered]


def _by_name(results):
    return {r.skill.name: r for r in results}


def test_discovers_exactly_the_expected_fixtures(results):
    names = {r.skill.name for r in results}
    assert names == set(EXPECTED_BUCKETS), (
        f"unexpected discovery set: {sorted(names)}"
    )


def test_skipconvert_fixture_is_excluded(results):
    names = {r.skill.name for r in results}
    assert EXCLUDED_FIXTURE not in names
    # And prove the sentinel really is on disk, so this test can't pass vacuously.
    assert (FIXTURES_DIR / EXCLUDED_FIXTURE / ".skipconvert").is_file()
    assert (FIXTURES_DIR / EXCLUDED_FIXTURE / "SKILL.md").is_file()


@pytest.mark.parametrize("name,expected", sorted(EXPECTED_BUCKETS.items()))
def test_each_fixture_lands_in_expected_bucket(results, name, expected):
    by_name = _by_name(results)
    assert name in by_name, f"{name} was not discovered"
    r = by_name[name]
    assert r.bucket == expected, (
        f"{name}: score={r.score} bucket={r.bucket} "
        f"(expected {expected}); signals={r.signals}"
    )
    assert 0 <= r.score <= 100


def test_replace_fixture_fires_all_expected_signals(results):
    r = _by_name(results)["git-plumbing"]
    sig = r.signals
    assert sig["sibling_scripts"] == 1.0  # convert.py sibling
    assert sig["structured_io"] == 1.0    # Input:/Output: present
    assert sig["command_lines"] == 1.0    # many $-prefixed bash lines
    assert sig["mech"] > sig["judg"]
    assert any("sibling script" in reason for reason in r.reasons)


def test_skip_fixtures_are_judgment_heavy(results):
    by_name = _by_name(results)
    for name in ("design-brainstorm", "code-review-mentor"):
        r = by_name[name]
        assert r.signals["judg"] > r.signals["mech"]
        assert any("judgment-heavy" in reason for reason in r.reasons)


def test_source_is_include_for_fixtures(results):
    assert results, "no results discovered"
    assert all(r.skill.source == "include" for r in results)


# ── report formats all render ────────────────────────────────────────────────


def test_format_table_renders(results):
    out = format_table(results)
    assert "SCORE" in out and "BUCKET" in out
    for name in EXPECTED_BUCKETS:
        assert name in out
    # ranked by score desc: replace fixture outranks the skip fixtures.
    assert out.index("git-plumbing") < out.index("design-brainstorm")


def test_format_json_renders_stable_schema(results):
    payload = json.loads(format_json(results))
    assert payload["count"] == len(EXPECTED_BUCKETS)
    assert set(payload["summary"]) == {"replace", "wrap", "skip"}
    names = {rec["name"] for rec in payload["skills"]}
    assert names == set(EXPECTED_BUCKETS)
    for rec in payload["skills"]:
        assert rec["bucket"] == EXPECTED_BUCKETS[rec["name"]]
        assert {"code_density", "mechanical_ratio", "mech", "judg"} <= set(
            rec["signals"]
        )
        assert rec["source"] == "include"


def test_format_md_renders(results):
    out = format_md(results)
    assert "# Skill-to-Code audit" in out
    assert "| Bucket | Count |" in out
    for name in EXPECTED_BUCKETS:
        assert name in out


# ── filters, combinable ──────────────────────────────────────────────────────


def test_bucket_filter(results):
    only_replace = apply_filters(results, bucket="replace")
    assert {r.skill.name for r in only_replace} == {"git-plumbing"}

    only_skip = apply_filters(results, bucket="skip")
    assert {r.skill.name for r in only_skip} == {
        "design-brainstorm",
        "code-review-mentor",
    }


def test_min_score_filter(results):
    high = apply_filters(results, min_score=70)
    assert {r.skill.name for r in high} == {"git-plumbing"}
    assert all(r.score >= 70 for r in high)


def test_source_filter(results):
    assert apply_filters(results, source="include") == results
    assert apply_filters(results, source="user") == []


def test_filters_combine(results):
    combined = apply_filters(
        results, bucket="wrap", min_score=35, source="include"
    )
    assert {r.skill.name for r in combined} == {"log-triage", "dep-upgrade"}


# ── CLI smoke: real entry point renders each format without error ────────────


@pytest.fixture
def pinned_cli(monkeypatch, empty_home):
    """Run the real cli.main pipeline but mask the machine: force discovery to
    an empty home and no project roots, so only --include fixtures are found.
    Keeps the smoke test hermetic and fast (no ~/github / ~/code walk)."""
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


@pytest.mark.parametrize("fmt", ["table", "json", "md"])
def test_cli_scan_renders_each_format(pinned_cli, capsys, fmt):
    rc = cli.main(["scan", "--format", fmt, "--include", str(FIXTURES_DIR)])
    assert rc == 0
    out = capsys.readouterr().out
    assert out.strip()
    for name in EXPECTED_BUCKETS:
        assert name in out
    assert EXCLUDED_FIXTURE not in out
    if fmt == "json":
        payload = json.loads(out)
        found = {rec["name"]: rec["bucket"] for rec in payload["skills"]}
        assert found == EXPECTED_BUCKETS


def test_cli_scan_bucket_filter(pinned_cli, capsys):
    rc = cli.main(
        ["scan", "--format", "json", "--bucket", "skip",
         "--include", str(FIXTURES_DIR)]
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    found = {rec["name"] for rec in payload["skills"]}
    assert found == {"design-brainstorm", "code-review-mentor"}
