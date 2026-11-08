"""On-disk cache for skill-to-code.

Cache lives at ``~/.claude/skill-to-code/cache.json``. Two concerns share it:

* Low-level primitives — ``cache_key`` / ``load_cache`` / ``save_cache`` — a
  digest of a SKILL.md's absolute path + mtime + sha256 of its bytes, so any
  edit (content or timestamp) invalidates.

* The **analysis session** — ``begin_session`` / ``get_cached_analysis`` /
  ``put_cached_analysis`` / ``prune_session`` / ``flush``. A process-wide cache
  of scored results, namespaced under ``"analysis"`` and keyed by SKILL.md
  absolute path -> ``{mtime, sha256, record}``. Discovery opens the session
  (honoring ``use_cache``) and prunes vanished skills; the analyzer serves
  unchanged skills straight from it. A warm hit skips re-scoring, and skips
  re-hashing on the fast (mtime-match) path. ``--no-cache`` (``enabled=False``)
  disables the session entirely: no read, no write, existing cache untouched.

The session is flushed to disk at interpreter exit (atexit) so a warm run works
across separate invocations; tests may call ``flush`` explicitly.

Stdlib only. Never writes outside the cache directory.
"""

from __future__ import annotations

import atexit
import hashlib
import json
from pathlib import Path
from typing import Iterable, Optional

from skill_to_code.types import AnalysisResult, DiscoveredSkill


def cache_dir(home: Optional[str] = None) -> Path:
    """Directory holding the cache file (``~/.claude/skill-to-code``)."""
    base = Path(home).expanduser() if home else Path.home()
    return base / ".claude" / "skill-to-code"


def cache_path(home: Optional[str] = None) -> Path:
    """Absolute path to the cache JSON file."""
    return cache_dir(home) / "cache.json"


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def cache_key(skill_md_path: str, mtime: float, content_hash: Optional[str] = None) -> str:
    """Stable key for a SKILL.md: digest of abspath + mtime + sha256(bytes).

    ``content_hash`` may be passed when already computed; otherwise it is read
    from disk. Returns a hex digest usable as a dict key.
    """
    p = Path(skill_md_path)
    if content_hash is None:
        content_hash = _sha256_file(p)
    # Normalize path for cross-invocation stability; case-fold for Windows.
    norm = str(p.resolve()).casefold()
    raw = f"{norm}\x00{mtime}\x00{content_hash}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def load_cache(home: Optional[str] = None) -> dict:
    """Load the cache dict. Returns an empty dict if missing or corrupt."""
    path = cache_path(home)
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError):
        # A corrupt cache must never break a scan; treat as empty.
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def save_cache(data: dict, home: Optional[str] = None) -> None:
    """Write the cache dict atomically. Creates the cache dir if needed."""
    d = cache_dir(home)
    d.mkdir(parents=True, exist_ok=True)
    tmp = cache_path(home).with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
    tmp.replace(cache_path(home))


# ---------------------------------------------------------------------------
# Analysis session: a process-wide, path-keyed cache of scored results.
# ---------------------------------------------------------------------------

_ANALYSIS_NS = "analysis"  # top-level namespace inside the cache file

_session: Optional[dict] = None       # {norm_path: entry}; None => caching off
_session_home: Optional[str] = None
_session_dirty: bool = False
_atexit_registered: bool = False


def _norm(path: str) -> str:
    return str(Path(path).resolve()).casefold()


def analysis_to_record(result: AnalysisResult) -> dict:
    """JSON-safe view of a scored result (score/bucket/reasons/signals)."""
    return {
        "score": result.score,
        "bucket": result.bucket,
        "reasons": list(result.reasons),
        "signals": dict(result.signals),
    }


def record_to_analysis(record: dict, discovered: DiscoveredSkill) -> AnalysisResult:
    """Rebuild an AnalysisResult from a cached record + its DiscoveredSkill."""
    return AnalysisResult(
        skill=discovered,
        score=record["score"],
        bucket=record["bucket"],
        reasons=list(record.get("reasons", [])),
        signals=dict(record.get("signals", {})),
    )


def begin_session(*, enabled: bool, home: Optional[str] = None) -> None:
    """Start (or disable) the process-wide analysis cache.

    ``enabled`` False (``--no-cache``) sets the session to None: the analyzer
    then neither reads nor writes the cache, and ``flush`` is a no-op, so any
    on-disk cache is left untouched.
    """
    global _session, _session_home, _session_dirty
    _session_home = home
    _session_dirty = False
    if not enabled:
        _session = None
        return
    ns = load_cache(home).get(_ANALYSIS_NS)
    _session = dict(ns) if isinstance(ns, dict) else {}
    _register_atexit()


def session_active() -> bool:
    return _session is not None


def _mark_dirty() -> None:
    global _session_dirty
    _session_dirty = True


def get_cached_analysis(discovered: DiscoveredSkill) -> Optional[AnalysisResult]:
    """Return a cached AnalysisResult for ``discovered`` if fresh, else None.

    Fresh = the stored entry's mtime equals the current mtime (fast path, no
    hashing), OR the mtime changed but the content sha256 still matches (a
    touched-but-unchanged file — re-scoring skipped, stored mtime refreshed).
    A missing entry, changed content, or unreadable file is a miss.
    """
    if _session is None:
        return None
    entry = _session.get(_norm(discovered.path))
    if not isinstance(entry, dict) or "record" not in entry:
        return None
    if entry.get("mtime") == discovered.mtime:
        return record_to_analysis(entry["record"], discovered)
    try:
        current = _sha256_file(Path(discovered.path))
    except OSError:
        return None
    if current == entry.get("sha256"):
        entry["mtime"] = discovered.mtime
        _mark_dirty()
        return record_to_analysis(entry["record"], discovered)
    return None


def put_cached_analysis(discovered: DiscoveredSkill, result: AnalysisResult) -> None:
    """Store a freshly scored result under the SKILL.md's absolute path."""
    if _session is None:
        return
    try:
        sha = _sha256_file(Path(discovered.path))
    except OSError:
        sha = ""
    _session[_norm(discovered.path)] = {
        "mtime": discovered.mtime,
        "sha256": sha,
        "record": analysis_to_record(result),
    }
    _mark_dirty()


def prune_session(live_paths: Iterable[str]) -> None:
    """Drop cached entries whose SKILL.md is no longer among ``live_paths``."""
    if _session is None:
        return
    live = {_norm(p) for p in live_paths}
    dead = [k for k in _session if k not in live]
    for k in dead:
        del _session[k]
    if dead:
        _mark_dirty()


def flush() -> None:
    """Persist the session to disk. No-op when caching is off or unchanged."""
    global _session_dirty
    if _session is None or not _session_dirty:
        return
    whole = load_cache(_session_home)
    whole[_ANALYSIS_NS] = _session
    save_cache(whole, _session_home)
    _session_dirty = False


def _register_atexit() -> None:
    global _atexit_registered
    if _atexit_registered:
        return
    atexit.register(_atexit_flush)
    _atexit_registered = True


def _atexit_flush() -> None:
    try:
        flush()
    except Exception:
        # A cache flush must never surface an error at interpreter shutdown.
        pass
