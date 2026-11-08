"""Analyzer tests: bucket assignment, monotonic score ordering, exact math."""

from __future__ import annotations

import pytest

from skill_to_code.analyzer import analyze, load_rubric
from skill_to_code.types import DiscoveredSkill, ParsedSkill

DISC = DiscoveredSkill(
    path="/x/SKILL.md",
    dir="/x",
    name="x",
    source="user",
    plugin=None,
    mtime=0.0,
)


def _parsed(body: str, *, code_blocks=None, code_langs=None, siblings=None):
    return ParsedSkill(
        frontmatter={},
        body=body,
        headings=[],
        code_blocks=code_blocks or [],
        code_langs=code_langs or [],
        sibling_scripts=siblings or [],
    )


# A code-heavy, mechanical skill: lots of shell, sibling script, structured IO.
CODE_HEAVY_BODY = """\
# Convert

Input: a file path.
Output: a converted file.

Run the script to convert. Read the file, parse it, then write the output.
Execute the commands below to download, extract, and copy the results.

```bash
$ curl -O https://example.com/data.zip
$ unzip data.zip
$ python convert.py --in data --out result
$ git add -A
$ git commit -m "done"
```
"""

CODE_HEAVY_CODE = (
    "$ curl -O https://example.com/data.zip\n"
    "$ unzip data.zip\n"
    "$ python convert.py --in data --out result\n"
    "$ git add -A\n"
    '$ git commit -m "done"'
)

# A mixed skill: mechanical + commands, but some judgment too -> wrap band.
MIXED_BODY = """\
# Triage

Input: an issue number.
Output: a triage label.

Read the issue and parse the labels. Fetch the linked PR, then list the files.
Run the checks below. After that, decide how to prioritize and recommend a label.

```bash
$ gh issue view 123
$ gh pr list
$ git log --oneline
```
"""

MIXED_CODE = "$ gh issue view 123\n$ gh pr list\n$ git log --oneline"

# A judgment-heavy prose skill: no code, all reasoning verbs.
JUDGMENT_BODY = """\
# Design Review

Consider the proposal carefully. Evaluate the tradeoffs and assess the risks.
Interpret the requirements, weigh the options, and recommend a direction.
Analyze the design, critique the approach, and explain your reasoning. Judge
whether it is sound. Discuss alternatives and prioritize the concerns. Reflect
and infer the intent, then determine the best path and clarify the ambiguities.
"""


def test_code_heavy_is_replace(rubric):
    parsed = _parsed(
        CODE_HEAVY_BODY,
        code_blocks=[CODE_HEAVY_CODE],
        code_langs=["bash"],
        siblings=["/x/convert.py"],
    )
    result = analyze(parsed, DISC, rubric)
    assert result.bucket == "replace"
    assert result.score >= 70


def test_judgment_heavy_is_skip(rubric):
    parsed = _parsed(JUDGMENT_BODY)
    result = analyze(parsed, DISC, rubric)
    assert result.bucket == "skip"
    assert result.score < 35


def test_mixed_is_wrap(rubric):
    parsed = _parsed(MIXED_BODY, code_blocks=[MIXED_CODE], code_langs=["bash"])
    result = analyze(parsed, DISC, rubric)
    assert result.bucket == "wrap"
    assert 35 <= result.score < 70


def test_monotonic_score_ordering(rubric):
    heavy = analyze(
        _parsed(
            CODE_HEAVY_BODY,
            code_blocks=[CODE_HEAVY_CODE],
            code_langs=["bash"],
            siblings=["/x/convert.py"],
        ),
        DISC,
        rubric,
    )
    mixed = analyze(
        _parsed(MIXED_BODY, code_blocks=[MIXED_CODE], code_langs=["bash"]),
        DISC,
        rubric,
    )
    prose = analyze(_parsed(JUDGMENT_BODY), DISC, rubric)
    assert heavy.score > mixed.score > prose.score


def test_signals_present(rubric):
    result = analyze(
        _parsed(
            CODE_HEAVY_BODY,
            code_blocks=[CODE_HEAVY_CODE],
            code_langs=["bash"],
            siblings=["/x/convert.py"],
        ),
        DISC,
        rubric,
    )
    for key in (
        "code_density",
        "mechanical_ratio",
        "sibling_scripts",
        "structured_io",
        "command_lines",
        "mech",
        "judg",
    ):
        assert key in result.signals
    assert result.signals["sibling_scripts"] == 1.0
    assert result.signals["structured_io"] == 1.0


def test_exact_math_hand_computed():
    """Hand-computed example locking the formula.

    Body (7 lines, indices shown):
        L1: # Task
        L2: (blank)
        L3: Input: a path.
        L4: Output: a result.
        L5: (blank)
        L6: ```bash
        L7: $ run the copy command
        L8: ```
    But code_blocks/langs are passed explicitly (the analyzer does not re-parse
    the body); the fence lines still exist in body for line counting.

    Tokens (via [a-z]+, lowercased) that are verbs:
        mechanical: "run" (L7), "copy" (L7)   -> mech = 2
        judgment: none                          -> judg = 0
        tv = max(1, 2+0) = 2
        mechanical_ratio = 2/2 = 1.0
        judg penalty term = 0

    code_blocks = ["$ run the copy command"] -> 1 code line
    total body lines = 8
    code_density = min(1, 1/8/0.5) = min(1, 0.25) = 0.25

    sibling_scripts = 0.0 (none)

    structured_io: body has "Input:" and "Output:" -> 1.0

    command_lines: fence lang "bash" is command-like -> its 1 line counts;
        plus body lines starting with "$": L7 "$ run the copy command" -> 1.
        cmd_count = 1 (from fence block) + 1 (body "$" line) = 2
        command_lines = min(1, 2/10) = 0.2

    base = 100 * (0.30*0.25 + 0.25*1.0 + 0.15*0.0 + 0.10*1.0 + 0.20*0.2)
         = 100 * (0.075 + 0.25 + 0.0 + 0.10 + 0.04)
         = 100 * 0.465 ~= 46.5   (46.50000000000001 in IEEE-754 doubles)
    penalty = 35 * (0/2) = 0
    score = int(round(46.50000000000001)) = 47
        NOTE: base is fractionally ABOVE 46.5, so round() goes up to 47 rather
        than applying banker's rounding to an exact .5. The formula is computed
        in floating point exactly as the analyzer does it; this is the real
        result, not an idealized decimal one.
    """
    rubric = load_rubric()
    body = (
        "# Task\n"
        "\n"
        "Input: a path.\n"
        "Output: a result.\n"
        "\n"
        "```bash\n"
        "$ run the copy command\n"
        "```"
    )
    parsed = ParsedSkill(
        frontmatter={},
        body=body,
        headings=["Task"],
        code_blocks=["$ run the copy command"],
        code_langs=["bash"],
        sibling_scripts=[],
    )
    result = analyze(parsed, DISC, rubric)
    assert result.signals["mech"] == 2
    assert result.signals["judg"] == 0
    assert result.signals["code_density"] == 0.25
    assert result.signals["mechanical_ratio"] == 1.0
    assert result.signals["structured_io"] == 1.0
    assert result.signals["command_lines"] == 0.2
    assert result.signals["base"] == pytest.approx(46.5)
    assert result.signals["penalty"] == 0.0
    assert result.score == 47
    assert result.bucket == "wrap"
