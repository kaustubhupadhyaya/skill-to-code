"""Render a list of AnalysisResult in table / json / md form. Stdlib only.

Pure formatting + filtering. No filesystem or scoring logic lives here; the
analyzer produces AnalysisResult, this module turns a list of them into text.
"""

from __future__ import annotations

import json
from typing import Optional

from skill_to_code.types import AnalysisResult, Bucket

_BUCKET_ORDER = (Bucket.REPLACE.value, Bucket.WRAP.value, Bucket.SKIP.value)


def _ranked(results: list[AnalysisResult]) -> list[AnalysisResult]:
    """Score desc; ties broken by name for stable, deterministic output."""
    return sorted(results, key=lambda r: (-r.score, r.skill.name.lower()))


def _top_reason(result: AnalysisResult) -> str:
    return result.reasons[0] if result.reasons else ""


def _bucket_counts(results: list[AnalysisResult]) -> dict[str, int]:
    counts = {b: 0 for b in _BUCKET_ORDER}
    for r in results:
        counts[r.bucket] = counts.get(r.bucket, 0) + 1
    return counts


def _summary_line(results: list[AnalysisResult]) -> str:
    counts = _bucket_counts(results)
    parts = [f"{b}={counts[b]}" for b in _BUCKET_ORDER]
    return f"{len(results)} skills  |  " + "  ".join(parts)


def apply_filters(
    results: list[AnalysisResult],
    bucket: Optional[str] = None,
    min_score: Optional[int] = None,
    source: Optional[str] = None,
) -> list[AnalysisResult]:
    """Narrow results by bucket, minimum score, and/or source. All combinable."""
    out = results
    if bucket is not None:
        out = [r for r in out if r.bucket == bucket]
    if min_score is not None:
        out = [r for r in out if r.score >= min_score]
    if source is not None:
        out = [r for r in out if r.skill.source == source]
    return out


def format_table(results: list[AnalysisResult]) -> str:
    """Aligned columns [score, bucket, source, name, top reason], score desc."""
    ranked = _ranked(results)
    headers = ("SCORE", "BUCKET", "SOURCE", "NAME", "TOP REASON")
    rows = [
        (
            str(r.score),
            r.bucket,
            r.skill.source,
            r.skill.name,
            _top_reason(r),
        )
        for r in ranked
    ]

    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))

    def _fmt(cells) -> str:
        # Last column is not padded (avoids trailing whitespace on the widest col).
        padded = [cells[i].ljust(widths[i]) for i in range(len(cells) - 1)]
        padded.append(cells[-1])
        return "  ".join(padded).rstrip()

    lines = [_fmt(headers), _fmt(tuple("-" * w for w in widths))]
    lines.extend(_fmt(row) for row in rows)
    lines.append("")
    lines.append(_summary_line(results))
    return "\n".join(lines)


def format_json(results: list[AnalysisResult]) -> str:
    """Stable JSON: one record per skill (score desc), including raw signals."""
    records = []
    for r in _ranked(results):
        s = r.skill
        records.append(
            {
                "name": s.name,
                "score": r.score,
                "bucket": r.bucket,
                "source": s.source,
                "plugin": s.plugin,
                "path": s.path,
                "dir": s.dir,
                "reasons": list(r.reasons),
                "signals": r.signals,
            }
        )
    payload = {
        "summary": _bucket_counts(results),
        "count": len(results),
        "skills": records,
    }
    return json.dumps(payload, indent=2)


def format_md(results: list[AnalysisResult]) -> str:
    """Shareable markdown report: per-bucket summary + a ranked table."""
    ranked = _ranked(results)
    counts = _bucket_counts(results)

    lines: list[str] = ["# Skill-to-Code audit", ""]
    lines.append(f"**{len(results)} skills scanned.**")
    lines.append("")
    lines.append("| Bucket | Count |")
    lines.append("| --- | --- |")
    for b in _BUCKET_ORDER:
        lines.append(f"| {b} | {counts[b]} |")
    lines.append("")

    lines.append("| Score | Bucket | Source | Name | Top reason |")
    lines.append("| ---: | --- | --- | --- | --- |")
    for r in ranked:
        reason = _top_reason(r).replace("|", "\\|")
        name = r.skill.name.replace("|", "\\|")
        lines.append(
            f"| {r.score} | {r.bucket} | {r.skill.source} | {name} | {reason} |"
        )
    lines.append("")
    return "\n".join(lines)
