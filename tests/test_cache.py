"""Tests for skill_to_code.cache.

The cache lives under ``Path.home()``. We monkeypatch ``Path.home`` to point at
tmp_path so tests never touch the real ~/.claude directory.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from skill_to_code import cache


@pytest.fixture
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home


def _write_skill_md(tmp_path: Path, content: str = "hello\n") -> Path:
    p = tmp_path / "SKILL.md"
    p.write_text(content, encoding="utf-8")
    return p


def test_cache_paths_under_home(fake_home: Path):
    assert cache.cache_dir() == fake_home / ".claude" / "skill-to-code"
    assert cache.cache_path() == fake_home / ".claude" / "skill-to-code" / "cache.json"


def test_load_missing_returns_empty(fake_home: Path):
    assert cache.load_cache() == {}


def test_save_then_load_roundtrip(fake_home: Path):
    data = {"k": {"score": 42, "bucket": "wrap"}}
    cache.save_cache(data)
    assert cache.cache_path().exists()
    assert cache.load_cache() == data


def test_load_corrupt_returns_empty(fake_home: Path):
    cache.cache_dir().mkdir(parents=True, exist_ok=True)
    cache.cache_path().write_text("{ not json", encoding="utf-8")
    assert cache.load_cache() == {}


def test_load_non_dict_returns_empty(fake_home: Path):
    cache.cache_dir().mkdir(parents=True, exist_ok=True)
    cache.cache_path().write_text("[1, 2, 3]", encoding="utf-8")
    assert cache.load_cache() == {}


def test_cache_key_stable_for_same_inputs(tmp_path: Path):
    md = _write_skill_md(tmp_path)
    mtime = md.stat().st_mtime
    k1 = cache.cache_key(str(md), mtime)
    k2 = cache.cache_key(str(md), mtime)
    assert k1 == k2
    assert isinstance(k1, str) and len(k1) == 64  # sha256 hexdigest


def test_cache_key_changes_with_content(tmp_path: Path):
    md = _write_skill_md(tmp_path, "aaa\n")
    mtime = md.stat().st_mtime
    k1 = cache.cache_key(str(md), mtime)

    md.write_text("bbb\n", encoding="utf-8")
    k2 = cache.cache_key(str(md), mtime)  # same declared mtime, new bytes
    assert k1 != k2


def test_cache_key_changes_with_mtime(tmp_path: Path):
    md = _write_skill_md(tmp_path)
    k1 = cache.cache_key(str(md), 1000.0)
    k2 = cache.cache_key(str(md), 2000.0)
    assert k1 != k2


def test_cache_key_accepts_precomputed_hash(tmp_path: Path):
    md = _write_skill_md(tmp_path)
    mtime = md.stat().st_mtime
    from_disk = cache.cache_key(str(md), mtime)
    # Recompute the hash the same way the function does and pass it in.
    import hashlib

    h = hashlib.sha256(md.read_bytes()).hexdigest()
    precomputed = cache.cache_key(str(md), mtime, content_hash=h)
    assert from_disk == precomputed


def test_save_creates_cache_dir(fake_home: Path):
    assert not cache.cache_dir().exists()
    cache.save_cache({"a": 1})
    assert cache.cache_dir().is_dir()
