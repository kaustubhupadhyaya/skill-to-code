# Usage & install

`skill-to-code` audits the Claude Code skills installed on a host, scores how
*programmable* each one is, and converts the deterministic ones into scripts the
skill calls — so the model stops re-deriving the same procedure on every run.

Runtime is **standard-library only** (no third-party deps). Requires **Python
3.11+**. Verified on Python 3.14, Windows 11.

## Install

Clone-and-run needs no install — invoke the module directly from the repo root:

```bash
python -m skill_to_code scan
```

To get the `skill-to-code` console script on your PATH, install editable:

```bash
pip install -e .
# then:
skill-to-code scan
```

Dev extras (pytest) for running the test suite:

```bash
pip install -e ".[dev]"
pytest
```

Throughout this doc `skill-to-code <cmd>` and `python -m skill_to_code <cmd>`
are interchangeable.

## Quickstart

```bash
# 1. Audit — ranked table of every skill + score + bucket + reason (read-only)
python -m skill_to_code scan

# 2. Convert — dry-run: stage scripts under <skill>/.converted/, touch nothing
python -m skill_to_code convert --bucket replace

# 3. Review one skill's rewrite as a unified diff
python -m skill_to_code convert --name my-skill --diff

# 4. Apply — write through (backs up the original, gated by lint)
python -m skill_to_code convert --name my-skill --apply

# 5. Apply with an opt-in smoke test of the generated script first
python -m skill_to_code convert --name my-skill --verify-run --apply
```

## How it discovers skills

A "skill" is any directory containing a `SKILL.md`. By default `scan` and
`convert` walk four sources:

| Source    | Location |
|-----------|----------|
| `user`    | `~/.claude/skills` |
| `plugin`  | `~/.claude/plugins` (nested; plugin name derived from the path) |
| `project` | `<proj>/.claude/skills` under the default project roots (`~/github`, `~/code`) |
| `include` | anything passed via `--include` / scoped via `--root` |

Discovery is read-only and dedupes by absolute path.

## `scan` — audit (read-only)

```
python -m skill_to_code scan [flags]
```

| Flag | Description |
|------|-------------|
| `--format {table,json,md}` | Output format (default: `table`). |
| `--bucket {replace,wrap,skip}` | Only show skills in this bucket. |
| `--min-score N` | Only show skills scoring `>= N`. |
| `--source {user,plugin,project,include}` | Only show skills from this source. |
| `--root PATH` | Scope the scan to `PATH`, **replacing** the default roots. Repeatable. |
| `--include PATH` | **Add** an extra skill dir or root on top of the defaults. Repeatable. |
| `--no-cache` | Bypass the discovery/analysis cache and rescan from disk. |

Filters combine (AND). `scan` never writes anything.

## `convert` — turn skills into scripts

```
python -m skill_to_code convert [flags]
```

Conversion is **LLM-free and verbatim**: it lifts fenced executable code
(`bash`/`sh`, `python`, `powershell`) that already exists in a `SKILL.md`. It
never interprets prose into code. A skill whose fences are all data
(`json`/`yaml`/untagged) is a **no-op**, never a garbage script.

By default `convert` is a **dry run**: it stages the generated script(s) and a
rewritten `SKILL.md` under `<skill>/.converted/` and leaves the real `SKILL.md`
untouched.

| Flag | Description |
|------|-------------|
| `--bucket {replace,wrap}` | Force the codegen strategy (default: the analyzer's per-skill bucket). |
| `--min-score N` | Only convert skills scoring `>= N`. |
| `--source {user,plugin,project,include}` | Only convert skills from this source. |
| `--name NAME` | Limit to specific skills by name. Repeatable. |
| `--root PATH` | Scope the run to `PATH`, **replacing** the default roots. Repeatable. |
| `--include PATH` | **Add** an extra skill dir or root on top of the defaults. Repeatable. |
| `--diff` | Print a unified diff of the original vs rewritten `SKILL.md` per skill. |
| `--apply` | Write through to the real `SKILL.md` (backup + lint gate). |
| `--verify-run` | Opt-in: **execute** each staged script once as a smoke test (see below). |
| `--verify-timeout SECONDS` | Per-script execution budget for `--verify-run` (default: `20`). |
| `--fixture DIR` | Seed the verify temp working directory by copying `DIR` into it. |
| `--no-cache` | Bypass the discovery/analysis cache and rescan from disk. |

### `replace` vs `wrap`

- **replace** — the skill is fully scriptable: collapse it into one script in
  the dominant language and rewrite `SKILL.md` to a short wrapper stub.
- **wrap** — the skill is mostly mechanical but keeps judgment prose: emit one
  helper script per language and replace each extracted code block in the
  `SKILL.md` with a reference line, leaving the prose intact.

### `--apply` safety gate

`--apply` writes through only after:

1. Backing up the original `SKILL.md` to `<skill>/.converted/.backup/SKILL.md`.
2. **Lint passing** on the staged script(s) — `py_compile` / `bash -n` /
   PowerShell parse. A lint failure marks the result `unsafe` and refuses the
   write. (A missing interpreter is a warn-not-block skip.)

Re-running `convert` on an unchanged `SKILL.md` is idempotent (`status=unchanged`).

### `--verify-run` (opt-in execution)

> **`--verify-run` executes the generated script.** Generated scripts are lifted
> verbatim from real skills and may do destructive things. It is a smoke test,
> **not** a security sandbox.

When passed, after lint passes each staged script is executed **once** in a fresh
`tempfile.mkdtemp()` working directory (optionally seeded with `--fixture DIR`),
with stdin closed and bounded by `--verify-timeout`. A non-zero exit or a timeout
marks the conversion **unsafe** and `--apply` refuses it — the staged
`.converted/` tree stays for inspection, the real `SKILL.md` is untouched. A
skip (interpreter absent) never blocks. See [verify.md](verify.md) for the full
safety contract and its honest limitations.

## Scoping the run: `--root` vs `--include`

- `--root PATH` **replaces** the default roots — the run sees only what is under
  the `PATH`(s) you pass. Use it to audit or convert a single project or
  directory in isolation.
- `--include PATH` is **additive** — it adds `PATH` on top of the default user /
  plugin / project sources.

Both are repeatable and can be combined (`--root` sets the base, `--include`
adds to it).

## Install the wrapper skill

`skill-to-code` also ships as a Claude Code skill (`SKILL.md` at the repo root)
that teaches the model which CLI command to run for a given request. Install it
so it is discoverable as a `user` skill:

```bash
# from the repo root
mkdir -p ~/.claude/skills/skill-to-code
cp SKILL.md ~/.claude/skills/skill-to-code/SKILL.md
```

PowerShell:

```powershell
New-Item -ItemType Directory -Force ~/.claude/skills/skill-to-code | Out-Null
Copy-Item SKILL.md ~/.claude/skills/skill-to-code/SKILL.md
```

The wrapper only invokes the CLI; keep the package importable (`pip install -e .`
or run from a checkout) so `python -m skill_to_code` resolves.
