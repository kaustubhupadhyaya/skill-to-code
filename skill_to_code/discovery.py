"""Discovery: walk the machine for Claude Code skills.

A "skill" is any directory containing a ``SKILL.md`` (case-insensitive). This
module finds them across four sources and returns ``DiscoveredSkill`` records:

    user     ~/.claude/skills
    plugin   ~/.claude/plugins   (nested; plugin name derived from the path)
    project  <proj>/.claude/skills   for projects under the project roots
    include  paths passed via --include (a skill dir or a root)

Discovery is read-only. Root/home overrides are exposed as parameters so tests
never depend on the real home directory.

Stdlib only.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Iterable, Optional

from skill_to_code import cache
from skill_to_code.types import DiscoveredSkill, Source

# Default project roots to search for <proj>/.claude/skills.
_DEFAULT_PROJECT_ROOTS = ("~/github", "~/code")

# Pulls `name: value` out of the leading YAML frontmatter block only.
_NAME_RE = re.compile(r"^[ \t]*name[ \t]*:[ \t]*(.+?)[ \t]*$", re.MULTILINE)


def _norm(path: Path) -> str:
    """Absolute, resolved, case-folded string key for dedup (Windows-safe)."""
    return str(path.resolve()).casefold()


def _is_skill_md(path: Path) -> bool:
    return path.is_file() and path.name.casefold() == "skill.md"


# Conversion staging dir (and its .backup) must never be discovered as skills,
# or post-apply runs find phantom copies and convert the tool's own output.
_STAGING_DIR = ".converted"


def _in_staging(path: Path) -> bool:
    return any(part.casefold() == _STAGING_DIR for part in path.parts)


def _iter_skill_mds(root: Path) -> Iterable[Path]:
    """Yield every SKILL.md at or under ``root`` (case-insensitive filename).

    Never descends into ``.converted`` staging dirs, and yields nothing when
    ``root`` itself sits inside one, so no source can surface staged copies.
    """
    if not root.exists() or _in_staging(root):
        return
    if _is_skill_md(root):
        yield root
        return
    if not root.is_dir():
        return
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d.casefold() != _STAGING_DIR]
        for fname in filenames:
            child = Path(dirpath) / fname
            if _is_skill_md(child):
                yield child


def _read_frontmatter_name(skill_md: Path) -> Optional[str]:
    """Return the frontmatter ``name:`` value, or None if absent/unreadable.

    Reads only the leading ``---`` fenced block; does not parse the full body.
    Kept independent of the parser module so discovery has no cross-module dep.
    """
    try:
        text = skill_md.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    if not text.startswith("---"):
        return None
    # Locate the closing fence of the frontmatter block.
    lines = text.splitlines()
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        return None
    block = "\n".join(lines[1:end])
    m = _NAME_RE.search(block)
    if not m:
        return None
    value = m.group(1).strip().strip('"').strip("'").strip()
    return value or None


_VERSION_RE = re.compile(r"^v?\d+(\.\d+)*$")


def _plugin_name_from_path(skill_md: Path) -> Optional[str]:
    """Derive the owning plugin's name from a SKILL.md path under ~/.claude/plugins.

    Anchoring on the bundle root (the segment after ``cache/`` or
    ``marketplaces/``) is far more stable than counting back from ``skills/``,
    because real plugins nest their skills at arbitrary depth (under version
    dirs, hash dirs, ``.agents/``, ``.claude/``, vendored source trees, etc.).

    Rule, by anchor:
      * ``plugins/cache/<A>/<B>/…/SKILL.md`` — the cache stores plugins as
        ``<marketplace>/<plugin>/<ver>/skills/…``. So ``<A>`` is the marketplace
        and ``<B>`` is the plugin: return ``<B>`` when it exists, else ``<A>``.
      * ``plugins/marketplaces/<bundle>/…/SKILL.md`` — flat bundles keyed by the
        bundle name; return ``<bundle>``.
      * ``plugins/<plugin>/…/SKILL.md`` (no cache/marketplaces layer) — return
        ``<plugin>``.

    Never returns the literal ``cache``/``marketplaces``/``plugins`` roots.
    """
    parts = list(skill_md.parts)
    lowered = [p.casefold() for p in parts]

    if "cache" in lowered:
        idx = lowered.index("cache")
        # Two cache layouts occur: <marketplace>/<plugin>/<ver>/skills (real
        # machine) and <plugin>/<ver>/skills (contract's stated shape). In the
        # first, idx+2 is the plugin; in the second, idx+2 is a <version> and
        # idx+1 is the plugin. Prefer idx+2 unless it is a version or `skills`.
        if (
            idx + 2 < len(parts)
            and lowered[idx + 2] != "skills"
            and not _VERSION_RE.match(parts[idx + 2])
        ):
            return parts[idx + 2]
        if idx + 1 < len(parts):
            return parts[idx + 1]

    if "marketplaces" in lowered:
        idx = lowered.index("marketplaces")
        if idx + 1 < len(parts):
            return parts[idx + 1]

    if "plugins" in lowered:
        idx = lowered.index("plugins")
        if idx + 1 < len(parts) and lowered[idx + 1] not in ("marketplaces", "cache"):
            return parts[idx + 1]

    return None


def _has_skipconvert(skill_dir: Path) -> bool:
    return (skill_dir / ".skipconvert").exists()


def _make_skill(
    skill_md: Path,
    source: str,
    plugin: Optional[str],
) -> Optional[DiscoveredSkill]:
    """Build a DiscoveredSkill, or None if the skill dir opts out (.skipconvert)."""
    skill_dir = skill_md.parent
    if _has_skipconvert(skill_dir):
        return None
    name = _read_frontmatter_name(skill_md) or skill_dir.name
    try:
        mtime = skill_md.stat().st_mtime
    except OSError:
        mtime = 0.0
    return DiscoveredSkill(
        path=str(skill_md.resolve()),
        dir=str(skill_dir.resolve()),
        name=name,
        source=source,
        plugin=plugin,
        mtime=mtime,
    )


def _find_project_skill_roots(project_root: Path) -> Iterable[Path]:
    """Yield every ``<proj>/.claude/skills`` dir under a project root.

    A project is any directory containing ``.claude/skills``. We search the
    root itself and one level of children as projects, plus any deeper
    ``.claude/skills`` dirs, so nested repos are still picked up.
    """
    if not project_root.exists() or not project_root.is_dir():
        return
    seen: set[str] = set()
    # The root may itself be a project.
    candidates = [project_root / ".claude" / "skills"]
    for child in project_root.iterdir():
        if child.is_dir():
            candidates.append(child / ".claude" / "skills")
    # Deeper nesting: any .claude/skills anywhere below the root.
    for claude_dir in project_root.rglob(".claude"):
        candidates.append(claude_dir / "skills")
    for c in candidates:
        if c.exists() and c.is_dir():
            key = _norm(c)
            if key not in seen:
                seen.add(key)
                yield c


def discover_skills(
    includes: Optional[list[str]] = None,
    extra_project_roots: Optional[list[str]] = None,
    use_cache: bool = True,
    *,
    home: Optional[str] = None,
    project_roots: Optional[list[str]] = None,
) -> list[DiscoveredSkill]:
    """Discover all skills across user / plugin / project / include sources.

    Args:
        includes: extra paths (skill dirs or roots) to scan as source=include.
        extra_project_roots: additional project roots appended to the defaults.
        use_cache: when True (default) open the analysis cache session so the
            analyzer serves unchanged skills without re-scoring; vanished skills
            are pruned from it here. When False (``--no-cache``) the session is
            disabled: no cache read or write, and the on-disk cache is left as
            is. The filesystem walk itself always runs, so adds/removes/edits
            are reflected and a full rescan stays correct.
        home: override for the home dir (tests). Defaults to ``Path.home()``.
        project_roots: replace the default project roots entirely (tests).

    Returns a list of DiscoveredSkill, deduped by normalized absolute path.
    Precedence on duplicate paths: user > plugin > project > include (first
    source to claim a path wins).
    """
    home_dir = Path(home).expanduser() if home else Path.home()
    cache.begin_session(enabled=use_cache, home=home)

    results: list[DiscoveredSkill] = []
    seen: set[str] = set()

    def _add(skill_md: Path, source: str, plugin: Optional[str]) -> None:
        key = _norm(skill_md)
        if key in seen:
            return
        skill = _make_skill(skill_md, source, plugin)
        if skill is None:
            return
        seen.add(key)
        results.append(skill)

    # 1. user: ~/.claude/skills
    user_root = home_dir / ".claude" / "skills"
    for md in _iter_skill_mds(user_root):
        _add(md, Source.USER.value, None)

    # 2. plugin: ~/.claude/plugins (nested)
    plugins_root = home_dir / ".claude" / "plugins"
    for md in _iter_skill_mds(plugins_root):
        _add(md, Source.PLUGIN.value, _plugin_name_from_path(md))

    # 3. project: <proj>/.claude/skills under each project root
    if project_roots is not None:
        roots = list(project_roots)
    else:
        roots = list(_DEFAULT_PROJECT_ROOTS)
        if extra_project_roots:
            roots.extend(extra_project_roots)
    for root_str in roots:
        root = Path(root_str).expanduser()
        for skills_root in _find_project_skill_roots(root):
            for md in _iter_skill_mds(skills_root):
                _add(md, Source.PROJECT.value, None)

    # 4. include: explicit paths (skill dir or root)
    for inc in includes or []:
        inc_path = Path(inc).expanduser()
        for md in _iter_skill_mds(inc_path):
            _add(md, Source.INCLUDE.value, None)

    # Reflect removals: drop cache entries for skills no longer on disk.
    cache.prune_session(s.path for s in results)
    return results
