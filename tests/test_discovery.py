"""Tests for skill_to_code.discovery.

We build a fake home / project tree under tmp_path and drive discovery with the
``home`` and ``project_roots`` / ``includes`` overrides so nothing touches the
real machine.
"""

from __future__ import annotations

from pathlib import Path

from skill_to_code.discovery import discover_skills
from skill_to_code.types import Source


def _write_skill(
    skill_dir: Path,
    *,
    frontmatter: str = "",
    body: str = "body\n",
    siblings: dict[str, str] | None = None,
    skipconvert: bool = False,
) -> Path:
    skill_dir.mkdir(parents=True, exist_ok=True)
    parts = []
    if frontmatter:
        parts.append("---\n" + frontmatter.rstrip("\n") + "\n---\n")
    parts.append(body)
    (skill_dir / "SKILL.md").write_text("".join(parts), encoding="utf-8")
    for fname, content in (siblings or {}).items():
        (skill_dir / fname).write_text(content, encoding="utf-8")
    if skipconvert:
        (skill_dir / ".skipconvert").write_text("", encoding="utf-8")
    return skill_dir


def _by_name(skills):
    return {s.name: s for s in skills}


def test_finds_user_skill(tmp_path: Path):
    home = tmp_path / "home"
    _write_skill(home / ".claude" / "skills" / "commit", body="Run git commit\n")

    skills = discover_skills(home=str(home), project_roots=[])

    assert len(skills) == 1
    s = skills[0]
    assert s.name == "commit"
    assert s.source == Source.USER.value
    assert s.plugin is None
    assert s.path.casefold().endswith("skill.md")
    assert Path(s.dir).name == "commit"
    assert s.mtime > 0


def test_name_from_frontmatter_then_dirname(tmp_path: Path):
    home = tmp_path / "home"
    skills_root = home / ".claude" / "skills"
    _write_skill(skills_root / "dir-a", frontmatter="name: fancy-name\n")
    _write_skill(skills_root / "dir-b", body="no frontmatter here\n")

    skills = _by_name(discover_skills(home=str(home), project_roots=[]))

    assert "fancy-name" in skills  # from frontmatter
    assert "dir-b" in skills       # falls back to dir basename
    assert "dir-a" not in skills   # dir name overridden by frontmatter name


def test_skipconvert_drops_skill(tmp_path: Path):
    home = tmp_path / "home"
    skills_root = home / ".claude" / "skills"
    _write_skill(skills_root / "keep", body="keep me\n")
    _write_skill(skills_root / "drop", body="drop me\n", skipconvert=True)

    names = set(_by_name(discover_skills(home=str(home), project_roots=[])))

    assert "keep" in names
    assert "drop" not in names


def test_plugin_nesting_classified_plugin(tmp_path: Path):
    home = tmp_path / "home"
    # plugins/cache/<plugin>/<version>/skills/<skill>/SKILL.md
    nested = (
        home / ".claude" / "plugins" / "cache" / "superpowers"
        / "1.2.0" / "skills" / "brainstorming"
    )
    _write_skill(nested, body="Execute the brainstorm\n")

    skills = discover_skills(home=str(home), project_roots=[])

    assert len(skills) == 1
    s = skills[0]
    assert s.source == Source.PLUGIN.value
    assert s.plugin == "superpowers"
    assert s.name == "brainstorming"


def test_plugin_cache_with_marketplace_layout(tmp_path: Path):
    home = tmp_path / "home"
    # plugins/cache/<marketplace>/<plugin>/<version>/skills/<skill>/SKILL.md
    nested = (
        home / ".claude" / "plugins" / "cache" / "claude-plugins-official"
        / "superpowers" / "6.0.2" / "skills" / "brainstorming"
    )
    _write_skill(nested, body="brainstorm\n")

    skills = discover_skills(home=str(home), project_roots=[])

    assert len(skills) == 1
    s = skills[0]
    assert s.source == Source.PLUGIN.value
    # Plugin is the real plugin, not the marketplace or version segment.
    assert s.plugin == "superpowers"
    assert s.name == "brainstorming"


def test_plugin_flat_marketplace_layout(tmp_path: Path):
    home = tmp_path / "home"
    # plugins/marketplaces/<bundle>/<skill>/SKILL.md  (no skills/ segment)
    nested = home / ".claude" / "plugins" / "marketplaces" / "danielrosehill" / "audio-production"
    _write_skill(nested, body="audio\n")

    skills = discover_skills(home=str(home), project_roots=[])

    assert len(skills) == 1
    s = skills[0]
    assert s.source == Source.PLUGIN.value
    # Falls back to the bundle segment after `marketplaces`, never "marketplaces".
    assert s.plugin == "danielrosehill"


def test_plugin_direct_layout(tmp_path: Path):
    home = tmp_path / "home"
    # plugins/<plugin>/skills/<skill>/SKILL.md  (no cache/ segment)
    nested = home / ".claude" / "plugins" / "myplugin" / "skills" / "doit"
    _write_skill(nested, body="run it\n")

    skills = discover_skills(home=str(home), project_roots=[])

    assert len(skills) == 1
    assert skills[0].source == Source.PLUGIN.value
    assert skills[0].plugin == "myplugin"


def test_project_source_via_project_roots(tmp_path: Path):
    home = tmp_path / "home"  # keep home empty of skills
    proj_root = tmp_path / "workspace"
    _write_skill(proj_root / "repo1" / ".claude" / "skills" / "deploy", body="deploy\n")

    skills = discover_skills(home=str(home), project_roots=[str(proj_root)])

    assert len(skills) == 1
    s = skills[0]
    assert s.source == Source.PROJECT.value
    assert s.name == "deploy"
    assert s.plugin is None


def test_include_path_source(tmp_path: Path):
    home = tmp_path / "home"
    extra = tmp_path / "extra" / "myskill"
    _write_skill(extra, body="included skill\n")

    skills = discover_skills(
        home=str(home),
        project_roots=[],
        includes=[str(extra)],
    )

    assert len(skills) == 1
    assert skills[0].source == Source.INCLUDE.value
    assert skills[0].name == "myskill"


def test_include_root_finds_nested(tmp_path: Path):
    home = tmp_path / "home"
    root = tmp_path / "bundle"
    _write_skill(root / "a", body="a\n")
    _write_skill(root / "sub" / "b", body="b\n")

    skills = discover_skills(home=str(home), project_roots=[], includes=[str(root)])

    names = set(_by_name(skills))
    assert names == {"a", "b"}
    assert all(s.source == Source.INCLUDE.value for s in skills)


def test_dedup_by_path(tmp_path: Path):
    home = tmp_path / "home"
    skill = home / ".claude" / "skills" / "shared"
    _write_skill(skill, body="shared\n")

    # Same skill also passed via --include; it must appear once, user wins.
    skills = discover_skills(
        home=str(home),
        project_roots=[],
        includes=[str(skill)],
    )

    assert len(skills) == 1
    assert skills[0].source == Source.USER.value


def test_case_insensitive_skill_md_filename(tmp_path: Path):
    home = tmp_path / "home"
    d = home / ".claude" / "skills" / "weird"
    d.mkdir(parents=True)
    (d / "Skill.MD").write_text("mixed case filename\n", encoding="utf-8")

    skills = discover_skills(home=str(home), project_roots=[])

    assert len(skills) == 1
    assert skills[0].name == "weird"


def test_empty_when_nothing_present(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    assert discover_skills(home=str(home), project_roots=[]) == []


def test_extra_project_roots_merges_with_explicit_roots(tmp_path: Path):
    home = tmp_path / "home"
    base_root = tmp_path / "base"
    extra_root = tmp_path / "more"
    _write_skill(base_root / "p1" / ".claude" / "skills" / "base-skill", body="b\n")
    _write_skill(extra_root / "p2" / ".claude" / "skills" / "extra-skill", body="e\n")

    # When project_roots is given, it replaces the real defaults, so this test
    # stays isolated from ~/github and ~/code. We pass the extra root inside
    # project_roots to prove both roots are scanned together.
    skills = discover_skills(
        home=str(home),
        project_roots=[str(base_root), str(extra_root)],
    )
    names = set(_by_name(skills))
    assert "base-skill" in names
    assert "extra-skill" in names
    assert all(s.source == Source.PROJECT.value for s in skills)


def test_converted_staging_never_discovered(tmp_path: Path):
    home = tmp_path / "home"
    skill = home / ".claude" / "skills" / "real"
    _write_skill(skill, body="the real skill\n")
    _write_skill(skill / ".converted", body="staged copy\n")
    _write_skill(skill / ".converted" / ".backup", body="backup copy\n")

    skills = discover_skills(home=str(home), project_roots=[])

    assert len(skills) == 1
    assert skills[0].name == "real"
    assert ".converted" not in Path(skills[0].path).parts


def test_root_with_only_staged_skill_yields_zero(tmp_path: Path):
    home = tmp_path / "home"
    bare = tmp_path / "bare"
    bare.mkdir()
    _write_skill(bare / ".converted", body="staged only\n")

    skills = discover_skills(home=str(home), project_roots=[], includes=[str(bare)])

    assert skills == []


def test_include_pointing_inside_converted_yields_zero(tmp_path: Path):
    home = tmp_path / "home"
    staged = tmp_path / "someskill" / ".converted"
    _write_skill(staged, body="staged copy\n")
    _write_skill(staged / ".backup", body="backup copy\n")

    skills = discover_skills(
        home=str(home),
        project_roots=[],
        includes=[str(staged)],
    )

    assert skills == []


def test_extra_project_roots_scanned_alongside_defaults(tmp_path: Path):
    home = tmp_path / "home"
    extra_root = tmp_path / "more"
    _write_skill(extra_root / "p" / ".claude" / "skills" / "extra-only", body="x\n")

    # project_roots=None keeps the real defaults; extra_project_roots is
    # appended. The injected skill must still surface regardless of whatever
    # real skills the default roots contain.
    skills = discover_skills(
        home=str(home),
        extra_project_roots=[str(extra_root)],
    )
    assert "extra-only" in set(_by_name(skills))
