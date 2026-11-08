"""Score a ParsedSkill for programmability. Stdlib only.

Implements the locked scoring formula from rubric.toml exactly. Deterministic —
no LLM. See rubric.toml for the full formula documentation.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

from skill_to_code import cache
from skill_to_code.types import AnalysisResult, Bucket, DiscoveredSkill, ParsedSkill

_WORD_RE = re.compile(r"[a-z]+")
_INPUT_RE = re.compile(r"input\s*:", re.IGNORECASE)
_OUTPUT_RE = re.compile(r"output\s*:", re.IGNORECASE)

# Fence languages whose lines count as command-like.
_COMMAND_LANGS = {"bash", "sh", "shell", "powershell", "ps1", "console"}


def load_rubric(path: str | Path | None = None) -> dict:
    """Load the rubric TOML. Prefers repo-root rubric.toml, falls back to the
    bundled package copy."""
    if path is not None:
        rubric_path = Path(path)
    else:
        pkg_copy = Path(__file__).resolve().parent / "rubric.toml"
        repo_copy = pkg_copy.parent.parent / "rubric.toml"
        rubric_path = repo_copy if repo_copy.is_file() else pkg_copy
    with rubric_path.open("rb") as fh:
        return tomllib.load(fh)


def _first_lang(lang: str) -> str:
    """Fence info string -> its leading language token, lowercased."""
    return lang.strip().split()[0].lower() if lang.strip() else ""


def analyze(
    parsed: ParsedSkill, discovered: DiscoveredSkill, rubric: dict
) -> AnalysisResult:
    """Score a skill, served from the analysis cache when unchanged.

    When a cache session is active (opened by discovery under ``use_cache``) an
    unchanged skill is returned from the cache without re-scoring; otherwise the
    fresh score is computed and stored. With no session active this is a plain,
    side-effect-free call to :func:`_analyze_impl`.
    """
    cached = cache.get_cached_analysis(discovered)
    if cached is not None:
        return cached
    result = _analyze_impl(parsed, discovered, rubric)
    cache.put_cached_analysis(discovered, result)
    return result


def _analyze_impl(
    parsed: ParsedSkill, discovered: DiscoveredSkill, rubric: dict
) -> AnalysisResult:
    weights = rubric["weights"]
    thresholds = rubric["thresholds"]
    penalty_cfg = rubric["penalty"]
    mech_verbs = set(rubric["verbs"]["mechanical"])
    judg_verbs = set(rubric["verbs"]["judgment"])

    body = parsed.body

    # 1-2. tokenize + verb counts.
    tokens = _WORD_RE.findall(body.lower())
    mech = sum(1 for t in tokens if t in mech_verbs)
    judg = sum(1 for t in tokens if t in judg_verbs)
    tv = max(1, mech + judg)

    # 3. signals.
    total_body_lines = max(1, len(body.splitlines()))
    code_lines = sum(
        len(block.splitlines()) for block in parsed.code_blocks
    )
    code_density = min(1.0, code_lines / total_body_lines / 0.5)

    mechanical_ratio = mech / tv

    sibling_scripts = 1.0 if parsed.sibling_scripts else 0.0

    has_input = bool(_INPUT_RE.search(body))
    has_output = bool(_OUTPUT_RE.search(body))
    if has_input and has_output:
        structured_io = 1.0
    elif has_input or has_output:
        structured_io = 0.5
    else:
        structured_io = 0.0

    cmd_count = 0
    for block, lang in zip(parsed.code_blocks, parsed.code_langs):
        if _first_lang(lang) in _COMMAND_LANGS:
            cmd_count += len(block.splitlines())
    for line in body.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("$") or stripped.startswith(">"):
            cmd_count += 1
    command_lines = min(1.0, cmd_count / 10)

    # 4. base.
    base = 100 * (
        weights["code_density"] * code_density
        + weights["mechanical_ratio"] * mechanical_ratio
        + weights["sibling_scripts"] * sibling_scripts
        + weights["structured_io"] * structured_io
        + weights["command_lines"] * command_lines
    )

    # 5. penalty.
    penalty = penalty_cfg["judgment"] * (judg / tv)

    # 6. score.
    score = int(max(0, min(100, round(base - penalty))))

    # 7. bucket.
    if score >= thresholds["replace"]:
        bucket = Bucket.REPLACE.value
    elif score < thresholds["skip"]:
        bucket = Bucket.SKIP.value
    else:
        bucket = Bucket.WRAP.value

    # 8. reasons.
    reasons: list[str] = []
    if code_density >= 0.5:
        pct = round(code_lines / total_body_lines * 100)
        reasons.append(f"high code density ({pct}% of body is code)")
    if mech and mechanical_ratio >= 0.5:
        reasons.append(f"{mech} mechanical verbs vs {judg} judgment")
    if sibling_scripts:
        names = ", ".join(Path(p).name for p in parsed.sibling_scripts)
        reasons.append(f"sibling script {names} present")
    if structured_io == 1.0:
        reasons.append("structured input/output declared")
    elif structured_io == 0.5:
        reasons.append("partial structured input/output")
    if command_lines >= 0.5:
        reasons.append(f"{cmd_count} command-like lines")
    if judg and (judg / tv) >= 0.5:
        reasons.append(f"judgment-heavy: {judg} judgment verbs")

    # 9. signals dict.
    signals = {
        "code_density": code_density,
        "mechanical_ratio": mechanical_ratio,
        "sibling_scripts": sibling_scripts,
        "structured_io": structured_io,
        "command_lines": command_lines,
        "mech": mech,
        "judg": judg,
        "base": base,
        "penalty": penalty,
    }

    return AnalysisResult(
        skill=discovered,
        score=score,
        bucket=bucket,
        reasons=reasons,
        signals=signals,
    )
