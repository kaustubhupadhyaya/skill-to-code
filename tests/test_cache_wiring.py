"""Tests for the discovery + analysis cache wiring.

The cache lives under ``home``. We drive the real scan pipeline
(``discover_skills`` -> ``analyze(parse_skill(d), d, rubric)``) exactly as the
CLI does, flushing the session between "runs" to simulate separate invocations.
A per-test reset keeps the process-wide session from leaking across tests.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from skill_to_code import analyzer, cache
from skill_to_code.analyzer import analyze, load_rubric
from skill_to_code.discovery import discover_skills
from skill_to_code.parser import parse_skill

CODE_HEAVY = (
    "# Convert\n\n"
    "Input: a file path.\nOutput: a converted file.\n\n"
    "Run the script to convert. Copy, download, then extract.\n\n"
    "```bash\n"
    "$ curl -O https://example.com/data.zip\n"
    "$ unzip data.zip\n"
    "$ python convert.py --in data --out result\n"
    "$ git add -A\n"
    "```\n"
)

PROSE = (
    "# Design Review\n\n"
    "Consider the proposal. Evaluate the tradeoffs and assess the risks.\n"
    "Interpret the requirements, weigh the options, and recommend a direction.\n"
    "Analyze the design, critique the approach, and judge whether it is sound.\n"
)


@pytest.fixture(autouse=True)
def _reset_session():
    """Isolate the process-wide analysis session for each test."""
    cache._session = None
    cache._session_home = None
    cache._session_dirty = False
    yield
    cache._session = None
    cache._session_home = None
    cache._session_dirty = False


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: h))
    return h


@pytest.fixture
def root(tmp_path: Path) -> Path:
    r = tmp_path / "skills"
    r.mkdir()
    return r


def _make_skill(root: Path, name: str, body: str) -> Path:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\n---\n{body}", encoding="utf-8")
    return d


def _run(home: Path, root: Path, *, use_cache: bool = True) -> dict:
    """One scan pipeline pass over ``root`` (as the CLI does), then flush."""
    rubric = load_rubric()
    discovered = discover_skills(
        includes=[str(root)],
        home=str(home),
        project_roots=[],
        use_cache=use_cache,
    )
    results = {}
    for d in discovered:
        results[d.name] = analyze(parse_skill(d), d, rubric)
    cache.flush()
    return results


def _read_cache(home: Path) -> dict:
    return json.loads(cache.cache_path(str(home)).read_text(encoding="utf-8"))


def _entry_for(home: Path, skill_md: Path) -> dict:
    return _read_cache(home)["analysis"][cache._norm(str(skill_md))]


def _spy(monkeypatch: pytest.MonkeyPatch) -> list:
    """Count calls into the real scorer; returns a list to len()."""
    calls: list = []
    real = analyzer._analyze_impl

    def counting(parsed, discovered, rubric):
        calls.append(discovered.name)
        return real(parsed, discovered, rubric)

    monkeypatch.setattr(analyzer, "_analyze_impl", counting)
    return calls


# ── population + warm serve ─────────────────────────────────────────────────


def test_first_run_populates_cache(home: Path, root: Path):
    _make_skill(root, "alpha", CODE_HEAVY)
    _make_skill(root, "beta", PROSE)

    _run(home, root)

    ns = _read_cache(home)["analysis"]
    assert len(ns) == 2
    for entry in ns.values():
        assert "mtime" in entry and "sha256" in entry
        assert set(entry["record"]) == {"score", "bucket", "reasons", "signals"}


def test_second_run_served_from_cache(home: Path, root: Path, monkeypatch):
    _make_skill(root, "alpha", CODE_HEAVY)
    _make_skill(root, "beta", PROSE)

    first = _run(home, root)
    calls = _spy(monkeypatch)
    second = _run(home, root)

    assert calls == []  # nothing re-scored on the warm run
    assert {n: r.score for n, r in first.items()} == {n: r.score for n, r in second.items()}
    assert {n: r.bucket for n, r in first.items()} == {n: r.bucket for n, r in second.items()}


def test_warm_run_trusts_cache_contents(home: Path, root: Path):
    """A poisoned record is served verbatim -> proves the cache is trusted."""
    skill = _make_skill(root, "alpha", CODE_HEAVY)
    _run(home, root)

    whole = _read_cache(home)
    whole["analysis"][cache._norm(str(skill / "SKILL.md"))]["record"]["score"] = 999
    cache.cache_path(str(home)).write_text(json.dumps(whole), encoding="utf-8")

    served = _run(home, root)
    assert served["alpha"].score == 999


# ── invalidation: edit / add / remove ───────────────────────────────────────


def test_edit_invalidates_only_that_entry(home: Path, root: Path, monkeypatch):
    a = _make_skill(root, "alpha", PROSE)
    _make_skill(root, "beta", PROSE)
    first = _run(home, root)

    # Poison beta so a warm serve is detectable; edit alpha so it must recompute.
    whole = _read_cache(home)
    b_md = root / "beta" / "SKILL.md"
    whole["analysis"][cache._norm(str(b_md))]["record"]["score"] = 999
    cache.cache_path(str(home)).write_text(json.dumps(whole), encoding="utf-8")

    a_md = a / "SKILL.md"
    a_md.write_text(f"---\nname: alpha\n---\n{CODE_HEAVY}", encoding="utf-8")
    bumped = a_md.stat().st_mtime + 10
    os.utime(a_md, (bumped, bumped))

    calls = _spy(monkeypatch)
    second = _run(home, root)

    assert calls == ["alpha"]  # only the edited skill was re-scored
    assert second["beta"].score == 999  # unchanged skill still served from cache
    assert second["alpha"].score != first["alpha"].score  # reflects the edit


def test_added_skill_is_scored_and_cached(home: Path, root: Path, monkeypatch):
    _make_skill(root, "alpha", CODE_HEAVY)
    _run(home, root)

    _make_skill(root, "beta", PROSE)
    calls = _spy(monkeypatch)
    second = _run(home, root)

    assert calls == ["beta"]  # only the new skill was scored
    assert set(second) == {"alpha", "beta"}
    assert len(_read_cache(home)["analysis"]) == 2


def test_removed_skill_drops_out_of_cache(home: Path, root: Path):
    _make_skill(root, "alpha", CODE_HEAVY)
    b = _make_skill(root, "beta", PROSE)
    _run(home, root)
    assert len(_read_cache(home)["analysis"]) == 2

    import shutil

    shutil.rmtree(b)
    second = _run(home, root)

    assert set(second) == {"alpha"}
    ns = _read_cache(home)["analysis"]
    assert len(ns) == 1
    assert cache._norm(str(b / "SKILL.md")) not in ns


# ── --no-cache bypasses read AND write ──────────────────────────────────────


def test_no_cache_bypasses_read_and_write(home: Path, root: Path):
    skill = _make_skill(root, "alpha", CODE_HEAVY)
    real = _run(home, root)["alpha"].score

    # Poison the on-disk cache. A cached read would surface 999.
    key = cache._norm(str(skill / "SKILL.md"))
    whole = _read_cache(home)
    whole["analysis"][key]["record"]["score"] = 999
    cache.cache_path(str(home)).write_text(json.dumps(whole), encoding="utf-8")

    result = _run(home, root, use_cache=False)

    assert result["alpha"].score == real  # read bypassed (not the poisoned 999)
    # Write bypassed: the on-disk cache is left exactly as poisoned.
    assert _read_cache(home)["analysis"][key]["record"]["score"] == 999


def test_no_cache_does_not_create_cache_file(home: Path, root: Path):
    _make_skill(root, "alpha", CODE_HEAVY)
    _run(home, root, use_cache=False)
    assert not cache.cache_path(str(home)).exists()


# ── touched-but-unchanged: mtime moved, content identical ───────────────────


def test_touch_without_content_change_serves_cache(home: Path, root: Path, monkeypatch):
    skill = _make_skill(root, "alpha", CODE_HEAVY)
    _run(home, root)

    md = skill / "SKILL.md"
    bumped = md.stat().st_mtime + 10
    os.utime(md, (bumped, bumped))  # mtime moved, bytes identical

    calls = _spy(monkeypatch)
    _run(home, root)

    assert calls == []  # sha256 match -> re-scoring skipped despite new mtime
