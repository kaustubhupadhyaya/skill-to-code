"""Extract executable code segments from a ParsedSkill. Stdlib only.

LLM-free and verbatim: only fenced blocks whose language normalizes to an
executable target (bash / powershell / python) are lifted. Non-executable
langs (json, yaml, ...), untagged blocks, and unknown tags are recorded as
skip reasons and never emitted (the honesty guard lives downstream in
convert.py, which no-ops when this returns no segments).

See docs/conversion.md for the authoritative spec.
"""

from __future__ import annotations

import re
from typing import Optional

from skill_to_code.types import CodeSegment, ParsedSkill

# Alias -> (canonical target lang, extension). Canonical lang is one of
# bash / powershell / python; downstream codegen keys on it and must not re-alias.
_LANG_MAP: dict[str, tuple[str, str]] = {
    "bash": ("bash", ".sh"),
    "sh": ("bash", ".sh"),
    "shell": ("bash", ".sh"),
    "shell-session": ("bash", ".sh"),
    "shellsession": ("bash", ".sh"),
    "console": ("bash", ".sh"),
    "console-session": ("bash", ".sh"),
    "zsh": ("bash", ".sh"),
    "powershell": ("powershell", ".ps1"),
    "pwsh": ("powershell", ".ps1"),
    "ps1": ("powershell", ".ps1"),
    "ps": ("powershell", ".ps1"),
    "python": ("python", ".py"),
    "py": ("python", ".py"),
    "python3": ("python", ".py"),
}

# Explicitly non-executable: data / examples / prose. Distinguished from an
# unknown tag only so the skip reason is precise.
_NON_EXECUTABLE = {
    "json", "yaml", "yml", "toml", "text", "txt", "md", "markdown",
    "html", "xml", "diff", "output", "log",
}

# Shell-family raw fence tokens that trigger prompt stripping. Keyed on the
# ORIGINAL token (not the canonical lang) so we don't strip a bash block that
# happens to canonicalize the same as console — the spec ties stripping to the
# shell family, and every shell alias here maps to bash anyway.
_SHELL_TOKENS = {
    "bash", "sh", "shell", "shell-session", "shellsession",
    "console", "console-session", "zsh",
}

# Prompt prefixes on a shell line (after leading whitespace):
#   "$ ", "> ", "PS> ", "PS C:\...> " (PS drive-path prompt).
_PS_PATH_PROMPT = re.compile(r"^PS\b[^\n>]*>\s?")


def normalize_lang(tag: str) -> tuple[Optional[str], Optional[str]]:
    """Fence info string -> (canonical_lang, ext), or (None, None).

    Returns the canonical target language (bash / powershell / python) and its
    extension for executable tags; (None, None) for untagged, non-executable,
    or unknown tags.
    """
    token = tag.strip().split()[0].lower() if tag.strip() else ""
    if not token:
        return None, None
    mapped = _LANG_MAP.get(token)
    if mapped is None:
        return None, None
    return mapped


def _skip_reason(tag: str) -> str:
    """Standardized skip-reason wording for a non-extractable block."""
    token = tag.strip().split()[0].lower() if tag.strip() else ""
    if not token:
        return "untagged block"
    if token in _NON_EXECUTABLE:
        return f"non-executable language: {token}"
    return f"unknown tag: {token}"


def _strip_one_prompt(line: str) -> Optional[str]:
    """If ``line`` (already left-stripped of indentation) starts with a
    recognized prompt, return the command with the prompt removed; else None."""
    if line.startswith("$ ") or line == "$":
        return line[2:] if line.startswith("$ ") else ""
    if line.startswith("> ") or line == ">":
        return line[2:] if line.startswith("> ") else ""
    m = _PS_PATH_PROMPT.match(line)
    if m:
        return line[m.end():]
    return None


def strip_prompts(block_lines: list[str], lang: str) -> list[str]:
    """Apply the console prompt/output heuristic to one block's lines.

    Only shell-family blocks are stripped (``lang`` is the ORIGINAL fence
    token, not the canonical lang). Heuristic, per block:
      - If any line carries a recognized prompt, keep only the prompted lines
        with their prompts removed (the rest is command output, dropped).
      - If no line carries a prompt, it's a raw command block: keep verbatim.
    Python / PowerShell blocks are returned unchanged.
    """
    token = lang.strip().split()[0].lower() if lang.strip() else ""
    if token not in _SHELL_TOKENS:
        return list(block_lines)

    stripped: list[tuple[str, bool]] = []
    any_prompt = False
    for line in block_lines:
        cmd = _strip_one_prompt(line.lstrip())
        if cmd is not None:
            any_prompt = True
            stripped.append((cmd, True))
        else:
            stripped.append((line, False))

    if not any_prompt:
        return list(block_lines)
    return [text for text, was_prompt in stripped if was_prompt]


def extract_segments(parsed: ParsedSkill) -> list[CodeSegment]:
    """Extract executable code segments from a ParsedSkill, in document order.

    A segment is a maximal run of consecutive extractable blocks of the same
    canonical language. A different executable language ends the current
    segment; a non-extractable block is skipped and does not by itself split a
    same-language run. Segments empty after prompt stripping are dropped.
    Returns [] when nothing is extractable.
    """
    segments: list[CodeSegment] = []
    cur_lang: Optional[str] = None
    cur_ext: Optional[str] = None
    cur_lines: list[str] = []
    cur_first_idx = -1

    def flush() -> None:
        nonlocal cur_lang, cur_ext, cur_lines, cur_first_idx
        if cur_lang is not None and any(ln.strip() for ln in cur_lines):
            segments.append(
                CodeSegment(
                    lang=cur_lang,
                    ext=cur_ext,
                    lines=list(cur_lines),
                    order=len(segments),
                    source_block_index=cur_first_idx,
                )
            )
        cur_lang = None
        cur_ext = None
        cur_lines = []
        cur_first_idx = -1

    for idx, (block, tag) in enumerate(zip(parsed.code_blocks, parsed.code_langs)):
        canon, ext = normalize_lang(tag)
        if canon is None:
            # Non-extractable: skipped, does not split a same-language run.
            continue

        lines = strip_prompts(block.splitlines(), tag)
        if not any(ln.strip() for ln in lines):
            # Extractable tag but nothing survived (e.g. a console block that
            # was all output). Contribute nothing and don't disturb the run.
            continue

        if canon != cur_lang:
            flush()
            cur_lang, cur_ext, cur_first_idx = canon, ext, idx
            cur_lines = list(lines)
        else:
            cur_lines.extend(lines)

    flush()
    return segments
