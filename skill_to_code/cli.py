"""Command-line entry point for skill-to-code. Stdlib only.

Two subcommands:

``scan``    discover -> parse -> analyze -> filter -> report               (read-only, M1)
``convert`` discover -> analyze -> plan -> stage -> lint -> verify? -> apply?  (M2/M4)

Both commands accept ``--root PATH`` (repeatable) to REPLACE the default
discovery roots with a scoped set, and ``--include PATH`` which stays additive.
``convert`` adds an opt-in ``--verify-run`` that executes each staged script as
a smoke test (see docs/verify.md) and folds a run-time failure into the same
gate that lint uses, so ``apply`` refuses it.

All real logic lives in the discovery / parser / analyzer / report / extract /
convert / lint / diffing modules; this file only parses args, threads results
between them, and renders the convert summary table.

The ``convert`` command drives the convert-module API (owned by
``skill_to_code/convert.py``):

    plan_conversion(discovered, parsed, analysis, *, bucket=None) -> ConversionPlan
    is_unchanged(skill_md_path)                                   -> bool
    render_scripts(plan)                                          -> {name: text}
    rewrite_skill_md(plan, parsed, original_text)                 -> str
    stage(plan, scripts, rewritten, skill_md_path, *, lint_ok, lint_output)
                                                                 -> ConversionResult
    apply(result)                                                -> ConversionResult

The convert module writes only under ``<skill_dir>/.converted/`` (never the real
SKILL.md) until ``apply``. It does NOT lint; the CLI runs lint (lint.py) on the
staged scripts and injects ``lint_ok`` before ``apply``, refusing ``--apply``
when lint fails (status "unsafe"). Idempotency (status "unchanged") is checked
via ``is_unchanged`` before any staging.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from typing import Optional

from skill_to_code import __version__
from skill_to_code.analyzer import analyze, load_rubric
from skill_to_code.discovery import discover_skills
from skill_to_code.parser import parse_skill
from skill_to_code.report import apply_filters, format_json, format_md, format_table
from skill_to_code.types import Bucket, ConversionResult, DiscoveredSkill, Source

_FORMATTERS = {
    "table": format_table,
    "json": format_json,
    "md": format_md,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="skill-to-code",
        description="Audit Claude Code skills and score how programmable each is.",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    scan = sub.add_parser("scan", help="Discover and score skills (read-only).")
    scan.add_argument(
        "--format",
        choices=list(_FORMATTERS),
        default="table",
        help="Output format (default: table).",
    )
    scan.add_argument(
        "--bucket",
        choices=[b.value for b in Bucket],
        help="Only show skills in this bucket.",
    )
    scan.add_argument(
        "--min-score",
        type=int,
        metavar="N",
        help="Only show skills scoring >= N.",
    )
    scan.add_argument(
        "--source",
        choices=[s.value for s in Source],
        help="Only show skills from this source.",
    )
    scan.add_argument(
        "--include",
        action="append",
        metavar="PATH",
        help="Extra skill dir or root to scan, ADDED to the roots (repeatable).",
    )
    scan.add_argument(
        "--root",
        action="append",
        metavar="PATH",
        help="Scan ONLY this dir/root, REPLACING the default roots (repeatable). "
        "--include stays additive on top.",
    )
    scan.add_argument(
        "--no-cache",
        action="store_true",
        help="Bypass the discovery cache and rescan from disk.",
    )
    scan.set_defaults(func=_cmd_scan)

    convert = sub.add_parser(
        "convert",
        help="Convert programmable skills into scripts (dry-run by default).",
    )
    convert.add_argument(
        "--bucket",
        choices=[Bucket.REPLACE.value, Bucket.WRAP.value],
        help="Force codegen strategy (default: per-skill analyzer bucket).",
    )
    convert.add_argument(
        "--min-score",
        type=int,
        metavar="N",
        help="Only convert skills scoring >= N.",
    )
    convert.add_argument(
        "--source",
        choices=[s.value for s in Source],
        help="Only convert skills from this source.",
    )
    convert.add_argument(
        "--include",
        action="append",
        metavar="PATH",
        help="Extra skill dir or root to scan, ADDED to the roots (repeatable).",
    )
    convert.add_argument(
        "--root",
        action="append",
        metavar="PATH",
        help="Scan ONLY this dir/root, REPLACING the default roots (repeatable). "
        "--include stays additive on top.",
    )
    convert.add_argument(
        "--name",
        action="append",
        metavar="NAME",
        help="Limit to specific skills by name (repeatable).",
    )
    convert.add_argument(
        "--diff",
        action="store_true",
        help="Print a unified diff of original vs rewritten SKILL.md per skill.",
    )
    convert.add_argument(
        "--apply",
        action="store_true",
        help="Write through to the real SKILL.md (backup + lint gate).",
    )
    convert.add_argument(
        "--verify-run",
        action="store_true",
        help="Opt-in: EXECUTE each staged script once as a smoke test after "
        "lint; a run-time failure blocks --apply (see docs/verify.md).",
    )
    convert.add_argument(
        "--verify-timeout",
        type=float,
        default=20.0,
        metavar="SECONDS",
        help="Per-script execution budget for --verify-run (default: 20).",
    )
    convert.add_argument(
        "--fixture",
        metavar="DIR",
        help="Seed each --verify-run temp dir by copying DIR into it first.",
    )
    convert.add_argument(
        "--no-cache",
        action="store_true",
        help="Bypass the discovery cache and rescan from disk.",
    )
    convert.set_defaults(func=_cmd_convert)

    return parser


def _discover(args: argparse.Namespace) -> list[DiscoveredSkill]:
    """Run discovery for scan/convert, honoring --root (scoped) and --include.

    Default: the built-in roots (user + plugin + project) plus any --include
    paths (additive). With --root, REPLACE the built-in roots: scan only the
    given root(s) -- plus --include, still additive -- by pointing discovery at
    an empty home and clearing the project roots so nothing from the real
    machine leaks in.

    Scoped runs deliberately bypass the on-disk cache. The scoped home is a
    throwaway temp dir that is removed before the analysis pass (and the atexit
    flush) runs, so caching to it would recreate/leak that dir; a fresh temp
    home each invocation is also always cold, so there is nothing to gain. The
    shared cache is left untouched -- notably NOT pruned down to the scoped
    subset -- so the next full scan stays warm.
    """
    use_cache = not args.no_cache
    roots = getattr(args, "root", None)
    if not roots:
        return discover_skills(includes=args.include, use_cache=use_cache)
    includes = list(roots) + list(args.include or [])
    with tempfile.TemporaryDirectory(prefix="stc-scoped-home-") as empty_home:
        return discover_skills(
            includes=includes,
            use_cache=False,
            home=empty_home,
            project_roots=[],
        )


def _cmd_scan(args: argparse.Namespace) -> int:
    rubric = load_rubric()
    discovered = _discover(args)
    results = [analyze(parse_skill(d), d, rubric) for d in discovered]
    results = apply_filters(
        results,
        bucket=args.bucket,
        min_score=args.min_score,
        source=args.source,
    )
    print(_FORMATTERS[args.format](results))
    return 0


def _cmd_convert(args: argparse.Namespace) -> int:
    # Import convert here (not at module load) so `scan` keeps working even if
    # the convert module is mid-development, and so import cost is paid only when
    # actually converting.
    from skill_to_code import convert as convert_mod
    from skill_to_code import lint as lint_mod

    # Verify is opt-in and EXECUTES generated code; import it (and warn) only
    # when actually requested.
    verify_mod = None
    if args.verify_run:
        from skill_to_code import verify as verify_mod
        print(
            "WARNING: --verify-run EXECUTES each generated script in a throwaway "
            "temp dir; only run it on scripts you have read and trust.",
            file=sys.stderr,
        )

    rubric = load_rubric()
    discovered = _discover(args)

    # Analyze everything, then narrow by score / source / name. --name matches
    # on the skill's discovered name (repeatable).
    analyses = [analyze(parse_skill(d), d, rubric) for d in discovered]
    analyses = apply_filters(analyses, min_score=args.min_score, source=args.source)
    if args.name:
        wanted = set(args.name)
        analyses = [a for a in analyses if a.skill.name in wanted]

    results: list[ConversionResult] = []
    verify_outcomes: list = []  # (skill_name, VerifyResult) for verified skills
    for analysis in analyses:
        result, verify_result = _convert_one(
            analysis,
            convert_mod,
            lint_mod,
            verify_mod,
            bucket_override=args.bucket,
            apply=args.apply,
            fixture=args.fixture,
            verify_timeout=args.verify_timeout,
        )
        results.append(result)
        if verify_result is not None:
            verify_outcomes.append((result.plan.skill.name, verify_result))

    print(_render_convert_table(results))
    if verify_outcomes:
        print(_render_verify_section(verify_outcomes))

    if args.diff:
        _print_diffs(results)

    return 0


def _convert_one(
    analysis,
    convert_mod,
    lint_mod,
    verify_mod,
    *,
    bucket_override: Optional[str],
    apply: bool,
    fixture: Optional[str],
    verify_timeout: float,
):
    """Plan -> idempotency -> render -> stage -> lint -> verify? -> apply? for one skill.

    Lint runs on the STAGED script files (they must exist on disk first), then
    the result's ``lint_ok`` gates ``apply``. A lint failure marks the result
    "unsafe" and blocks write-through. When ``verify_mod`` is set (``--verify-run``)
    and lint passed, each staged script is executed once as a smoke test; a
    run-time failure folds into the SAME ``lint_ok`` gate, so ``apply`` refuses
    it exactly as for a lint failure. A skip (interpreter absent) never blocks.

    Returns ``(ConversionResult, VerifyResult | None)`` -- the VerifyResult is
    the representative outcome for rendering (None when nothing was verified).
    """
    discovered = analysis.skill
    parsed = parse_skill(discovered)
    plan = convert_mod.plan_conversion(
        discovered, parsed, analysis, bucket=bucket_override
    )

    # Honesty guard: ineligible plans stage nothing.
    if not plan.eligible:
        return convert_mod.stage(plan, {}, "", discovered.path), None

    # Idempotency: an unchanged SKILL.md (hash matches stored plan.json) is a
    # no-op. Report it without restaging.
    if convert_mod.is_unchanged(discovered.path):
        return (
            ConversionResult(
                plan=plan,
                staged_dir=os.path.join(discovered.dir, ".converted"),
                status="unchanged",
            ),
            None,
        )

    scripts = convert_mod.render_scripts(plan)
    original_text = _read_text(discovered.path)
    rewritten = convert_mod.rewrite_skill_md(plan, parsed, original_text)
    result = convert_mod.stage(plan, scripts, rewritten, discovered.path)

    # Lint the staged scripts; inject the verdict before any apply.
    if result.script_paths:
        ok, output = lint_mod.lint_scripts(result.script_paths)
        result.lint_ok = ok
        result.lint_output = output
        if not ok:
            result.status = "unsafe"

    # Verify runs AFTER a passing lint, only when opted in and something staged.
    # A real failure (ran and not ok) folds into lint_ok -> apply() refuses it.
    verify_result = None
    if verify_mod is not None and result.lint_ok and result.script_paths:
        blocked, verify_result = _verify_staged(
            verify_mod, result.script_paths, fixture=fixture, timeout_s=verify_timeout
        )
        if blocked:
            result.lint_ok = False
            result.status = "unsafe"
            summary = _verify_summary_line(verify_result)
            result.lint_output = (result.lint_output + "\n" + summary).strip()

    if apply:
        if not result.lint_ok:
            result.status = "unsafe"
        else:
            result = convert_mod.apply(result)

    return result, verify_result


def _verify_staged(verify_mod, script_paths: list[str], *, fixture, timeout_s):
    """Execute each staged script as a smoke test via verify.verify_script.

    Returns ``(blocked, representative)``. ``blocked`` is True as soon as ANY
    script ran and failed (nonzero exit or timeout) -- that first failure is the
    representative. Otherwise every script passed or skipped, and the last result
    is returned for reporting.
    """
    representative = None
    for path in script_paths:
        vr = verify_mod.verify_script(path, fixture=fixture, timeout_s=timeout_s)
        if vr.ran and not vr.ok:
            return True, vr
        representative = vr
    return False, representative


def _verify_summary_line(vr) -> str:
    """One-line failure note appended to lint_output when verify blocks apply."""
    if vr.timed_out:
        return f"verify FAILED: {vr.interpreter} timed out after {vr.duration_s:.1f}s"
    detail = f"rc={vr.returncode}"
    stderr = (vr.stderr or "").strip()
    if stderr:
        detail += f": {stderr.splitlines()[0]}"
    return f"verify FAILED: {vr.interpreter} {detail}"


def _verify_outcome(vr) -> str:
    """Human-readable per-skill verify outcome for the VERIFY summary section."""
    if not vr.ran:
        return f"skipped ({vr.skipped_reason or 'not run'})"
    if vr.timed_out:
        return f"FAIL (timed out after {vr.duration_s:.1f}s)"
    verdict = "ok" if vr.ok else "FAIL"
    return f"{verdict} (rc={vr.returncode}, {vr.duration_s:.1f}s)"


def _render_verify_section(outcomes: list) -> str:
    """A VERIFY block listing each verified skill's run outcome."""
    width = max(len(name) for name, _ in outcomes)
    lines = ["", "VERIFY"]
    lines.extend(f"  {name.ljust(width)}  {_verify_outcome(vr)}" for name, vr in outcomes)
    return "\n".join(lines)


def _render_convert_table(results: list[ConversionResult]) -> str:
    """Aligned summary: skill | bucket | #segments | script | status."""
    headers = ("SKILL", "BUCKET", "#SEG", "SCRIPT", "STATUS")
    rows: list[tuple[str, str, str, str, str]] = []
    for r in results:
        plan = r.plan
        script = ", ".join(_basename(p) for p in r.script_paths)
        rows.append(
            (
                plan.skill.name,
                plan.bucket or "",
                str(len(plan.segments)),
                script,
                r.status,
            )
        )

    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))

    def _fmt(cells) -> str:
        padded = [cells[i].ljust(widths[i]) for i in range(len(cells) - 1)]
        padded.append(cells[-1])
        return "  ".join(padded).rstrip()

    lines = [_fmt(headers), _fmt(tuple("-" * w for w in widths))]
    lines.extend(_fmt(row) for row in rows)
    lines.append("")
    lines.append(_convert_summary(results))
    return "\n".join(lines)


def _convert_summary(results: list[ConversionResult]) -> str:
    counts: dict[str, int] = {}
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1
    parts = [f"{status}={counts[status]}" for status in sorted(counts)]
    return f"{len(results)} skills  |  " + "  ".join(parts)


def _print_diffs(results: list[ConversionResult]) -> None:
    """Print a unified diff (original -> rewritten SKILL.md) per converted skill."""
    from skill_to_code.diffing import unified_diff

    for r in results:
        if r.status not in ("converted", "unsafe") or not r.rewritten_skill_md:
            continue
        # After --apply the real SKILL.md IS the rewritten text; read the backup
        # (the preserved original) so the diff still shows the real change.
        origin_path = r.backup_path if r.applied and r.backup_path else r.plan.skill.path
        original = _read_text(origin_path)
        rewritten = _read_text(r.rewritten_skill_md)
        diff = unified_diff(original, rewritten, f"{r.plan.skill.name}/SKILL.md")
        if diff:
            print()
            print(diff)


def _read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()


def _basename(path: str) -> str:
    return os.path.basename(path)


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
