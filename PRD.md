# Skill-to-Code — Product Requirements Document

> Status: v1.0 · Owner: Kaustubh · Last updated: 2026-07-18 · **v1 scope complete — M1–M5 shipped** (see README) · 179 tests green · field-validated (see `docs/field-report.md`)

---

## 1. One-line pitch

Find every Claude Code skill on a host, score how *programmable* each one is, and convert the deterministic ones into scripts the skill calls — so the LLM stops re-deriving the same procedures every invocation.

## 2. Problem

Claude Code skills are prose instructions the LLM re-reads on every use. A large fraction of them describe **purely mechanical** work: `git` plumbing, file globbing, regex, API calls. Re-deriving these steps from prose burns tokens, introduces variability, and slows tasks that should be instant.

There is no existing tool to:

1. Discover all installed skills (user-level, plugin-level, project-level).
2. Decide whether each skill's logic is deterministic enough to be code.
3. Migrate the deterministic portions into scripts, leaving genuine judgment steps as prose.

## 3. Users & personas

| Persona | Goal | Pain today |
|---|---|---|
| **Power user** (40+ installed skills) | Reduce token cost, speed up routine tasks | No visibility into which skills are wasteful |
| **Skill author** | Harden a skill so it runs the same way every time | Has to manually decide what to extract |
| **Team lead** sharing skills across a fleet | Standardize and audit team skills | No batch tooling |

Primary persona for v1: **power user**.

## 4. Use cases

1. *"Audit every skill on my machine and tell me which ones are wasteful."*
2. *"Convert my `commit` skill to a pure shell script."*
3. *"Show me which steps of `mw-build-and-test` could be code, and extract them."*
4. *"I just installed a new plugin — rescan and tell me what's convertible."*

## 5. Functional requirements

### F1 — Discovery

- **F1.1** Walk well-known skill roots:
  - `~/.claude/skills/`
  - `~/.claude/plugins/*/skills/`
  - `<project>/.claude/skills/` for projects found under common dev roots (`~/github`, `~/code`, etc., configurable).
- **F1.2** Accept `--include <path>` to pick up skills the auto-scan missed.
- **F1.3** Honour a `.skipconvert` sentinel file inside any skill directory.
- **F1.4** Deduplicate by absolute path.
- **F1.5** Cache the discovery result so subsequent runs are fast.

### F2 — Analysis (programmability scoring)

- **F2.1** Parse each `SKILL.md` (YAML frontmatter + markdown body).
- **F2.2** Apply a rubric (see `rubric.yaml`) to compute a score 0–100.
- **F2.3** Assign a bucket:
  - **replace** (≥ replace_threshold) — fully scriptable
  - **wrap** (between thresholds) — mostly mechanical, some judgment
  - **skip** (< skip_threshold) — judgment-heavy, leave alone
- **F2.4** Emit human-readable reasoning per skill (which signals fired).
- **F2.5** Persist results to `~/.claude/skill-to-code/cache.json`.

### F3 — Reporting

- **F3.1** Default output: ranked table (skill name, score, bucket, top reason).
- **F3.2** `--format json` for piping.
- **F3.3** `--format md` for archiving to a report file.
- **F3.4** Filters: `--bucket replace`, `--min-score 70`, `--source plugin`.

### F4 — Conversion

- **F4.1** Stage all outputs to `<skill-dir>/.converted/` — **never** overwrite the original until the user accepts.
- **F4.2** For **replace** bucket: emit a standalone script and rewrite `SKILL.md` as a thin wrapper that calls it.
- **F4.3** For **wrap** bucket: extract mechanical blocks into a helper script and rewrite `SKILL.md` to call the helper between remaining judgment steps.
- **F4.4** Generated script language: see open question Q2.
- **F4.5** Idempotent — re-running on an already-converted skill is a no-op.
- **F4.6** Interactive `apply` / `reject` / `diff` per skill.

### F5 — Verification

- **F5.1** Lint / parse-check the generated script before staging.
- **F5.2** Optional `--verify-run` flag executes the script in a sandboxed shell against a fixture.
- **F5.3** On verification failure, mark the conversion `unsafe` and skip apply.

## 6. Non-functional requirements

| Property | Target |
|---|---|
| Discovery + analysis perf | < 30s for 50 skills on a warm cache |
| Cold-start perf | < 90s for a fresh scan of a typical dev machine |
| Safety | No in-place writes without `--apply` |
| Portability | Windows (primary), macOS, Linux |
| Privacy | Analysis is fully local; LLM-assisted scoring is opt-in |
| Reversibility | Every conversion produces a `.converted/.backup` of the original |

## 7. Success metrics

*Resolved via Q1 (2026-07-18, see §12):* primary metric is **median SKILL.md size reduction for applied conversions** (measured 57.2% in the field campaign) plus **conversion precision on replace-scored skills** (measured 7/19 stage cleanly, 1/19 convert + verify cleanly). See `docs/field-report.md` for the full measured ledger.

Secondary candidates (unmeasured, v2):

- Median token reduction per converted skill invocation
- Fraction of installed skills flagged as `replace` or `wrap`
- User-reported time saved per week
- Conversion acceptance rate (offered vs. applied)

## 8. Scope

**In scope (v1)**
- Local skill files with a `SKILL.md`
- Read-only analysis
- Wrap-bucket and replace-bucket conversion
- One generated-script language (decided by Q2)

**Out of scope (v1)**
- Converting MCP servers
- Converting entire plugins as a unit
- Publishing converted skills to a registry
- Multi-machine sync

## 9. Architecture

```
┌─────────────┐   ┌───────────┐   ┌──────────┐   ┌──────────┐
│  Discovery  │──▶│  Analyzer │──▶│ Reporter │──▶│ Converter│
│  (fs walk)  │   │  (rubric) │   │  (table) │   │ (codegen)│
└─────────────┘   └───────────┘   └──────────┘   └──────────┘
        │               │                              │
        ▼               ▼                              ▼
   discovery.json   cache.json                 <skill>/.converted/
```

Module boundaries:

- **discovery** — pure FS walker, no parsing
- **analyzer** — consumes a `SKILL.md` path, emits `{score, bucket, reasons[]}`
- **reporter** — formatters only, no logic
- **converter** — codegen + staging
- **verifier** — optional sandboxed run

## 10. Risks & mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| False positive: judgment skill marked replaceable | Medium | Two-stage confirm, show reasoning, stage to `.converted/` |
| Generated script drifts from intent | High | Self-verification step; user-facing diff |
| Walking a huge filesystem is slow | Medium | Skip lists; concurrent walk; cache |
| Skill format changes upstream | Low | Pluggable parser |
| Destructive write under `--apply` | Low | Always back up original to `.converted/.backup` |

## 11. Milestones

| ID | Milestone | Definition of done | Status |
|---|---|---|---|
| M1 | Discovery + analysis + report | `skill-to-code scan` prints a ranked table | ✅ done |
| M2 | Wrap-bucket conversion | `convert --bucket wrap` emits helper scripts | ✅ done |
| M3 | Replace-bucket conversion | `convert --bucket replace` emits standalone scripts | ✅ done |
| M4 | Verification + rollback | static lint + opt-in `--verify-run`; failure blocks `--apply` | ✅ done |
| M5 | Packaging | ships as an installable Claude Code skill (`SKILL.md` + `docs/USAGE.md` + LICENSE + console script) | ✅ done |

_v1 scope complete (179 tests green). M2 + M3 conversion is LLM-free/verbatim. See README "Known limitations" for v1 boundaries and v2 candidates._

## 12. Open questions

### Resolved (2026-06-30)
| ID | Question | Decision |
|---|---|---|
| Q2 | Generated-script / implementation language | **Python**, stdlib-only runtime. |
| — | Rubric config format | **TOML** via stdlib `tomllib` (chosen over YAML to avoid a dependency; `rubric.yaml` in prose → `rubric.toml` in code). |
| Q3 | Scoring weights | Seeded in `rubric.toml`: code_density 0.30, mechanical_ratio 0.25, sibling_scripts 0.15, structured_io 0.10, command_lines 0.20; judgment penalty 35; thresholds replace≥70 / skip<35. Tunable; revisit after real-corpus feedback. |
| Q4 | Safety posture | Scan is **read-only**. Conversion stages to `<skill>/.converted/` and is **dry-run by default**; writing the original requires `--apply`. |
| Q5 | LLM-assisted scoring | **No.** Analysis is fully deterministic. Conversion is also **LLM-free** — it lifts fenced code blocks and shell commands verbatim, it does not interpret prose into code. |

### Resolved (2026-07-18)
| ID | Question | Decision |
|---|---|---|
| Q1 | What is the primary success metric? | **Median SKILL.md size reduction for applied conversions + conversion precision**, both now measured on real skills (2026-07 field campaign, 45 attempts across local/WSL/OSS — see `docs/field-report.md`). Measured: median reduction for the 3 applied end-to-end conversions is **57.2%** (98.4% / 57.2% / 0.9%; median across all 24 staged conversions is 57.1%). Precision on replace-scored skills: **7/19 (36.8%) stage cleanly, 1/19 (5.3%) convert + verify cleanly** (verify-ok required the `--fixture` workflow; 6 of the 12 failures were tool bug #1, not skill properties). Token-reduction-per-invocation is deferred to v2 as a secondary metric — size reduction is its measurable proxy and was directly observable in the field. |

### Still open
_None._
