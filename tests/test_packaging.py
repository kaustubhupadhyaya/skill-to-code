"""Packaging (M5) guards: the shipped wrapper skill, pyproject, and LICENSE.

These lock the ship-as-a-skill deliverables so a future edit that breaks the
frontmatter, the console script, or the license is caught by CI. Stdlib only.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL_MD = REPO_ROOT / "SKILL.md"
PYPROJECT = REPO_ROOT / "pyproject.toml"
LICENSE = REPO_ROOT / "LICENSE"
USAGE = REPO_ROOT / "docs" / "USAGE.md"


def _frontmatter(text: str) -> dict[str, str]:
    """Parse the leading ``---`` fenced block into flat ``key: value`` pairs."""
    assert text.startswith("---"), "SKILL.md must open with a YAML frontmatter fence"
    lines = text.splitlines()
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    assert end is not None, "frontmatter block is not closed with ---"
    fm: dict[str, str] = {}
    for line in lines[1:end]:
        if ":" in line and not line.startswith((" ", "\t")):
            key, _, value = line.partition(":")
            fm[key.strip()] = value.strip().strip('"').strip("'")
    return fm


def _skill_body(text: str) -> str:
    lines = text.splitlines()
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    return "\n".join(lines[end + 1 :]) if end is not None else text


def _pyproject() -> dict:
    with PYPROJECT.open("rb") as fh:
        return tomllib.load(fh)


def test_skill_md_exists_with_name_and_description():
    assert SKILL_MD.is_file(), "SKILL.md wrapper skill must exist at the repo root"
    fm = _frontmatter(SKILL_MD.read_text(encoding="utf-8"))
    assert fm.get("name") == "skill-to-code"
    assert fm.get("description", "").strip(), "frontmatter needs a non-empty description"


def test_skill_md_body_references_real_subcommands():
    body = _skill_body(SKILL_MD.read_text(encoding="utf-8"))
    assert "scan" in body, "wrapper body must reference the scan subcommand"
    assert "convert" in body, "wrapper body must reference the convert subcommand"
    assert "skill_to_code" in body, "wrapper body must show how to invoke the CLI"


def test_pyproject_parses_and_declares_console_script():
    data = _pyproject()
    scripts = data["project"]["scripts"]
    assert scripts.get("skill-to-code") == "skill_to_code.cli:main"


def test_pyproject_declares_package_and_rubric_data():
    data = _pyproject()
    assert "skill_to_code" in data["tool"]["setuptools"]["packages"]
    pkg_data = data["tool"]["setuptools"]["package-data"]["skill_to_code"]
    assert "rubric.toml" in pkg_data
    assert data["project"]["requires-python"] == ">=3.11"
    assert data["project"]["optional-dependencies"]["dev"] == ["pytest"]


def test_license_exists_and_is_nonempty():
    assert LICENSE.is_file(), "LICENSE must exist at the repo root"
    text = LICENSE.read_text(encoding="utf-8")
    assert text.strip(), "LICENSE must not be empty"
    assert "MIT License" in text
    assert "Kaustubh Upadhyaya" in text


def test_usage_doc_exists_and_covers_scan_convert_verify():
    assert USAGE.is_file(), "docs/USAGE.md must exist"
    text = USAGE.read_text(encoding="utf-8")
    for token in ("pip install -e .", "scan", "convert", "--verify-run"):
        assert token in text, f"USAGE.md should document {token!r}"
