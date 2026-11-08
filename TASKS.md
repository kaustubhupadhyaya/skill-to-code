# Skill-to-Code — Task Plan

> Companion to `PRD.md`. Tasks are grouped into epics that match the milestones in PRD §11. Each task lists: short description, acceptance criteria, dependencies, and rough effort (S = ≤ ½ day, M = 1–2 days, L = 3–5 days).

---

## Epic 1 — Discovery (M1)

| ID | Task | Acceptance | Dep | Effort |
|---|---|---|---|---|
| T1.1 | Walk `~/.claude/skills/` | Returns absolute paths to every `SKILL.md` under it | — | S |
| T1.2 | Walk `~/.claude/plugins/*/skills/` | Same, namespaced by plugin | T1.1 | S |
| T1.3 | Project-root walker | Recurses configurable roots and finds `<proj>/.claude/skills/` | T1.1 | M |
| T1.4 | `--include <path>` flag | Manual path picked up and merged with auto-scan results | T1.1 | S |
| T1.5 | `.skipconvert` exclusion | A skill dir containing this sentinel is dropped from results | T1.1 | S |
| T1.6 | Discovery cache | Results persist to `~/.claude/skill-to-code/discovery.json` with mtime check | T1.1 | M |

## Epic 2 — Analysis (M1)

| ID | Task | Acceptance | Dep | Effort |
|---|---|---|---|---|
| T2.1 | `SKILL.md` parser | Returns `{frontmatter, body, code_blocks, headings}` | — | S |
| T2.2 | Keyword classifier | Counts mechanical vs. judgment verbs against `rubric.yaml` | T2.1 | M |
| T2.3 | Code-block density signal | Ratio of fenced code lines to total lines | T2.1 | S |
| T2.4 | Sibling-script detector | Detects `.py / .ps1 / .sh / .js` in skill dir; weighted positively | T2.1 | S |
| T2.5 | Structured-IO detector | Looks for "input: …", "output: …" patterns | T2.1 | M |
| T2.6 | Weighted score combiner | Outputs `{score: 0-100, reasons: [str]}` | T2.2–T2.5 | M |
| T2.7 | Bucket assignment | Maps score → replace/wrap/skip using thresholds in `rubric.yaml` | T2.6 | S |
| T2.8 | Analysis cache | Persisted to `cache.json`, keyed by SKILL.md hash | T2.7 | M |

## Epic 3 — Reporting (M1)

| ID | Task | Acceptance | Dep | Effort |
|---|---|---|---|---|
| T3.1 | Tabular formatter | Renders ranked table to terminal | T2.7 | S |
| T3.2 | JSON formatter | Stable schema with one record per skill | T2.7 | S |
| T3.3 | Markdown report formatter | Shareable .md report with summary stats | T2.7 | S |
| T3.4 | Filter flags | `--bucket`, `--min-score`, `--source` work in combination | T3.1 | S |

## Epic 4 — Conversion (M2 + M3)

| ID | Task | Acceptance | Dep | Effort |
|---|---|---|---|---|
| T4.1 | Section splitter | Identifies contiguous mechanical blocks in `SKILL.md` | T2.1 | M |
| T4.2 | Wrap-bucket codegen | Generates helper script + rewrites `SKILL.md` to call it | T4.1 | L |
| T4.3 | Replace-bucket codegen | Full script + stub `SKILL.md` ("Run `helper.ps1`") | T4.1 | L |
| T4.4 | Staging to `.converted/` | All writes go to staging dir; original untouched | T4.2 | S |
| T4.5 | Backup originals | Original `SKILL.md` + siblings copied to `.converted/.backup/` | T4.4 | S |
| T4.6 | Diff renderer | Side-by-side diff of original vs. staged | T4.4 | M |
| T4.7 | Interactive apply/reject | Per-skill prompt with `apply / reject / diff / skip` | T4.6 | M |
| T4.8 | Idempotency guard | Re-running on converted skill is a no-op | T4.4 | S |

## Epic 5 — Verification (M4)

| ID | Task | Acceptance | Dep | Effort |
|---|---|---|---|---|
| T5.1 | Static lint of generated script | Fails staging if script has syntax errors | T4.2 | S |
| T5.2 | Sandboxed run | `--verify-run` executes script against a fixture in a temp dir | T4.2 | L |
| T5.3 | Rollback on verification fail | Conversion marked `unsafe`, not eligible for `--apply` | T5.2 | M |

## Epic 6 — Packaging (M5)

| ID | Task | Acceptance | Dep | Effort |
|---|---|---|---|---|
| T6.1 | CLI entrypoint | `skill-to-code scan / convert / apply` commands work | All | M |
| T6.2 | Wrap as Claude Code skill | `~/.claude/skills/skill-to-code/SKILL.md` invokes the CLI | T6.1 | S |
| T6.3 | Public README + install instructions | Anyone can clone and run | T6.1 | S |
| T6.4 | Example fixture set | 5 sample skills (mix of buckets) for regression testing | T2.7 | M |

---

## Critical path

`T1.1 → T2.1 → T2.6 → T2.7 → T3.1` ships M1 (read-only audit). Everything else builds on that foundation, so getting M1 right is the highest-leverage work.

## Suggested phasing

- **Week 1**: T1.1–T1.6, T2.1
- **Week 2**: T2.2–T2.8, T3.1
- **Week 3**: T3.2–T3.4, T6.4 (fixtures), M1 demo
- **Week 4–5**: Epic 4 (M2, M3)
- **Week 6**: Epic 5 (M4)
- **Week 7**: Epic 6 (M5)
