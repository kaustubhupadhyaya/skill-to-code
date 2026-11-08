---
name: skill-to-code
description: Audit and convert Claude Code skills into scripts. Use when the user wants to find every skill installed on this machine and score how programmable each one is, or convert a deterministic skill's procedure into a verbatim script the skill calls instead of re-deriving it on every invocation. Triggers on "audit my skills", "score my skills", "which skills can be scripted", "convert this skill to code", "skill-to-code".
---

# skill-to-code

A thin wrapper over the `skill_to_code` CLI. The CLI does all the work — this
skill only tells you which command to run for the task at hand. It is
deterministic and LLM-free: scanning is read-only, and conversion lifts fenced
code out of a `SKILL.md` verbatim (it never invents commands).

Run everything with the module entry point (works without installing):

```bash
python -m skill_to_code <command> [flags]
```

If the package is installed (`pip install -e .`), `skill-to-code <command>` is
equivalent.

## Audit skills (read-only)

Rank every skill on the host by a 0–100 programmability score and its bucket
(`replace` / `wrap` / `skip`):

```bash
python -m skill_to_code scan
```

Narrow the view with combinable filters, e.g. only fully-scriptable skills as
JSON:

```bash
python -m skill_to_code scan --bucket replace --format json
```

## Convert a skill into scripts

Conversion is a **dry run by default**: it stages generated scripts and a
rewritten `SKILL.md` under `<skill>/.converted/` and never touches the original.

Preview the whole replace bucket:

```bash
python -m skill_to_code convert --bucket replace
```

Inspect one skill's rewrite before committing to it:

```bash
python -m skill_to_code convert --name <skill-name> --diff
```

Write the conversion through to the real skill directory. `--apply` is gated: it
backs up the original `SKILL.md` first and refuses if the generated script fails
lint:

```bash
python -m skill_to_code convert --name <skill-name> --apply
```

## Optionally smoke-test generated code before applying

`--verify-run` is **opt-in and executes the generated script** in a throwaway
temp directory. A run failure marks the conversion unsafe so `--apply` refuses
it. Only use it on scripts you have read and trust:

```bash
python -m skill_to_code convert --name <skill-name> --verify-run --apply
```

## Guidance

- Start with `scan` to see the landscape before converting anything.
- Convert **one** skill by `--name` and review the `--diff` before you `--apply`.
- The `skip` bucket is intentional — those skills are judgment-heavy and should
  stay prose. Do not force them.
- Full flag reference and install steps live in [docs/USAGE.md](docs/USAGE.md).
