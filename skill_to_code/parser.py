"""Parse a SKILL.md into a ParsedSkill. Stdlib only.

Frontmatter is a leading `---`-fenced block of simple `key: value` scalars
(no yaml dependency). The body is everything after the closing `---`. From the
body we extract markdown headings, fenced code blocks (contents + language tag),
and we scan the skill directory for sibling scripts.
"""

from __future__ import annotations

import os

from skill_to_code.types import DiscoveredSkill, ParsedSkill

_SCRIPT_EXTS = (".py", ".ps1", ".sh", ".js")


def _split_frontmatter(text: str) -> tuple[dict, str]:
    """Return (frontmatter_dict, body). No frontmatter -> ({}, text)."""
    lines = text.splitlines()
    # Frontmatter must be the very first line (allow a leading BOM only).
    first = lines[0].lstrip("﻿") if lines else ""
    if first.strip() != "---":
        return {}, text

    fm_lines: list[str] = []
    close_idx = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            close_idx = i
            break
        fm_lines.append(lines[i])

    if close_idx is None:
        # No closing fence: not valid frontmatter, treat whole thing as body.
        return {}, text

    body = "\n".join(lines[close_idx + 1 :])
    return _parse_frontmatter(fm_lines), body


def _parse_frontmatter(fm_lines: list[str]) -> dict:
    """Tiny line-based `key: value` scalar parser."""
    fm: dict = {}
    for raw in fm_lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        fm[key] = value
    return fm


def _extract_blocks(body: str) -> tuple[list[str], list[str], list[str]]:
    """Return (headings, code_blocks, code_langs) from the body.

    code_blocks[i] is the raw contents of a fence; code_langs[i] is its info
    string ("" when the fence has no language). Headings are only counted when
    outside a fence.
    """
    headings: list[str] = []
    code_blocks: list[str] = []
    code_langs: list[str] = []

    in_fence = False
    fence_marker = ""
    cur_lines: list[str] = []
    cur_lang = ""

    for line in body.splitlines():
        stripped = line.lstrip()
        if not in_fence:
            if stripped.startswith("```") or stripped.startswith("~~~"):
                in_fence = True
                fence_marker = stripped[:3]
                cur_lang = stripped[3:].strip()
                cur_lines = []
            elif stripped.startswith("#"):
                headings.append(stripped.lstrip("#").strip())
        else:
            if stripped.startswith(fence_marker) and stripped[3:].strip() == "":
                code_blocks.append("\n".join(cur_lines))
                code_langs.append(cur_lang)
                in_fence = False
                fence_marker = ""
                cur_lang = ""
                cur_lines = []
            else:
                cur_lines.append(line)

    # Unterminated fence: still record what we captured.
    if in_fence:
        code_blocks.append("\n".join(cur_lines))
        code_langs.append(cur_lang)

    return headings, code_blocks, code_langs


def _find_sibling_scripts(skill_dir: str) -> list[str]:
    scripts: list[str] = []
    try:
        entries = os.listdir(skill_dir)
    except OSError:
        return scripts
    for entry in sorted(entries):
        full = os.path.join(skill_dir, entry)
        if os.path.isfile(full) and entry.lower().endswith(_SCRIPT_EXTS):
            scripts.append(os.path.abspath(full))
    return scripts


def parse_skill(discovered: DiscoveredSkill) -> ParsedSkill:
    with open(discovered.path, "r", encoding="utf-8", errors="replace") as fh:
        text = fh.read()

    frontmatter, body = _split_frontmatter(text)
    headings, code_blocks, code_langs = _extract_blocks(body)
    sibling_scripts = _find_sibling_scripts(discovered.dir)

    return ParsedSkill(
        frontmatter=frontmatter,
        body=body,
        headings=headings,
        code_blocks=code_blocks,
        code_langs=code_langs,
        sibling_scripts=sibling_scripts,
    )
