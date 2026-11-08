"""Tests for skill_to_code.report — formatting and filtering.

Builds AnalysisResult objects directly (no filesystem, no scoring) so these
tests exercise only the report module's contract.
"""

from __future__ import annotations

import json

import pytest

from skill_to_code.report import (
    apply_filters,
    format_json,
    format_md,
    format_table,
)
from skill_to_code.types import AnalysisResult, Bucket, DiscoveredSkill, Source


def _skill(name: str, source: str, plugin=None) -> DiscoveredSkill:
    return DiscoveredSkill(
        path=f"/skills/{name}/SKILL.md",
        dir=f"/skills/{name}",
        name=name,
        source=source,
        plugin=plugin,
        mtime=0.0,
    )


def _result(name, score, bucket, source, reasons=None, signals=None) -> AnalysisResult:
    return AnalysisResult(
        skill=_skill(name, source),
        score=score,
        bucket=bucket,
        reasons=reasons if reasons is not None else [f"reason-{name}"],
        signals=signals if signals is not None else {"code_density": 0.5, "mech": 3},
    )


@pytest.fixture
def results() -> list[AnalysisResult]:
    return [
        _result("wrap-mid", 50, Bucket.WRAP.value, Source.PLUGIN.value),
        _result("replace-top", 90, Bucket.REPLACE.value, Source.USER.value),
        _result("skip-low", 10, Bucket.SKIP.value, Source.PROJECT.value),
        _result("replace-mid", 72, Bucket.REPLACE.value, Source.INCLUDE.value),
    ]


# ── table ────────────────────────────────────────────────────────────────────


def test_table_has_header_and_all_rows(results):
    out = format_table(results)
    lines = out.splitlines()
    assert lines[0].split() == ["SCORE", "BUCKET", "SOURCE", "NAME", "TOP", "REASON"]
    for r in results:
        assert r.skill.name in out
    # summary line reflects bucket counts
    assert "replace=2" in out
    assert "wrap=1" in out
    assert "skip=1" in out


def test_table_rows_are_score_descending(results):
    out = format_table(results)
    lines = out.splitlines()
    # Data rows start after header + separator; stop before the blank/summary.
    data = [ln for ln in lines[2:] if ln and not ln.startswith("4 skills")]
    scores = [int(ln.split()[0]) for ln in data]
    assert scores == sorted(scores, reverse=True)
    assert scores == [90, 72, 50, 10]


def test_table_top_reason_shown(results):
    out = format_table(results)
    assert "reason-replace-top" in out


# ── json ─────────────────────────────────────────────────────────────────────


def test_json_round_trips_with_expected_keys(results):
    payload = json.loads(format_json(results))
    assert payload["count"] == 4
    assert payload["summary"] == {"replace": 2, "wrap": 1, "skip": 1}
    assert len(payload["skills"]) == 4
    rec = payload["skills"][0]
    for key in ("name", "score", "bucket", "source", "plugin", "path", "dir", "reasons", "signals"):
        assert key in rec
    # ranked score-desc; first record is the top scorer
    assert rec["name"] == "replace-top"
    assert rec["score"] == 90
    assert rec["signals"]["mech"] == 3


def test_json_skills_are_score_descending(results):
    payload = json.loads(format_json(results))
    scores = [s["score"] for s in payload["skills"]]
    assert scores == [90, 72, 50, 10]


# ── md ───────────────────────────────────────────────────────────────────────


def test_md_contains_bucket_counts_and_table(results):
    out = format_md(results)
    assert "# Skill-to-Code audit" in out
    assert "4 skills scanned" in out
    assert "| replace | 2 |" in out
    assert "| wrap | 1 |" in out
    assert "| skip | 1 |" in out
    # every skill name appears in the table
    for r in results:
        assert r.skill.name in out


def test_md_table_escapes_pipes():
    r = _result("x", 60, Bucket.WRAP.value, Source.USER.value, reasons=["a | b"])
    out = format_md([r])
    assert "a \\| b" in out


# ── filters ──────────────────────────────────────────────────────────────────


def test_filter_by_bucket(results):
    out = apply_filters(results, bucket=Bucket.REPLACE.value)
    assert {r.skill.name for r in out} == {"replace-top", "replace-mid"}


def test_filter_by_min_score(results):
    out = apply_filters(results, min_score=70)
    assert {r.score for r in out} == {90, 72}


def test_filter_by_source(results):
    out = apply_filters(results, source=Source.USER.value)
    assert [r.skill.name for r in out] == ["replace-top"]


def test_filters_combine(results):
    out = apply_filters(
        results, bucket=Bucket.REPLACE.value, min_score=80, source=Source.USER.value
    )
    assert [r.skill.name for r in out] == ["replace-top"]


def test_filter_no_criteria_returns_all(results):
    assert apply_filters(results) == results


def test_empty_results_render():
    assert "0 skills" in format_table([])
    assert json.loads(format_json([]))["count"] == 0
    assert "0 skills scanned" in format_md([])
