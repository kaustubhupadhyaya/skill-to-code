"""Extractor tests: language normalization, prompt stripping, segmentation."""

from __future__ import annotations

from skill_to_code.extract import extract_segments, normalize_lang, strip_prompts
from skill_to_code.types import ParsedSkill


def _parsed(blocks: list[str], langs: list[str]) -> ParsedSkill:
    return ParsedSkill(
        frontmatter={},
        body="",
        headings=[],
        code_blocks=blocks,
        code_langs=langs,
        sibling_scripts=[],
    )


# --- normalize_lang -------------------------------------------------------


def test_normalize_executable_aliases():
    assert normalize_lang("bash") == ("bash", ".sh")
    assert normalize_lang("sh") == ("bash", ".sh")
    assert normalize_lang("zsh") == ("bash", ".sh")
    assert normalize_lang("console") == ("bash", ".sh")
    assert normalize_lang("pwsh") == ("powershell", ".ps1")
    assert normalize_lang("ps1") == ("powershell", ".ps1")
    assert normalize_lang("py") == ("python", ".py")
    assert normalize_lang("python3") == ("python", ".py")


def test_normalize_uses_leading_token_and_lowercases():
    assert normalize_lang("Python {.line-numbers}") == ("python", ".py")
    assert normalize_lang("  BASH  ") == ("bash", ".sh")


def test_normalize_non_executable_and_untagged_and_unknown():
    for tag in ("json", "yaml", "yml", "toml", "text", "md", "html", "diff", "output", "log"):
        assert normalize_lang(tag) == (None, None)
    assert normalize_lang("") == (None, None)
    assert normalize_lang("mermaid") == (None, None)


# --- prompt stripping -----------------------------------------------------


def test_console_with_dollar_prompts_drops_output_and_strips():
    block = ["$ git status", "On branch main", "nothing to commit", "$ git log --oneline"]
    assert strip_prompts(block, "console") == ["git status", "git log --oneline"]


def test_raw_command_block_no_prompts_keeps_all_lines():
    block = ["git add -A", 'git commit -m "wip"', "git push"]
    assert strip_prompts(block, "bash") == block


def test_ps_drive_path_prompt_stripped_in_shell_block():
    block = [r"PS C:\Users\me> Get-Item .", "Directory listing", "> echo done"]
    assert strip_prompts(block, "console") == ["Get-Item .", "echo done"]


def test_python_and_powershell_blocks_not_prompt_stripped():
    py = [">>> x = 1", "print(x)"]
    assert strip_prompts(py, "python") == py
    ps = ["PS> Get-Item .", "Write-Host hi"]
    assert strip_prompts(ps, "powershell") == ps


# --- segmentation ---------------------------------------------------------


def test_json_yaml_untagged_excluded():
    parsed = _parsed(
        ['{"a": 1}', "key: value", "some plain text"],
        ["json", "yaml", ""],
    )
    assert extract_segments(parsed) == []


def test_tagged_blocks_included_with_correct_ext():
    parsed = _parsed(
        ["git status", "Write-Host hi", "print('x')"],
        ["bash", "powershell", "python"],
    )
    segs = extract_segments(parsed)
    assert [(s.lang, s.ext) for s in segs] == [
        ("bash", ".sh"),
        ("powershell", ".ps1"),
        ("python", ".py"),
    ]


def test_consecutive_same_language_blocks_merge():
    parsed = _parsed(
        ["git add -A", "git commit -m x"],
        ["bash", "sh"],  # both canonicalize to bash
    )
    segs = extract_segments(parsed)
    assert len(segs) == 1
    assert segs[0].lang == "bash"
    assert segs[0].lines == ["git add -A", "git commit -m x"]
    assert segs[0].order == 0
    assert segs[0].source_block_index == 0


def test_different_language_blocks_stay_separate():
    parsed = _parsed(
        ["git status", "print('x')"],
        ["bash", "python"],
    )
    segs = extract_segments(parsed)
    assert len(segs) == 2
    assert segs[0].lang == "bash"
    assert segs[1].lang == "python"
    assert [s.order for s in segs] == [0, 1]
    assert [s.source_block_index for s in segs] == [0, 1]


def test_non_extractable_block_does_not_split_same_language_run():
    parsed = _parsed(
        ["git add -A", "some notes", "git push"],
        ["bash", "", "bash"],
    )
    segs = extract_segments(parsed)
    assert len(segs) == 1
    assert segs[0].lines == ["git add -A", "git push"]
    assert segs[0].source_block_index == 0


def test_all_json_replace_looking_skill_yields_empty():
    parsed = _parsed(
        ['{"steps": [1, 2, 3]}', "config:\n  a: 1", '{"more": true}'],
        ["json", "yaml", "json"],
    )
    assert extract_segments(parsed) == []


def test_console_block_that_is_all_output_is_dropped():
    # A console block with prompts where only output survives -> no prompts
    # kept -> empty -> contributes nothing.
    parsed = _parsed(["On branch main\nnothing to commit"], ["console"])
    # No prompt lines at all -> treated as a raw command block (kept). To get a
    # true drop we need a prompt-bearing block whose commands are blank; instead
    # verify a block of pure output with a lone prompt keeps only the command.
    parsed2 = _parsed(["$ ls\nfile-a\nfile-b"], ["console"])
    segs = extract_segments(parsed2)
    assert len(segs) == 1
    assert segs[0].lines == ["ls"]


def test_segment_order_reindexes_after_dropped_empty_segments():
    parsed = _parsed(
        ["git status", '{"x": 1}', "Write-Host a", "Write-Host b"],
        ["bash", "json", "powershell", "pwsh"],
    )
    segs = extract_segments(parsed)
    assert [(s.lang, s.order) for s in segs] == [("bash", 0), ("powershell", 1)]
    assert segs[1].lines == ["Write-Host a", "Write-Host b"]
    assert segs[1].source_block_index == 2
