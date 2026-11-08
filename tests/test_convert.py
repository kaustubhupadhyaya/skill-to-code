"""Convert tests: codegen, SKILL.md rewriting, staging, idempotency, apply.

Drives the real pipeline (parse_skill -> plan_conversion -> render_scripts ->
rewrite_skill_md -> stage -> apply) over tmp_path skill dirs. Lint is injected
(the CLI wires the real lint); these tests pass lint_ok explicitly.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from skill_to_code.parser import parse_skill
from skill_to_code.types import Bucket, DiscoveredSkill
from skill_to_code import convert


def _discovered(skill_dir: Path) -> DiscoveredSkill:
    skill_md = skill_dir / "SKILL.md"
    return DiscoveredSkill(
        path=str(skill_md),
        dir=str(skill_dir),
        name=skill_dir.name,
        source="include",
        plugin=None,
        mtime=skill_md.stat().st_mtime,
    )


class _Analysis:
    """Minimal stand-in for AnalysisResult — plan_conversion only reads .bucket."""

    def __init__(self, bucket: str) -> None:
        self.bucket = bucket


def _plan(skill_dir: Path, bucket: str):
    disc = _discovered(skill_dir)
    parsed = parse_skill(disc)
    plan = convert.plan_conversion(disc, parsed, _Analysis(bucket))
    return disc, parsed, plan


REPLACE_BODY = """\
# Deploy

Run the deploy steps.

```bash
echo "building"
make build
```

Then finish up.

```bash
make deploy
echo "done"
```
"""

WRAP_BODY = """\
# Triage

Read the issue and decide what matters. This needs judgment.

```bash
gh issue view 123
gh pr list
```

Now weigh the options carefully and pick a label. Consider the tradeoffs.

```python
print("scoring")
score = compute()
```

Finally, recommend a direction based on the analysis above.
"""

JSON_ONLY_BODY = """\
# Config

Here is the config shape.

```json
{"a": 1, "b": 2}
```

And a yaml example.

```yaml
key: value
```
"""


# ---------------------------------------------------------------------------
# REPLACE
# ---------------------------------------------------------------------------


def test_replace_one_script_concatenated(make_skill):
    skill_dir = make_skill("deploy", frontmatter="name: deploy\n", body=REPLACE_BODY)
    _, _, plan = _plan(skill_dir, Bucket.REPLACE.value)

    assert plan.eligible
    assert plan.dominant_lang == "bash"

    scripts = convert.render_scripts(plan)
    assert list(scripts) == ["deploy.sh"]
    text = scripts["deploy.sh"]

    lines = text.splitlines()
    assert lines[0] == "#!/usr/bin/env bash"
    assert lines[1] == "set -euo pipefail"
    # All four bash commands from both segments, in document order.
    for cmd in ('echo "building"', "make build", "make deploy", 'echo "done"'):
        assert cmd in text
    assert text.index("make build") < text.index("make deploy")
    assert text.endswith("\n")


def test_replace_stub_keeps_frontmatter_verbatim(make_skill):
    fm = "name: deploy\ndescription: ship it\n"
    skill_dir = make_skill("deploy", frontmatter=fm, body=REPLACE_BODY)
    disc, parsed, plan = _plan(skill_dir, Bucket.REPLACE.value)
    original = (skill_dir / "SKILL.md").read_text(encoding="utf-8")

    rewritten = convert.rewrite_skill_md(plan, parsed, original)

    assert rewritten.startswith("---\nname: deploy\ndescription: ship it\n---\n")
    assert "This skill runs `.converted/deploy.sh`." in rewritten
    # Body prose is gone in a stub.
    assert "Run the deploy steps." not in rewritten


def test_replace_non_dominant_language_skipped(make_skill):
    # bash dominates (4 lines) over python (2 lines).
    body = REPLACE_BODY + "\n```python\nx = 1\ny = 2\n```\n"
    skill_dir = make_skill("deploy", body=body)
    _, _, plan = _plan(skill_dir, Bucket.REPLACE.value)

    assert plan.dominant_lang == "bash"
    assert any("non-dominant language: python" in r for r in plan.skipped_blocks)
    scripts = convert.render_scripts(plan)
    assert "x = 1" not in scripts["deploy.sh"]


# ---------------------------------------------------------------------------
# WRAP
# ---------------------------------------------------------------------------


def test_wrap_per_language_helpers_with_steps(make_skill):
    skill_dir = make_skill("triage", body=WRAP_BODY)
    _, _, plan = _plan(skill_dir, Bucket.WRAP.value)

    assert plan.eligible
    scripts = convert.render_scripts(plan)
    assert set(scripts) == {"triage.sh", "triage.py"}

    sh = scripts["triage.sh"]
    assert sh.splitlines()[0] == "#!/usr/bin/env bash"
    assert "# --- step 1 ---" in sh
    assert "gh issue view 123" in sh

    py = scripts["triage.py"]
    assert py.splitlines()[0] == "#!/usr/bin/env python3"
    assert "# --- step 2 ---" in py
    assert 'print("scoring")' in py


def test_wrap_rewrite_preserves_prose_and_inserts_refs(make_skill):
    skill_dir = make_skill("triage", body=WRAP_BODY)
    disc, parsed, plan = _plan(skill_dir, Bucket.WRAP.value)
    original = (skill_dir / "SKILL.md").read_text(encoding="utf-8")

    rewritten = convert.rewrite_skill_md(plan, parsed, original)

    # Judgment prose survives.
    assert "weigh the options carefully" in rewritten
    assert "recommend a direction" in rewritten
    # Code segments are replaced by reference lines.
    assert "Run step 1 of `.converted/triage.sh`." in rewritten
    assert "Run step 2 of `.converted/triage.py`." in rewritten
    # Raw commands no longer inline in the prose.
    assert "gh issue view 123" not in rewritten
    assert 'print("scoring")' not in rewritten


def test_wrap_rewrite_survives_fence_marker_in_frontmatter(make_skill):
    # Regression: a ``` (or ~~~) line inside the frontmatter must not shift the
    # body-relative fence spans used by the WRAP rewrite. Before the fix this
    # silently ate the closing --- and all prose.
    fm = "name: x\nexample: |\n  ```\n  not real code\n  ```\n"
    body = "# Heading\n\nDecide carefully.\n\n```bash\ngit status\n```\n\nThen finish.\n"
    skill_dir = make_skill("frontmatter-fence", frontmatter=fm, body=body)
    disc, parsed, plan = _plan(skill_dir, Bucket.WRAP.value)
    original = (skill_dir / "SKILL.md").read_text(encoding="utf-8")

    rewritten = convert.rewrite_skill_md(plan, parsed, original)

    # Frontmatter preserved verbatim (both fences + the embedded ``` example).
    assert rewritten.startswith("---\n" + fm + "---\n")
    # Body prose and heading survive; the code segment becomes a reference.
    assert "# Heading" in rewritten
    assert "Decide carefully." in rewritten
    assert "Then finish." in rewritten
    assert "Run step 1 of `.converted/frontmatter-fence.sh`." in rewritten
    assert "git status" not in rewritten.split("---\n", 2)[-1]


def test_shell_session_alias_extracted_and_stripped(make_skill):
    body = (
        "# Run\n\nDo the thing.\n\n"
        "```shell-session\n$ make build\nbuilding...\n$ make test\nok\n```\n"
    )
    skill_dir = make_skill("sess", frontmatter="name: sess\n", body=body)
    _, _, plan = _plan(skill_dir, Bucket.REPLACE.value)

    assert plan.eligible
    assert plan.dominant_lang == "bash"
    text = convert.render_scripts(plan)["sess.sh"]
    # Prompts stripped, output ("building...", "ok") dropped.
    assert "make build" in text and "make test" in text
    assert "building..." not in text and "ok" not in text


# ---------------------------------------------------------------------------
# Staging + plan.json
# ---------------------------------------------------------------------------


def test_stage_writes_converted_tree(make_skill):
    skill_dir = make_skill("deploy", frontmatter="name: deploy\n", body=REPLACE_BODY)
    disc, parsed, plan = _plan(skill_dir, Bucket.REPLACE.value)
    original = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    scripts = convert.render_scripts(plan)
    rewritten = convert.rewrite_skill_md(plan, parsed, original)

    result = convert.stage(plan, scripts, rewritten, disc.path)

    assert result.status == "converted"
    converted = skill_dir / ".converted"
    assert (converted / "deploy.sh").is_file()
    assert (converted / "SKILL.md").is_file()
    plan_json = converted / "plan.json"
    assert plan_json.is_file()

    record = json.loads(plan_json.read_text(encoding="utf-8"))
    assert record["bucket"] == "replace"
    assert record["dominant_lang"] == "bash"
    assert "source_sha256" in record
    # Real SKILL.md untouched by staging.
    assert (skill_dir / "SKILL.md").read_text(encoding="utf-8") == original


def test_second_run_unchanged(make_skill):
    skill_dir = make_skill("deploy", frontmatter="name: deploy\n", body=REPLACE_BODY)
    disc, parsed, plan = _plan(skill_dir, Bucket.REPLACE.value)
    original = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    scripts = convert.render_scripts(plan)
    rewritten = convert.rewrite_skill_md(plan, parsed, original)

    assert convert.is_unchanged(disc.path) is False
    convert.stage(plan, scripts, rewritten, disc.path)
    assert convert.is_unchanged(disc.path) is True

    # Editing SKILL.md invalidates the idempotency token.
    (skill_dir / "SKILL.md").write_text(original + "\nextra line\n", encoding="utf-8")
    assert convert.is_unchanged(disc.path) is False


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------


def test_apply_backs_up_then_overwrites(make_skill):
    skill_dir = make_skill("deploy", frontmatter="name: deploy\n", body=REPLACE_BODY)
    disc, parsed, plan = _plan(skill_dir, Bucket.REPLACE.value)
    original = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    scripts = convert.render_scripts(plan)
    rewritten = convert.rewrite_skill_md(plan, parsed, original)

    result = convert.stage(plan, scripts, rewritten, disc.path, lint_ok=True)
    result = convert.apply(result)

    assert result.applied is True
    assert result.status == "converted"
    # Backup holds the original.
    backup = skill_dir / ".converted" / ".backup" / "SKILL.md"
    assert backup.read_text(encoding="utf-8") == original
    # Real SKILL.md is now the stub.
    now = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    assert "This skill runs `.converted/deploy.sh`." in now
    # Script placed as a sibling of SKILL.md.
    assert (skill_dir / "deploy.sh").is_file()


def test_apply_refused_when_lint_fails(make_skill):
    skill_dir = make_skill("deploy", frontmatter="name: deploy\n", body=REPLACE_BODY)
    disc, parsed, plan = _plan(skill_dir, Bucket.REPLACE.value)
    original = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    scripts = convert.render_scripts(plan)
    rewritten = convert.rewrite_skill_md(plan, parsed, original)

    result = convert.stage(plan, scripts, rewritten, disc.path, lint_ok=False,
                           lint_output="bash -n failed")
    result = convert.apply(result)

    assert result.status == "unsafe"
    assert result.applied is False
    # Real SKILL.md untouched, no sibling script written.
    assert (skill_dir / "SKILL.md").read_text(encoding="utf-8") == original
    assert not (skill_dir / "deploy.sh").exists()


# ---------------------------------------------------------------------------
# Honesty guard
# ---------------------------------------------------------------------------


def test_json_only_skill_is_noop(make_skill):
    skill_dir = make_skill("config", frontmatter="name: config\n", body=JSON_ONLY_BODY)
    disc, parsed, plan = _plan(skill_dir, Bucket.REPLACE.value)

    assert plan.eligible is False
    assert plan.reason == "no deterministically-extractable executable code"
    assert plan.segments == []
    # Both non-executable blocks recorded.
    assert any("json" in r for r in plan.skipped_blocks)
    assert any("yaml" in r for r in plan.skipped_blocks)

    assert convert.render_scripts(plan) == {}

    result = convert.stage(plan, {}, "", disc.path)
    assert result.status == "noop"
    assert result.staged_dir == ""
    # Nothing written.
    assert not (skill_dir / ".converted").exists()


def test_noop_apply_is_inert(make_skill):
    skill_dir = make_skill("config", frontmatter="name: config\n", body=JSON_ONLY_BODY)
    disc, parsed, plan = _plan(skill_dir, Bucket.REPLACE.value)
    result = convert.stage(plan, {}, "", disc.path)
    out = convert.apply(result)
    assert out.status == "noop"
    assert out.applied is False
    assert not (skill_dir / ".converted").exists()
