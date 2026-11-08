# skill-to-code

> Find every Claude Code skill on a host, score how *programmable* each one is, and convert the deterministic ones into scripts the skill calls — so the LLM stops re-deriving the same procedures on every invocation.

## Quickstart

Python 3.11+ and the standard library only. No install step is required.

```bash
git clone https://github.com/kaustubhupadhyaya/skill-to-code.git
cd skill-to-code
python -m skill_to_code --version
python demo/run_demo.py
```

`demo/run_demo.py` is the reproducible end-to-end check. It copies the bundled
example skills to a temp directory, scans them, stages a conversion, runs the
generated script through the opt-in verify gate, applies it to the copy, and
executes the resulting artifact. It never reads or writes your live `~/.claude`
skills. To install the console script instead: `pip install -e .`

## Progress

![progress](https://img.shields.io/badge/skill--to--code-100%25%20(v1)-brightgreen)

**100% of v1 scope** — Scan (M1), Convert (M2), Verify (M4), and Packaging (M5) all shipped, tested, and running on the real machine. _(scale 1:100, one cell = 1%)_

```
████████████████████████████████████████████████████████████████████████████████████████████████████
```

| Milestone | State |
|---|---|
| M1 — audit (discovery / analysis / reporting) | ✅ done |
| M2 — conversion (extract / codegen / stage / diff / apply) | ✅ done |
| M4 — verification (static lint + opt-in verify-run + rollback) | ✅ done |
| M5 — packaging (installable skill + docs + LICENSE) | ✅ done |
| Cache wiring + `--root` scoping | ✅ done |

**179 tests pass** (pytest, Python 3.14). Stdlib-only runtime, zero third-party deps, **no LLM anywhere** in the pipeline.

## What it does

```
pip install -e .                                      # or: python -m skill_to_code ...

skill-to-code scan                                    # ranked table: every skill + score + bucket + reason
skill-to-code scan --format json|md  --bucket replace --min-score 70 --source user
skill-to-code scan --root DIR                          # scope the scan to one root

skill-to-code convert --bucket replace                 # dry-run: stage scripts to <skill>/.converted/
skill-to-code convert --name X --diff                  # unified diff of original vs rewritten SKILL.md
skill-to-code convert --name X --verify-run            # opt-in: smoke-test the generated script (see safety note)
skill-to-code convert --name X --apply                 # write through (backup + lint [+ verify] gate)
```

Verified on this machine: real scan finds **502 skills** (replace=29 / wrap=208 / skip=265); real `convert` stages verbatim scripts touching **zero originals**; `--verify-run` executes a generated script in a throwaway temp dir and a non-zero exit **blocks `--apply`** (rollback), leaving the original SKILL.md byte-identical.

**Claim provenance:** the 502-skill scan, the 179-test run, and the field-campaign
numbers below come from the author's machines (see `docs/field-report.md`). The
published demo does not re-run that environment or the test suite; it verifies
the pipeline end to end on two bundled skills.

## How it works

1. **Discovery** (`discovery.py`) — walks `~/.claude/skills`, nested `~/.claude/plugins/**`, project `.claude/skills`, `--include`/`--root`; `.skipconvert`, path-dedup, symlink-safe.
2. **Parse** (`parser.py`) — SKILL.md → frontmatter + body + fenced blocks (with language tags) + sibling scripts.
3. **Analyze** (`analyzer.py` + [rubric.toml](rubric.toml)) — deterministic 0–100 score from 5 weighted signals − judgment penalty → replace / wrap / skip, with reasons.
4. **Report** (`report.py`) — table / JSON / Markdown, combinable filters.
5. **Extract** (`extract.py`) — lift *only* executable fences (bash/py/ps1) verbatim; json/yaml/untagged are never extracted.
6. **Codegen + stage** (`convert.py`) — replace (one concatenated script + stub) / wrap (per-language helper + prose preserved); stage to `.converted/` with `plan.json`, idempotency, backup, `--apply` gate.
7. **Lint** (`lint.py`) then optional **verify-run** (`verify.py`) — syntax-check, then opt-in execute-in-temp-dir smoke test; either failure blocks apply.

Full spec: [docs/conversion.md](docs/conversion.md), [docs/verify.md](docs/verify.md), [docs/USAGE.md](docs/USAGE.md). Architecture diagram: [docs/architecture.drawio](docs/architecture.drawio).

## Decisions locked (PRD §12)
Python, stdlib-only · rubric in TOML (`tomllib`, dep-free) · scan read-only · convert dry-run/stage by default, `--apply` gated by backup + lint + optional verify · **conversion is LLM-free** — verbatim code-fence extraction, never prose interpretation.

## Known limitations (honest v1 boundaries — candidates for v2)
- **`--verify-run` is a smoke test, not an OS sandbox.** It executes the generated script (lifted from a real skill) in a temp dir with a timeout — no filesystem/network isolation. Opt-in only; run it on scripts you trust.
- **Verbatim conversion doesn't judge intent.** A ```` ```bash ```` block is lifted as-is; the tool never rewrites or "understands" prose (by design — that's the LLM-free guarantee).
- **Discovery isn't cached** (only analysis is) — a full scan re-walks the filesystem (~14 s over ~500 skills) so added/removed/edited skills are always reflected; the analysis layer is cached (~2.5× warm).
- **`--include` is additive** (adds to default roots); use `--root` to scope a run.
- **Out of v1 scope:** interactive apply/reject TUI, third-party skill registries, multi-machine sync. Open question Q1 (primary success metric) resolved 2026-07-18 — see PRD §12 and [docs/field-report.md](docs/field-report.md).

## Progress log
- **2026-07-18** — **Field campaign complete** (local machine + WSL + open source, 45 real skills attempted one at a time): 24 staged cleanly, 3 verify-ok (all fixture-assisted), 3 applied end-to-end on scratch copies with median SKILL.md reduction **57.2%**; 11 lint-gate refusals (placeholders/pseudo-code in fences, as designed), 8 hit one reproducible Windows cp1252 lint crash (top v1.x fix), zero originals harmed. Q1 (primary success metric) resolved in PRD §12 from the measured data. Full ledger + bugs: [docs/field-report.md](docs/field-report.md). Bar stays 100% (v1).
- **2026-07-13** — **v1 complete.** Shipped M4 (opt-in `--verify-run` executing in a temp dir + rollback: a failing/timed-out run blocks `--apply`), M5 (installable `SKILL.md` wrapper + `docs/USAGE.md` + MIT LICENSE + console script), cache wiring (analysis layer ~2.5× warm), and `--root` scoping. 133 → **179 tests**. Live-verified verify-run pass/fail rollback with zero temp leaks and untouched originals. 3-lens adversarial review: 0 findings. Bar 75% → 100% (v1).
- **2026-07-13** — **M2 shipped: the converter.** extract/convert/lint/diff + `convert` CLI. Re-review caught + fixed a HIGH WRAP-frontmatter corruption bug (+ regression test). Bar 38% → 75%.
- **2026-06-30** — **M1 shipped.** Discovery + analysis + reporting, 72 tests, real scan finds 502 skills. Bar 3% → 38%.
- **2026-06-30** — README + progress tracker created.
- **2026-06-13** — PRD v0.1 and TASKS.md authored.

## Updating this bar
The milestone table is the source of truth. v1 scope is complete; further work is v2 (see Known limitations). Recompute `pct = round(100 × done / total)` against a v2 task list if one is opened.
