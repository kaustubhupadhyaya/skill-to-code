"""Codegen + staging for M2 conversion. Stdlib only.

Turns a scored, parsed skill into staged scripts and a rewritten SKILL.md,
then (optionally) applies them. Conversion is LLM-free and verbatim: it only
lifts fenced code the extractor already found. This module owns:

    plan_conversion  -> ConversionPlan   (honesty guard, dominant lang, names)
    render_scripts   -> {filename: text}  (REPLACE / WRAP codegen)
    rewrite_skill_md -> str               (stub / prose-with-refs)
    stage            -> ConversionResult  (writes .converted/ + plan.json)
    is_unchanged     -> bool              (idempotency via stored sha256)
    apply            -> ConversionResult  (backup-then-overwrite, lint gate)

Lint is NOT run here; the caller injects lint_ok / lint_output before apply.
Nothing is written outside <skill_dir>/.converted/ unless applying.

See docs/conversion.md for the authoritative spec.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil

from skill_to_code import extract
from skill_to_code.types import (
    Bucket,
    CodeSegment,
    ConversionPlan,
    ConversionResult,
    DiscoveredSkill,
    ParsedSkill,
    conversion_plan_to_dict,
)

_CONVERTED_DIR = ".converted"
_BACKUP_DIR = ".backup"
_PLAN_JSON = "plan.json"

# Language header lines emitted at the top of a generated script.
_HEADERS: dict[str, list[str]] = {
    "bash": ["#!/usr/bin/env bash", "set -euo pipefail"],
    "python": ["#!/usr/bin/env python3"],
    "powershell": [],
}

_SANITIZE_RE = re.compile(r"[^a-z0-9._-]+")


# ---------------------------------------------------------------------------
# Naming + skip-reason helpers
# ---------------------------------------------------------------------------


def sanitize_name(raw: str) -> str:
    """skill dir basename -> a safe script base name.

    lowercase, non-``[a-z0-9._-]`` runs collapsed to ``-``, leading/trailing
    ``-``/``.`` trimmed, empty result falls back to ``skill``.
    """
    lowered = raw.strip().lower()
    collapsed = _SANITIZE_RE.sub("-", lowered)
    trimmed = collapsed.strip("-.")
    return trimmed or "skill"


def _skip_reason(tag: str) -> str:
    """Standardized skip-reason wording for a non-extractable block.

    Mirrors extract._skip_reason (kept local so the audit wording is owned by
    the module that records it in the plan).
    """
    token = tag.strip().split()[0].lower() if tag.strip() else ""
    if not token:
        return "untagged block"
    _non_exec = {
        "json", "yaml", "yml", "toml", "text", "txt", "md", "markdown",
        "html", "xml", "diff", "output", "log",
    }
    if token in _non_exec:
        return f"non-executable language: {token}"
    return f"unknown tag: {token}"


def _non_extractable_skip_reasons(parsed: ParsedSkill) -> list[str]:
    """One skip reason per fenced block the extractor won't lift."""
    reasons: list[str] = []
    for tag in parsed.code_langs:
        canon, _ = extract.normalize_lang(tag)
        if canon is None:
            reasons.append(_skip_reason(tag))
    return reasons


def _dominant_lang(segments: list[CodeSegment]) -> str | None:
    """Executable lang with the most total extracted lines. Ties -> first
    appearance in document order."""
    if not segments:
        return None
    totals: dict[str, int] = {}
    order: list[str] = []
    for seg in segments:
        if seg.lang not in totals:
            totals[seg.lang] = 0
            order.append(seg.lang)
        totals[seg.lang] += len(seg.lines)
    return max(order, key=lambda lang: totals[lang])


def _ext_for(segments: list[CodeSegment], lang: str) -> str:
    for seg in segments:
        if seg.lang == lang:
            return seg.ext
    return ""


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------


def plan_conversion(
    discovered: DiscoveredSkill,
    parsed: ParsedSkill,
    analysis,
    *,
    bucket: str | None = None,
) -> ConversionPlan:
    """Decide what conversion will do to one skill (no writes).

    ``bucket`` overrides the analyzer's verdict when the CLI forces a strategy;
    otherwise ``analysis.bucket`` is used. The honesty guard makes the plan a
    no-op (``eligible=False``) when there is no extractable executable code.
    """
    chosen_bucket = bucket or analysis.bucket
    script_name = sanitize_name(os.path.basename(discovered.dir))

    segments = extract.extract_segments(parsed)
    skipped = _non_extractable_skip_reasons(parsed)

    if not segments:
        return ConversionPlan(
            skill=discovered,
            bucket=chosen_bucket,
            segments=[],
            dominant_lang=None,
            script_name=script_name,
            skipped_blocks=skipped,
            eligible=False,
            reason="no deterministically-extractable executable code",
        )

    dominant = _dominant_lang(segments)

    if chosen_bucket == Bucket.REPLACE.value:
        # Non-dominant-language segments are not emitted by REPLACE codegen.
        for seg in segments:
            if seg.lang != dominant:
                skipped.append(f"non-dominant language: {seg.lang}")
        reason = f"replace: dominant language {dominant}"
    else:
        reason = f"wrap: {len(segments)} segment(s)"

    return ConversionPlan(
        skill=discovered,
        bucket=chosen_bucket,
        segments=segments,
        dominant_lang=dominant,
        script_name=script_name,
        skipped_blocks=skipped,
        eligible=True,
        reason=reason,
    )


# ---------------------------------------------------------------------------
# Codegen
# ---------------------------------------------------------------------------


def _script_body(header: list[str], sections: list[list[str]]) -> str:
    """Assemble a script: header, blank line, sections separated by one blank
    line. Trailing newline. ``sections`` is a list of line-lists."""
    parts: list[str] = list(header)
    if header and sections:
        parts.append("")
    for i, section in enumerate(sections):
        if i:
            parts.append("")
        parts.extend(section)
    return "\n".join(parts).rstrip("\n") + "\n"


def render_scripts(plan: ConversionPlan) -> dict[str, str]:
    """ConversionPlan -> {filename: file_text}. Empty for an ineligible plan.

    REPLACE: one script of the dominant language, all its segments concatenated.
    WRAP: one helper per language, each segment a ``# --- step N ---`` section.
    """
    if not plan.eligible:
        return {}

    if plan.bucket == Bucket.REPLACE.value:
        lang = plan.dominant_lang
        ext = _ext_for(plan.segments, lang)
        sections = [list(seg.lines) for seg in plan.segments if seg.lang == lang]
        text = _script_body(_HEADERS.get(lang, []), sections)
        return {f"{plan.script_name}{ext}": text}

    # WRAP: group segments by language, preserving each segment's step number.
    by_lang: dict[str, tuple[str, list[list[str]]]] = {}
    for seg in plan.segments:
        ext, sections = by_lang.setdefault(seg.lang, (seg.ext, []))
        section = [f"# --- step {seg.order + 1} ---"]
        section.extend(seg.lines)
        sections.append(section)
    scripts: dict[str, str] = {}
    for lang, (ext, sections) in by_lang.items():
        scripts[f"{plan.script_name}{ext}"] = _script_body(
            _HEADERS.get(lang, []), sections
        )
    return scripts


# ---------------------------------------------------------------------------
# SKILL.md rewriting
# ---------------------------------------------------------------------------


def _split_raw_frontmatter(text: str) -> tuple[str, str]:
    """Return (raw_frontmatter_block, body) preserving frontmatter byte-for-byte.

    The frontmatter block includes both ``---`` fences and the trailing newline
    after the closing fence. No frontmatter -> ("", text).
    """
    lines = text.splitlines(keepends=True)
    if not lines:
        return "", text
    first = lines[0].lstrip("﻿")
    if first.strip() != "---":
        return "", text
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            fm = "".join(lines[: i + 1])
            body = "".join(lines[i + 1 :])
            return fm, body
    return "", text


def _fence_spans(text: str) -> list[tuple[int, int]]:
    """Line-index spans (start, end_exclusive) of each fenced block's OUTER
    fences in ``text``, in document order. Aligned with ParsedSkill.code_blocks.

    Mirrors parser._extract_blocks fence detection so block index i here matches
    parsed.code_blocks[i].
    """
    lines = text.splitlines()
    spans: list[tuple[int, int]] = []
    in_fence = False
    marker = ""
    start = -1
    for i, line in enumerate(lines):
        stripped = line.lstrip()
        if not in_fence:
            if stripped.startswith("```") or stripped.startswith("~~~"):
                in_fence = True
                marker = stripped[:3]
                start = i
        else:
            if stripped.startswith(marker) and stripped[3:].strip() == "":
                spans.append((start, i + 1))
                in_fence = False
    if in_fence:
        spans.append((start, len(lines)))
    return spans


def rewrite_skill_md(plan: ConversionPlan, parsed: ParsedSkill, original_text: str) -> str:
    """Produce the rewritten SKILL.md text for a plan.

    REPLACE -> a stub: original frontmatter verbatim + a one-line wrapper note.
    WRAP    -> the original prose with each extracted code segment replaced in
    place by a ``Run step N of `.converted/<script>`.`` reference line.
    """
    if plan.bucket == Bucket.REPLACE.value:
        fm, _ = _split_raw_frontmatter(original_text)
        ext = _ext_for(plan.segments, plan.dominant_lang)
        script = f"{plan.script_name}{ext}"
        note = f"This skill runs `.converted/{script}`.\n"
        return fm + note if fm else note

    # WRAP: replace each segment's block span with a reference line.
    #
    # The parser strips frontmatter BEFORE indexing code blocks, so segment
    # indices and parsed.code_langs are body-relative. Fence spans must be
    # computed in that same coordinate space — a ``` or ~~~ marker inside the
    # frontmatter would otherwise shift every span and silently corrupt the
    # output (eating the closing --- and the prose). Split first, rewrite the
    # body, reattach the frontmatter verbatim.
    fm, body = _split_raw_frontmatter(original_text)
    lines = body.splitlines()
    spans = _fence_spans(body)

    # For each segment, find the block-index range it occupies. The extractor
    # merges a same-language run even ACROSS intervening non-extractable blocks
    # (those don't split a run); a different executable language ends the run.
    # So we walk forward from source_block_index collecting the LAST same-lang
    # block index, and replace the whole line span [first_block_start,
    # last_block_end) with one reference line — consuming any interleaved
    # non-extractable block that fell inside the run.
    replaced_spans: list[tuple[int, int, str]] = []  # (start_line, end_line_excl, ref_text)
    for seg in plan.segments:
        ref = f"Run step {seg.order + 1} of `.converted/{plan.script_name}{seg.ext}`."
        idx = seg.source_block_index
        last = idx
        j = idx
        while j < len(parsed.code_langs):
            canon, _ = extract.normalize_lang(parsed.code_langs[j])
            if canon is None:
                j += 1
                continue
            if canon != seg.lang:
                break
            last = j
            j += 1
        replaced_spans.append((spans[idx][0], spans[last][1], ref))

    replaced_spans.sort()
    out: list[str] = []
    cursor = 0
    for start, end, ref in replaced_spans:
        out.extend(lines[cursor:start])
        out.append(ref)
        cursor = end
    out.extend(lines[cursor:])

    trailing = "\n" if original_text.endswith("\n") else ""
    return fm + "\n".join(out) + trailing


# ---------------------------------------------------------------------------
# Staging + idempotency + apply
# ---------------------------------------------------------------------------


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_bytes(path: str) -> bytes:
    with open(path, "rb") as fh:
        return fh.read()


def is_unchanged(skill_md_path: str) -> bool:
    """True when .converted/plan.json exists and its stored source_sha256 equals
    the sha256 of the current SKILL.md bytes."""
    skill_dir = os.path.dirname(os.path.abspath(skill_md_path))
    plan_path = os.path.join(skill_dir, _CONVERTED_DIR, _PLAN_JSON)
    if not os.path.isfile(plan_path):
        return False
    try:
        with open(plan_path, "r", encoding="utf-8") as fh:
            stored = json.load(fh)
    except (OSError, ValueError):
        return False
    current = _sha256_bytes(_read_bytes(skill_md_path))
    return stored.get("source_sha256") == current


def stage(
    plan: ConversionPlan,
    scripts: dict[str, str],
    rewritten: str,
    skill_md_path: str,
    *,
    lint_ok: bool = True,
    lint_output: str = "",
) -> ConversionResult:
    """Write the .converted/ tree for an eligible plan and return a result.

    Writes: each script, the rewritten SKILL.md, plan.json (with source sha256).
    Does not create the backup and does not touch the real SKILL.md — that is
    apply()'s job. An ineligible plan is a no-op (status "noop", nothing written).
    """
    skill_md_path = os.path.abspath(skill_md_path)
    skill_dir = os.path.dirname(skill_md_path)

    if not plan.eligible:
        return ConversionResult(
            plan=plan,
            staged_dir="",
            lint_ok=lint_ok,
            lint_output=lint_output,
            status="noop",
        )

    converted_dir = os.path.join(skill_dir, _CONVERTED_DIR)
    os.makedirs(converted_dir, exist_ok=True)

    script_paths: list[str] = []
    for filename, text in scripts.items():
        dest = os.path.join(converted_dir, filename)
        with open(dest, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        script_paths.append(dest)

    rewritten_path = os.path.join(converted_dir, "SKILL.md")
    with open(rewritten_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(rewritten)

    plan_record = conversion_plan_to_dict(plan)
    plan_record["source_sha256"] = _sha256_bytes(_read_bytes(skill_md_path))
    plan_path = os.path.join(converted_dir, _PLAN_JSON)
    with open(plan_path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(plan_record, fh, indent=2, sort_keys=False)
        fh.write("\n")

    return ConversionResult(
        plan=plan,
        staged_dir=converted_dir,
        script_paths=sorted(script_paths),
        rewritten_skill_md=rewritten_path,
        lint_ok=lint_ok,
        lint_output=lint_output,
        status="converted",
    )


def apply(result: ConversionResult) -> ConversionResult:
    """Write a staged result through to the real skill directory.

    Refuses (status "unsafe") when lint_ok is False. Ensures .backup/SKILL.md
    holds the ORIGINAL before overwriting, copies the staged SKILL.md over the
    real one, and places scripts as siblings of SKILL.md.
    """
    plan = result.plan
    if not plan.eligible:
        return result

    if not result.lint_ok:
        result.status = "unsafe"
        result.applied = False
        return result

    skill_md_path = plan.skill.path
    skill_dir = plan.skill.dir
    converted_dir = result.staged_dir or os.path.join(skill_dir, _CONVERTED_DIR)
    backup_dir = os.path.join(converted_dir, _BACKUP_DIR)
    backup_path = os.path.join(backup_dir, "SKILL.md")

    os.makedirs(backup_dir, exist_ok=True)
    if not os.path.isfile(backup_path):
        shutil.copyfile(skill_md_path, backup_path)

    shutil.copyfile(result.rewritten_skill_md, skill_md_path)

    for staged_script in result.script_paths:
        sibling = os.path.join(skill_dir, os.path.basename(staged_script))
        shutil.copyfile(staged_script, sibling)

    result.backup_path = backup_path
    result.applied = True
    result.status = "converted"
    return result
