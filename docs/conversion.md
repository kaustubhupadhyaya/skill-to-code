# Conversion (M2): turning programmable skills into scripts

This is the spec the conversion modules code against. It is the authority; the
dataclasses in `skill_to_code/types.py` are the wire contract. Read both.

Conversion is **LLM-free and verbatim.** It lifts fenced code that already
exists in a `SKILL.md` and rewrites the skill around it. It **never** interprets
prose into code, and it **never** invents commands. If the code isn't already in
a fenced block, conversion does not produce it.

Conversion is **read-only by default.** Everything stages to
`<skill_dir>/.converted/`. The real `SKILL.md` is only touched by `--apply`, and
only after a backup exists and lint has passed.

## Pipeline

```
parse_skill(discovered)      -> ParsedSkill      (existing; code_blocks + code_langs, index-aligned)
extract(parsed)              -> list[CodeSegment]  (extract.py)
plan(...)                    -> ConversionPlan     (convert.py)
stage(plan) / apply(plan)    -> ConversionResult   (convert.py; lint.py gates apply)
```

Module ownership (each a distinct file — no collisions):

| Concern                | File                        |
|------------------------|-----------------------------|
| extraction             | `skill_to_code/extract.py`  |
| codegen + staging/apply| `skill_to_code/convert.py`  |
| SKILL.md diffing       | `skill_to_code/diffing.py`  |
| static lint (F5.1)     | `skill_to_code/lint.py`     |
| CLI subcommand         | `skill_to_code/cli.py`      |
| data contract          | `skill_to_code/types.py`    |

## Executable-language map

Only **executable** fenced blocks are extractable. Normalize the fence info
string to its leading token, lowercased (reuse the analyzer's `_first_lang`
approach: `lang.strip().split()[0].lower()`), then map:

| Normalized fence tokens                 | Target | Extension | Header lines emitted                          |
|-----------------------------------------|--------|-----------|-----------------------------------------------|
| `bash`, `sh`, `shell`, `console`, `zsh` | shell  | `.sh`     | `#!/usr/bin/env bash` then `set -euo pipefail` |
| `powershell`, `pwsh`, `ps1`, `ps`       | pwsh   | `.ps1`    | (none)                                        |
| `python`, `py`, `python3`               | python | `.py`     | `#!/usr/bin/env python3`                       |

`lang` on a `CodeSegment` is the **normalized target language** — canonicalize
aliases to one of `bash` / `powershell` / `python` so downstream codegen and the
dominant-language tally don't have to re-alias. `ext` is the matching extension.

**Never extractable** (these are data / examples / prose, not procedures):
`json`, `yaml`, `yml`, `toml`, `text`, `txt`, `md`, `markdown`, `html`, `xml`,
`diff`, `output`, `log`, **untagged blocks** (empty fence info string), and **any
unknown tag**. Blocks in these languages are recorded in
`ConversionPlan.skipped_blocks` with a reason, never emitted.

## Extraction

Input is a `ParsedSkill` from `parser.parse_skill`. It already gives us
`code_blocks` and `code_langs`, aligned by index. Extraction:

1. Walk blocks in document order. For each, normalize the lang. Keep only
   executable ones; append a skip reason for the rest (e.g.
   `"non-executable language: json"`, `"untagged block"`, `"unknown tag: mermaid"`).
2. Prompt-strip each kept block (below).
3. Group into segments (below).

### Prompt stripping (shell / console)

Only applied to shell-family blocks (`bash`/`sh`/`shell`/`console`/`zsh`).
Recognized prompt prefixes on a line (after leading whitespace):

- `$ `
- `> `
- `PS> `
- `PS C:\...> ` — a `PS` drive-path prompt, i.e. `PS`, optional path, then `> `.

Heuristic, applied **per block**:

- If the block contains **any** prompt-prefixed line: keep only the
  prompt-prefixed lines and strip their prompts. The non-prompt lines are
  command **output** (e.g. what `git status` printed) and are dropped.
- If the block contains **no** prompt-prefixed line: it's a raw command block —
  keep every line verbatim, strip nothing.

Blank lines inside a kept block are preserved as-is.

Python and PowerShell blocks are **not** prompt-stripped — a leading `>` in
Python is an operator, and `PS>` transcripts are rare; treat those blocks as raw
command bodies and keep every line. (PowerShell `PS>` transcript stripping is a
deliberate non-goal for M2.)

> **Known limitation.** The output-vs-command split is a prompt heuristic, not a
> shell parser. A console block that mixes prompts and commands *without*
> prompting every command line will lose the unprompted commands. A block with
> no prompts is assumed to be all commands. Documented here so the behavior is
> predictable rather than surprising.
>
> In particular, `> ` is a recognized prompt but is also shell redirect /
> line-continuation / heredoc-body syntax. A shell-family block that uses `> `
> at the start of a continuation or heredoc line will have those lines treated
> as prompted commands (and any truly unprompted siblings dropped). If this
> bites a real skill, tag the block ```` ```bash ```` (a raw command block that
> genuinely has no `$ ` prompts is kept verbatim) rather than ```` ```console ````.

### Segment definition

A **segment** is a *maximal run of consecutive extractable blocks of the same
normalized language, with no intervening extractable block of a different
language.* Document order is preserved.

- Non-extractable blocks do **not** break a run *(they are skipped, not
  emitted)* — but note that whether they break a run is moot for codegen: the
  REPLACE bucket concatenates by dominant language regardless, and WRAP emits per
  segment. Implement the strict reading: an extractable block of a *different*
  executable language ends the current segment and starts a new one; a
  non-extractable block is simply skipped and does not by itself split a run of
  same-language blocks around it. Keep it simple and deterministic; if unsure,
  splitting more (never merging across languages) is always safe.
- Each segment records `order` (0-based among the skill's segments) and
  `source_block_index` (the index of its **first** block in
  `ParsedSkill.code_blocks`), for audit and for locating the segment when
  rewriting `SKILL.md`.
- A segment whose lines are all empty after stripping is dropped (don't emit
  empty segments).

## Codegen

`script_name` defaults to the **skill directory basename**, sanitized: lowercase,
non-`[a-z0-9._-]` runs collapsed to `-`, leading/trailing `-`/`.` trimmed. Empty
result falls back to `skill`.

### REPLACE bucket

The skill is fully scriptable, so collapse it into one script.

1. Determine the **dominant language**: the executable language with the most
   total extracted lines across its segments. Ties broken by first appearance in
   document order.
2. Concatenate **all** segments of the dominant language, in document order, into
   one script `<script_name><ext>`. Emit the language header first (shebang, plus
   `set -euo pipefail` for shell), then a blank line, then the concatenated
   command lines. Separate consecutive segments with a single blank line.
3. Every segment **not** in the dominant language goes to `skipped_blocks` with
   reason `"non-dominant language: <lang>"`. Its code is not emitted.
4. Rewrite `SKILL.md` to a **stub**: keep the original YAML frontmatter
   **verbatim** (byte-for-byte between the `---` fences), and replace the body
   with a short wrapper note:
   `This skill runs \`.converted/<script>\`.`
   Preserve any trivially-present judgment prose headings if they carry
   instruction the script can't (optional; when in doubt keep the stub minimal).

### WRAP bucket

The skill is mostly mechanical but keeps judgment prose, so keep the prose and
factor the code out.

1. Emit **one helper script per language** present in the segments (e.g.
   `<script_name>.sh` and `<script_name>.py` if both occur). Within a script,
   write each of that language's segments as a labeled section:
   `# --- step N ---` (N is the segment `order` + 1), followed by the segment's
   lines. Language header first, as in REPLACE.
2. Rewrite `SKILL.md` keeping the prose intact, but **replace each extracted code
   segment in place** with a reference line:
   `Run step N of \`.converted/<script>\`.`
   Judgment prose (headings, explanation, Input/Output notes) is left untouched.

## Staging layout

Under `<skill_dir>/.converted/`:

```
.converted/
  <script_name>.<ext>      # the generated script(s)
  SKILL.md                 # the rewritten SKILL.md (stub or prose-with-refs)
  .backup/
    SKILL.md               # copy of the ORIGINAL, created before any --apply
  plan.json                # serialized ConversionPlan + source SKILL.md sha256
```

`plan.json` is `conversion_plan_to_dict(plan)` (see `types.py`) plus a top-level
`"source_sha256"` key holding the sha256 of the current `SKILL.md` bytes. It is
both an audit record and the idempotency token.

## Idempotency

Before staging, if `.converted/plan.json` exists and its stored
`source_sha256` equals the sha256 of the current `SKILL.md` bytes, the skill is
`unchanged`: do nothing, return a `ConversionResult` with `status="unchanged"`.
Any edit to `SKILL.md` changes the hash and forces a fresh plan.

## Apply (`--apply`) + backup

`--apply` writes the staged result through to the real skill directory:

1. Refuse if `lint_ok` is False — set `status="unsafe"`, do not write. Lint must
   pass first.
2. Ensure `.converted/.backup/SKILL.md` exists; if missing, copy the original
   `SKILL.md` there **before** overwriting anything. The backup is the original,
   never a rewritten copy.
3. Copy `.converted/SKILL.md` over the real `SKILL.md`, and place the generated
   script(s) as **siblings** of `SKILL.md` (in the skill dir, not inside
   `.converted/`).
4. Set `applied=True`, `status="converted"`.

An ineligible plan (honesty guard) is never applied — it has nothing to write.

## Lint (F5.1)

Lint runs on staged scripts **before** a plan is marked apply-eligible. It is
static only (no execution; sandboxed run F5.2 is out of scope for M2).

| Ext    | Check                                   | Tool absent                                            |
|--------|-----------------------------------------|--------------------------------------------------------|
| `.py`  | `python -m py_compile <file>`           | n/a (Python is always present)                         |
| `.sh`  | `bash -n <file>`                        | skip with warning: `lint_ok` stays True, note "bash unavailable" |
| `.ps1` | best-effort `[ScriptBlock]::Create(...)` parse via `powershell`/`pwsh` | skip with warning: `lint_ok` stays True, note "powershell unavailable" |

- Any lint **failure** (non-zero / parse error) sets `lint_ok=False`,
  `status="unsafe"`, and blocks `--apply`.
- A **skip-with-warning** (tool absent) leaves `lint_ok=True` and appends a note
  to `lint_output`. We warn rather than block so a Windows-only or Unix-only box
  can still convert; the skipped-language script simply wasn't statically
  verified.
- Use `subprocess` with the tool discovered via `shutil.which`. Never run the
  generated script; only parse/compile it.

## Honesty guard

Scoring (the analyzer) can rate a skill `replace` on **code density** even when
every code block is `json` / `yaml` / untagged — density counts lines in fences
regardless of language. Conversion must not be fooled by that.

**If a skill has zero extractable executable blocks, conversion is a no-op.**
Produce a `ConversionPlan` with `eligible=False`, empty `segments`, and
`reason="no deterministically-extractable executable code"`. Stage nothing, emit
no script, and surface `status="noop"` with that clear message. Never write an
empty or garbage script just because the density score said `replace`.

## CLI

`convert` subcommand (thin wrapper; logic lives in the modules above):

```
skill-to-code convert
    [--bucket replace|wrap]     # force codegen strategy; default: per-skill analyzer bucket
    [--min-score N]             # only convert skills scoring >= N
    [--source S]                # only skills from this source
    [--include PATH ...]        # extra skill dir/root (repeatable)
    [--name NAME ...]           # limit to specific skills by name (repeatable)
    [--diff]                    # print unified diff of original vs rewritten SKILL.md
    [--apply]                   # write through (backup + lint gate)
    [--no-cache]                # bypass discovery cache
```

Default is a **dry run**: discover -> analyze -> plan + stage conversions for
eligible skills, then print a summary table:

```
skill | bucket | #segments | script | status
```

- `--diff` prints a `difflib` unified diff (original `SKILL.md` vs rewritten) per
  converted skill.
- `--apply` writes through, honoring the backup + lint gate above.
- `--name` limits to skills whose `name` matches (repeatable).
- Skills filtered out before planning get `status="skipped"`; ineligible skills
  get `status="noop"`; unchanged skills get `status="unchanged"`.

## Data contract (see `types.py`)

- `CodeSegment(lang, ext, lines, order, source_block_index)`
- `ConversionPlan(skill, bucket, segments, dominant_lang, script_name, skipped_blocks, eligible, reason)`
- `ConversionResult(plan, staged_dir, script_paths, rewritten_skill_md, backup_path, lint_ok, lint_output, applied, status)`

Serialization helpers (JSON-safe, stable shape): `code_segment_to_dict`,
`discovered_skill_to_dict`, `conversion_plan_to_dict`. The plan writer adds the
top-level `"source_sha256"` to the `conversion_plan_to_dict` output when writing
`plan.json`.
