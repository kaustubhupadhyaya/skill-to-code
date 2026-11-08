"""Shared data contract for skill_to_code.

Every module (discovery, parser, analyzer, report, cache, cli) codes against
the types defined here. Stdlib only.

Pipeline:
    discovery  -> list[DiscoveredSkill]
    parser     -> ParsedSkill   (per DiscoveredSkill)
    analyzer   -> AnalysisResult (wraps a DiscoveredSkill + score/bucket)
    report     -> renders list[AnalysisResult]
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class Bucket(str, Enum):
    """Programmability verdict for a skill."""

    REPLACE = "replace"  # fully scriptable
    WRAP = "wrap"        # mostly mechanical, some judgment
    SKIP = "skip"        # judgment-heavy, leave alone


class Source(str, Enum):
    """Where a discovered skill was found."""

    USER = "user"        # ~/.claude/skills
    PLUGIN = "plugin"    # ~/.claude/plugins
    PROJECT = "project"  # <proj>/.claude/skills
    INCLUDE = "include"  # supplied via --include


@dataclass
class DiscoveredSkill:
    """A SKILL.md located on disk. Produced by discovery, before any parsing."""

    path: str            # absolute path to SKILL.md
    dir: str             # absolute path to the skill directory
    name: str            # frontmatter `name:` if present, else dir basename
    source: str          # a Source value
    plugin: Optional[str]  # plugin name when source == plugin, else None
    mtime: float         # SKILL.md modification time (epoch seconds)


@dataclass
class ParsedSkill:
    """Structured view of one SKILL.md. Produced by the parser."""

    frontmatter: dict              # parsed YAML frontmatter (empty dict if none)
    body: str                      # markdown body with frontmatter stripped
    headings: list[str] = field(default_factory=list)        # heading texts
    code_blocks: list[str] = field(default_factory=list)     # fenced block contents
    code_langs: list[str] = field(default_factory=list)      # fence info strings ("" if none)
    sibling_scripts: list[str] = field(default_factory=list)  # abs paths to .py/.ps1/.sh/.js in dir


@dataclass
class AnalysisResult:
    """Scored verdict for one skill. Produced by the analyzer; consumed by report."""

    skill: DiscoveredSkill
    score: int                 # 0-100
    bucket: str                # a Bucket value
    reasons: list[str] = field(default_factory=list)   # human-readable fired signals
    signals: dict = field(default_factory=dict)        # raw numeric signal values + mech/judg counts


# ---------------------------------------------------------------------------
# Conversion contract (M2: extract -> convert -> lint -> stage/apply).
#
# Pipeline (mirrors the scan pipeline above):
#     extract  -> list[CodeSegment]  (per ParsedSkill; executable blocks only)
#     convert  -> ConversionPlan     (segments + bucket-specific codegen intent)
#     convert  -> ConversionResult   (staged/applied outcome, after lint)
#
# All conversion is LLM-free and verbatim: it only lifts fenced code that
# already exists in SKILL.md and never interprets prose into code.
# ---------------------------------------------------------------------------


@dataclass
class CodeSegment:
    """A maximal run of consecutive extractable fenced blocks of one language.

    Produced by the extractor. ``lines`` are prompt-stripped, verbatim command
    lines ready to be written into a script. A segment never mixes languages.
    """

    lang: str                  # normalized executable lang (e.g. "bash", "powershell", "python")
    ext: str                   # target script extension (".sh" / ".ps1" / ".py")
    lines: list[str]           # prompt-stripped command lines, in document order
    order: int                 # 0-based position of this segment among a skill's segments
    source_block_index: int    # index into ParsedSkill.code_blocks of the segment's FIRST block


@dataclass
class ConversionPlan:
    """What conversion intends to do to one skill. Produced before any writes.

    ``eligible`` is False (with a human-readable ``reason``) when there is no
    deterministically-extractable executable code — the honesty guard. Such a
    plan is a no-op: no script is emitted.
    """

    skill: DiscoveredSkill
    bucket: str                        # a Bucket value ("replace" / "wrap")
    segments: list[CodeSegment] = field(default_factory=list)
    dominant_lang: Optional[str] = None  # most-lines executable lang (None when no segments)
    script_name: str = ""              # sanitized base name for emitted script(s)
    skipped_blocks: list[str] = field(default_factory=list)  # one reason string per skipped block
    eligible: bool = False             # False -> no-op (honesty guard); see reason
    reason: str = ""                   # why (in)eligible / notable planning decision


@dataclass
class ConversionResult:
    """Outcome of staging (and optionally applying) a ConversionPlan.

    ``status`` values:
        "converted" — script(s) + rewritten SKILL.md staged (or applied)
        "noop"      — plan ineligible (no extractable executable code)
        "unchanged" — .converted/plan.json hash matches current SKILL.md
        "unsafe"    — lint failed; --apply refused
        "skipped"   — filtered out (bucket/name/score) before planning
    """

    plan: ConversionPlan
    staged_dir: str                    # abs path to <skill_dir>/.converted (""/unset for skipped)
    script_paths: list[str] = field(default_factory=list)  # abs paths to staged script(s)
    rewritten_skill_md: str = ""       # abs path to staged rewritten SKILL.md
    backup_path: Optional[str] = None  # abs path to .backup/SKILL.md once a backup exists
    lint_ok: bool = True               # False blocks --apply (status -> "unsafe")
    lint_output: str = ""              # combined lint tool output / skip-with-warning notes
    applied: bool = False              # True once written through to the real SKILL.md
    status: str = "noop"               # see docstring


def code_segment_to_dict(seg: "CodeSegment") -> dict:
    """asdict-equivalent for a CodeSegment (stable ordering, JSON-safe)."""
    return {
        "lang": seg.lang,
        "ext": seg.ext,
        "lines": list(seg.lines),
        "order": seg.order,
        "source_block_index": seg.source_block_index,
    }


def discovered_skill_to_dict(skill: "DiscoveredSkill") -> dict:
    """JSON-safe view of a DiscoveredSkill for plan.json audit records."""
    return {
        "path": skill.path,
        "dir": skill.dir,
        "name": skill.name,
        "source": skill.source,
        "plugin": skill.plugin,
        "mtime": skill.mtime,
    }


def conversion_plan_to_dict(plan: "ConversionPlan") -> dict:
    """Serialize a ConversionPlan for staging in .converted/plan.json.

    Stdlib ``dataclasses.asdict`` would work, but a hand-rolled serializer keeps
    the on-disk shape stable and explicit (it is an audit + idempotency record,
    not an internal detail). The source SKILL.md sha256 is added by the writer.
    """
    return {
        "skill": discovered_skill_to_dict(plan.skill),
        "bucket": plan.bucket,
        "segments": [code_segment_to_dict(s) for s in plan.segments],
        "dominant_lang": plan.dominant_lang,
        "script_name": plan.script_name,
        "skipped_blocks": list(plan.skipped_blocks),
        "eligible": plan.eligible,
        "reason": plan.reason,
    }


# ---------------------------------------------------------------------------
# Verify contract (M4: optional sandboxed --verify-run + rollback, F5.2/T5.3).
#
# A VerifyResult is the outcome of a single SMOKE TEST: the CLI (never the
# convert module) executes one generated script in a throwaway temp working
# directory, bounded by a timeout, and records what happened. It is STRICTLY
# OPT-IN (--verify-run) and never runs against a real skill directory.
#
# ``ok`` is the apply-gate signal: ok == (ran and returncode == 0 and not
# timed_out). A verify FAILURE (ran and not ok) marks the conversion unsafe so
# apply() refuses it, exactly like a lint failure — see docs/verify.md for the
# full safety contract and how the CLI threads this into the apply gate.
#
# This is a smoke test, NOT an OS security sandbox: there is no filesystem or
# network isolation. Documented honestly in docs/verify.md.
# ---------------------------------------------------------------------------


@dataclass
class VerifyResult:
    """Outcome of executing one generated script as an opt-in smoke test.

    A "did not run" result (interpreter absent, or --verify-run not requested)
    is the default: ``ran=False``, ``ok=False``, with ``skipped_reason`` set.
    Such a skip must NOT block --apply — only a real failure (``ran and not
    ok``) does. See docs/verify.md.
    """

    ran: bool = False               # True once the interpreter actually executed the script
    ok: bool = False                # ran and returncode == 0 and not timed_out
    returncode: Optional[int] = None  # process exit code; None when it never ran
    stdout: str = ""                # captured stdout (may be truncated by the runner)
    stderr: str = ""                # captured stderr (may be truncated by the runner)
    duration_s: float = 0.0         # wall-clock execution time in seconds
    timed_out: bool = False         # True if killed by the --verify-timeout budget
    skipped_reason: str = ""        # why it didn't run (e.g. "interpreter not found: bash")
    interpreter: str = ""           # interpreter used/attempted ("python" / "bash" / "powershell")


def verify_result_to_dict(vr: "VerifyResult") -> dict:
    """JSON-safe view of a VerifyResult (stable ordering) for audit records.

    Mirrors the other ``*_to_dict`` serializers: hand-rolled so the on-disk /
    reported shape stays explicit rather than an ``asdict`` implementation
    detail.
    """
    return {
        "ran": vr.ran,
        "ok": vr.ok,
        "returncode": vr.returncode,
        "stdout": vr.stdout,
        "stderr": vr.stderr,
        "duration_s": vr.duration_s,
        "timed_out": vr.timed_out,
        "skipped_reason": vr.skipped_reason,
        "interpreter": vr.interpreter,
    }
