"""Shared pytest fixtures for skill_to_code tests.

Keep this minimal — it is scaffolding for the discovery / parser / analyzer /
report test authors. Fixtures here should not encode module-specific behavior.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PKG_RUBRIC = REPO_ROOT / "skill_to_code" / "rubric.toml"


@pytest.fixture
def rubric() -> dict:
    """The shipped rubric.toml, parsed."""
    with PKG_RUBRIC.open("rb") as fh:
        return tomllib.load(fh)


@pytest.fixture
def make_skill(tmp_path: Path):
    """Factory: write a SKILL.md (optionally with sibling files) and return its dir.

        skill_dir = make_skill(
            "commit",
            frontmatter="name: commit\\n",
            body="# Commit\\n\\nRun git commit.\\n",
            siblings={"convert.py": "print('hi')"},
        )

    Returns the absolute Path to the skill directory containing SKILL.md.
    """

    def _make(
        name: str,
        *,
        frontmatter: str = "",
        body: str = "",
        siblings: dict[str, str] | None = None,
        skipconvert: bool = False,
    ) -> Path:
        skill_dir = tmp_path / name
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

    return _make
