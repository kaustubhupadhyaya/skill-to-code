"""Parser tests: frontmatter split, headings, code blocks, sibling scripts."""

from __future__ import annotations

from pathlib import Path

from skill_to_code.parser import parse_skill
from skill_to_code.types import DiscoveredSkill

SAMPLE = """\
---
name: commit
description: "Commit and push changes"
---
# Commit

Run the commit.

## Steps

```bash
git add -A
git commit -m "wip"
```

Some prose.

```
plain fenced text
```
"""


def _discover(skill_dir: Path) -> DiscoveredSkill:
    md = skill_dir / "SKILL.md"
    return DiscoveredSkill(
        path=str(md),
        dir=str(skill_dir),
        name="commit",
        source="user",
        plugin=None,
        mtime=md.stat().st_mtime,
    )


def test_frontmatter_parsed(make_skill):
    skill_dir = make_skill(
        "commit",
        frontmatter='name: commit\ndescription: "Commit and push changes"',
        body="# Commit\n\nRun the commit.\n",
    )
    parsed = parse_skill(_discover(skill_dir))
    assert parsed.frontmatter["name"] == "commit"
    # Quotes stripped from the scalar value.
    assert parsed.frontmatter["description"] == "Commit and push changes"


def test_no_frontmatter_gives_empty_dict(make_skill):
    skill_dir = make_skill("x", body="# Title\n\nBody only, no frontmatter.\n")
    parsed = parse_skill(_discover(skill_dir))
    assert parsed.frontmatter == {}
    assert "Body only" in parsed.body


def test_body_excludes_frontmatter(tmp_path):
    skill_dir = tmp_path / "commit"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(SAMPLE, encoding="utf-8")
    parsed = parse_skill(_discover(skill_dir))
    assert "name: commit" not in parsed.body
    assert parsed.body.lstrip().startswith("# Commit")


def test_headings_extracted(tmp_path):
    skill_dir = tmp_path / "commit"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(SAMPLE, encoding="utf-8")
    parsed = parse_skill(_discover(skill_dir))
    assert parsed.headings == ["Commit", "Steps"]


def test_code_blocks_and_langs(tmp_path):
    skill_dir = tmp_path / "commit"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(SAMPLE, encoding="utf-8")
    parsed = parse_skill(_discover(skill_dir))
    assert len(parsed.code_blocks) == 2
    assert parsed.code_langs == ["bash", ""]
    assert 'git commit -m "wip"' in parsed.code_blocks[0]
    assert parsed.code_blocks[1] == "plain fenced text"


def test_heading_inside_fence_not_counted(make_skill):
    body = "# Real\n\n```\n# not a heading\ncode\n```\n"
    skill_dir = make_skill("x", body=body)
    parsed = parse_skill(_discover(skill_dir))
    assert parsed.headings == ["Real"]
    assert "# not a heading" in parsed.code_blocks[0]


def test_sibling_scripts_detected(make_skill):
    skill_dir = make_skill(
        "commit",
        body="# Commit\n",
        siblings={"convert.py": "print('hi')", "notes.txt": "ignore me"},
    )
    parsed = parse_skill(_discover(skill_dir))
    names = {Path(p).name for p in parsed.sibling_scripts}
    assert names == {"convert.py"}
    assert all(Path(p).is_absolute() for p in parsed.sibling_scripts)
