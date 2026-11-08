# Field report — real-world conversion campaign

> Date: 2026-07-18 · Tool: skill-to-code v1 (179 tests green at campaign start) · Host: Windows 11, Python 3.14, Git Bash lint/verify shell

## Methodology

Three phases — the user's local machine (`~/.claude` user + plugin skills), WSL (Ubuntu, canonical root `~/.claude/skills (WSL)` accessed over UNC), and open-source repos (fresh throwaway clones of high-star GitHub repos containing `SKILL.md` skills) — were each run through the same pipeline, one skill at a time with `--no-cache`: **scan** (score 0–100 → replace/wrap/skip bucket) → **convert** (dry-run stage to `<skill>/.converted/`) → **read-script safety gate** (every staged script was read in full and classified SAFE — reads/prints/temp-relative/fail-harmless only — or UNSAFE — installs, deletes, uploads, credential use, system mutation; UNSAFE scripts were never executed) → **`--verify-run`** on SAFE scripts only (empty temp dir first, then the documented `--fixture` workflow where a seeded fixture could make the script's assumptions true) → **`--apply`** only on throwaway copies/clones inside the scratch directory, never on a real installed skill. Verify failures (rc≠0) were recorded as data, not fixed. All `.converted/` staging created under real skill dirs was deleted at the end of each phase and confirmed gone (zero `.converted/` or `.backup/` anywhere real); no originals were modified; no commits were made; tool source was untouched — bugs were recorded with repros only.

## Full ledger (45 attempts across 3 phases)

Body chars = SKILL.md body before → after conversion; reduction % shown for `converted` rows. n/a = not measured (staging blocked or crashed before measurement was relevant).

### Phase 1 — Local machine (17 attempts)

| Skill | Origin | Score | Bucket | Status | Lang | Safety | Verify | Applied | Body before → after | Red. % | Notes |
|---|---|---|---|---|---|---|---|---|---|---|---|
| docker-patterns | user | 75 | replace | converted | bash | unsafe-skipped | skipped: `docker system prune` + `docker compose down -v` (system mutation) | no | 8598 → 361 | 95.8% | Clean stage; lifted docker-compose cheatsheet verbatim incl. destructive cleanup commands, so never executed |
| deployment-patterns | user | 74 | replace | unsafe | bash | not-run | skipped: lint failed (bash -n syntax error line 21: `railway up --commit <previous-sha>` placeholder) | no | 11306 → 366 | — | Lint gate correctly blocked `<placeholder>` tokens lifted verbatim from fences; apply refused as designed |
| ffmpeg-usage | user | 73 | replace | converted | bash | unsafe-skipped | skipped: begins with brew/apt/choco install commands (installer) | no | 11599 → 244 | 97.9% | 124-line ffmpeg recipe script staged cleanly; best compression ratio of the phase |
| plugin-settings | plugin | 77 | replace | error | bash | not-run | skipped: CLI crashed in lint (bug #1) | no | 12097 → 516 | — | Staging succeeded, then UnicodeEncodeError → TimeoutExpired crash piping non-cp1252 script to `bash -n` |
| AgentDB Performance Optimization | plugin | 76 | replace | error | bash | not-run | skipped: CLI crashed in lint (bug #1, U+2705 emoji) | no | 12674 → 351 | — | Duplicate copy under ruflo/.agents deduped; converted the .claude copy only |
| agent-production-validator | plugin | 75 | replace | converted | bash | safe | **ok** | **yes (scratch)** | 12271 → 193 | 98.4% | grep-only script. Empty-cwd verify rc=2 (no src/) as expected; `--fixture`-seeded run ok rc=0 in 0.2s; full `--apply` on scratch copy: backup==original, stub written, script installed as sibling |
| AgentDB Memory Patterns | plugin | 75 | replace | unsafe | bash | not-run | skipped: lint failed (`npx agentdb@latest plugin-info <name>` placeholder, line 65) | no | 9031 → 340 | — | Lint gate blocked placeholder tokens; correct behavior |
| AgentDB Vector Search | plugin | 75 | replace | error | bash | not-run | skipped: CLI crashed in lint (bug #1, U+2705) | no | 9302 → 327 | — | Same crash signature as plugin-settings |
| V3 Performance Optimization | plugin | 75 | replace | unsafe | bash | not-run | skipped: lint failed (JS pseudo-code `Task("Performance baseline",...)` in bash fence) | no | 11828 → 310 | — | Skill author put Claude-orchestration pseudo-code in bash fences; lint gate caught it |
| Verification & Quality Assurance | plugin | 75 | replace | error | bash | not-run | skipped: CLI crashed in lint (bug #1, U+274C) | no | 20344 → 301 | — | Largest crash case (7261-byte staged script) |
| worker-integration | plugin | 75 | replace | converted | bash | unsafe-skipped | skipped: `npx agentic-flow` would download+execute a third-party npm package | no | 3909 → 199 | 94.9% | Clean stage; smallest skill in the set |
| agent-sync-coordinator | plugin | 71 | replace | unsafe | bash | not-run | skipped: lint failed (stray `}` line 19 — JS object literal in bash fence) | no | 16667 → 177 | — | Lint gate blocked non-bash content; correct behavior |
| Skill Builder | plugin | 71 | replace | error | bash | not-run | skipped: CLI crashed in lint (bug #1) | no | 22811 → 361 | — | Same crash signature |
| V3 MCP Optimization | plugin | 71 | replace | unsafe | bash | not-run | skipped: lint failed (`Task(...)` pseudo-code in bash fence) | no | 22204 → 312 | — | Lint gate blocked; correct behavior |
| build-mcpb | plugin | 70 | replace | converted | bash | unsafe-skipped | skipped: `npm install` + `pip install -t` (installer) | no | 7867 → 501 | 93.6% | Clean stage from official claude-plugins mcp-server-dev plugin |
| tdd-workflow | user | 69 | wrap | converted | bash | safe | fail:127 | no | 9775 → 3031 | 69.0% | WRAP path exercised; npm-only script safe to run; rc=127 — npm not resolved in verify's spawned bash despite interactive PATH; gate correctly flipped result to unsafe |
| e2e-testing | user | 64 | wrap | converted | bash | unsafe-skipped | skipped: `npx playwright` can auto-download the package | no | 8053 → 7973 | 1.0% | WRAP kept prose intact, replaced only the 2-line code fence with a step reference |

### Phase 2 — WSL (Ubuntu, 13 attempts)

| Skill | Origin | Score | Bucket | Status | Lang | Safety | Verify | Applied | Body before → after | Red. % | Notes |
|---|---|---|---|---|---|---|---|---|---|---|---|
| docker-patterns | wsl | 75 | replace | converted | bash | unsafe-skipped | skipped: `docker system prune` + `docker compose down -v` | no | 8598 → 361 | 95.8% | Clean replace-bucket collapse to one script; stub keeps frontmatter verbatim |
| deployment-patterns | wsl | 74 | replace | unsafe | bash | not-run | skipped: lint gate (bash -n failed on `<previous-sha>` placeholder) | no | 11306 → 366 | — | Lint gate correctly rejected doc-style placeholder syntax; apply refused as designed |
| tdd-workflow | wsl | 69 | wrap | converted | bash | safe | fail:127 | no | 9775 → 3031 | 69.0% | Script is only npm test/run lines; rc=127 npm not on verify PATH; also surfaced the .converted-rediscovery dup-row bug (#3) |
| e2e-testing | wsl | 64 | wrap | converted | bash | unsafe-skipped | skipped: `npx playwright` can auto-download/install | no | 8053 → 7973 | 1.0% | Wrap kept prose, tiny 2-line helper; smallest body reduction of the set |
| local-batch-test | wsl | 59 | wrap | unsafe | bash | not-run | skipped: lint gate (placeholders); script also runs `aws configure` + `docker exec` against a real company container | no | 18806 → 2325 | — | Company batch-test skill; would be unsafe-skipped even if lint passed (creds + container mutation) |
| verification-loop | wsl | 56 | wrap | converted | bash | safe | fail:127 | no | 2479 → 1405 | 43.3% | `set -euo pipefail` guarantees exit at first npm line before `npx tsc` could fetch; rest read-only grep/git |
| release | wsl | 55 | wrap | converted | bash | unsafe-skipped | skipped: credentialed Bitbucket curl DELETE/POST, git pull, ssh to a production host | no | 12902 → 3496 | 72.9% | Prod release skill lifted verbatim incl. `$BITBUCKET_TOKEN` usage — textbook unsafe |
| mw-build-and-test | wsl | 50 | wrap | converted | bash+powershell | unsafe-skipped | skipped: .sh scp-uploads jar + sudo jboss restart on a dev app server; .ps1 runs real Ant build in user workspace | no | 5059 → 2637 | 47.9% | Multi-language wrap correctly emitted two scripts (.ps1 + .sh) |
| transcribe | wsl | 37 | wrap | converted | bash | safe | fail:127 | no | 551 → 519 | 5.8% | transcribe binary lives in WSL ~/bin, absent on Windows verify host — expected env miss |
| rcs-preflight | wsl | 40 | wrap | error | bash | not-run | skipped: CLI crashed during lint (unhandled TimeoutExpired, bug #1, U+2192 arrow) | no | 7506 → 4264 | — | Reproducible crash: U+2192 in lifted comment vs cp1252 stdin encoding; script staged fine before crash |
| python-patterns | wsl | 51 | wrap | unsafe | python | not-run | skipped: lint gate (py_compile failed on concatenated doc snippets) | no | 9467 → 842 | — | Doc-example fences do not form a valid module; py_compile gate blocked honestly |
| drawio | wsl | 40 | wrap | converted | python+bash | unsafe-skipped | skipped: .sh runs `pip install defusedxml`; .py writes `~/work/diagram.drawio` (real home path) | no | 8661 → 5435 | 37.2% | 3 segments to 2 scripts; drawio.py contains U+2192 but py_compile lints by path, so no cp1252 crash |
| answerinshort | wsl | 18 | skip | noop | none | not-run | skipped: noop, nothing staged | no | 3087 → 3087 | — | Honesty guard: zero executable fences → noop, stages nothing |

### Phase 3 — Open source (throwaway clones, 15 attempts)

| Skill | Origin | Stars | Score | Bucket | Status | Lang | Safety | Verify | Applied | Body before → after | Red. % | Notes |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| finishing-a-development-branch | oss:obra/superpowers | 256,641 | 57 | wrap | unsafe | bash | not-run | skipped: lint failed | no | n/a | — | `<base-branch>`/`<feature-branch>` placeholders; bash -n syntax error; lint gate correctly refused |
| writing-skills | oss:obra/superpowers | 256,641 | 56 | wrap | error | bash | not-run | skipped: tool crash | no | n/a | — | CLI crashed with unhandled TimeoutExpired — cp1252 lint bug (#1) triggered by U+274C; re-run reports "unchanged" without ever linting (lint bypass) |
| test-driven-development | oss:obra/superpowers | 256,641 | 45 | wrap | converted | bash | safe | **ok** | **yes (scratch)** | 9752 → 4175 | 57.2% | FULL SUCCESS with npm package.json fixture (rc=0, 4.1s); no-fixture run failed rc=127 as expected; rewrite exposed bug #2 (prose between merged bash blocks deleted) |
| writing-plans | oss:obra/superpowers | 256,641 | 45 | wrap | converted | bash+python | safe | **ok** | **yes (scratch)** | 6943 → 6879 | 0.9% | FULL SUCCESS with git-repo fixture (rc=0, 0.3s); cleanest conversion seen — both code blocks replaced in place, prose fully intact; no-fixture run failed rc=128 as expected |
| systematic-debugging | oss:obra/superpowers | 256,641 | 43 | wrap | converted | bash | safe | fail:127 | no | 9720 → 9294 | 4.4% | Staged ok; verify fail expected — script needs macOS security/codesign; gate correctly blocked apply |
| using-git-worktrees | oss:obra/superpowers | 256,641 | 43 | wrap | converted | bash | safe | fail:128 | no | 7221 → 3169 | 56.1% | Staged ok; `git rev-parse` in non-repo temp dir; `set -euo pipefail` killed it early as predicted |
| verification-before-completion | oss:obra/superpowers | 256,641 | 40 | wrap | noop | — | not-run | skipped: noop | no | n/a | — | Honesty guard: zero extractable executable blocks despite wrap score; correct no-op |
| pdf | oss:anthropics/skills | 162,129 | 80 | replace | converted | python | safe | fail:1 | no | 7512 → 37 | 99.5% | Only replace-bucket skill in repo; 5 segments collapsed to pdf.py; verify fail expected (pypdf/document.pdf absent); best compression of the campaign |
| docx | oss:anthropics/skills | 162,129 | 51 | wrap | converted | bash | safe | fail:2 | no | 5944 → 2559 | 56.9% | All paths cwd-relative; verify fail expected (scripts/office/soffice.py absent); gate blocked apply |
| webapp-testing | oss:anthropics/skills | 162,129 | 51 | wrap | unsafe | python+bash | not-run | skipped: lint failed | no | n/a | — | py_compile IndentationError line 13 — concatenated python fences from separate examples don't align; lint gate correctly refused |
| skill-creator | oss:anthropics/skills | 162,129 | 44 | wrap | unsafe | bash | not-run | skipped: lint failed | no | n/a | — | `<workspace>`/`<name>` placeholders in fenced bash; bash -n syntax error; gate correctly refused |
| pptx | oss:anthropics/skills | 162,129 | 42 | wrap | converted | bash | safe | fail:1 | no | 19820 → 7200 | 63.7% | Relative paths only; verify fail expected (deck.pptx absent); gate blocked apply |
| claude-api | oss:anthropics/skills | 162,129 | 37 | wrap | converted | python | safe | fail:1 | no | 72143 → 65151 | 9.7% | Script is API snippets referencing undefined `client` — instant NameError, no network possible; verify fail expected |
| Plugin Settings | oss:anthropics/claude-code | 138,118 | 77 | replace | error | bash | not-run | skipped: tool crash | no | n/a | — | Second repro of bug #1 — staged script contains U+26A0; cp1252 stdin lint crash |
| Hook Development | oss:anthropics/claude-code | 138,118 | 60 | wrap | converted | bash | safe | fail:1 | no | 15634 → 8138 | 47.9% | `set -u` kills at unbound `$CLAUDE_ENV_FILE` line 5 before anything runs; stdin closed so cat can't hang; verify fail expected |

## OSS star findings (honest numbers vs. the ≥50k ask)

The ≥50k-star bar **is** met by real `SKILL.md` repos. GitHub API stars checked 2026-07-18:

| Repo | Stars | SKILL.md skills | Note |
|---|---|---|---|
| obra/superpowers | 256,641 | 14 | Attempted 7 |
| anthropics/skills | 162,129 | 18 | Attempted 6 |
| anthropics/claude-code | 138,118 | 10 | Attempted 2 |
| hesreallyhim/awesome-claude-code | 50,261 | **0** | Link list only — no convertible skills despite clearing the bar |
| ohmyzsh/ohmyzsh (control) | 188,667 | 0 | Huge repo, no SKILL.md-format skills |
| x1xhlol/system-prompts (control) | 142,028 | 0 | Same |

Below-bar candidates not cloned: wshobson/agents 37,988; davila7/claude-code-templates 29,680; VoltAgent/awesome-claude-code-subagents 23,448.

Scan distributions of the cloned repos: superpowers 0 replace / 7 wrap / 7 skip; anthropics/skills 1 replace (pdf, 80) / 5 wrap / 12 skip; claude-code 1 replace (Plugin Settings, 77) / 1 wrap / 8 skip.

## Tool bugs found (recorded, not fixed)

1. **BUG — Windows cp1252 lint crash (+ silent lint bypass on re-run).** `skill_to_code/lint.py` `_run()` pipes the staged script to `bash -n -` with `text=True` and no `encoding=`; on Windows the locale codec is cp1252, so any script containing non-cp1252 chars (U+2705, U+274C, U+26A0, U+2192 all observed) raises `UnicodeEncodeError` in the stdin writer thread, `bash -n` never sees EOF and blocks until the 30s timeout, and the resulting `subprocess.TimeoutExpired` escapes uncaught — the whole CLI dies with a traceback. Worse: staging happens before the crash, so the **next** run reports `unchanged` and the script is never actually linted. Hit **8 of 45** attempts (5 local, 1 WSL, 2 OSS) — the single highest-impact defect of the campaign. Repro: `skill-to-code convert --root <root> --name writing-skills --no-cache` on a fresh obra/superpowers clone (Windows, Python 3.14, Git Bash); minimal: `subprocess.run([bash,'-n','-'], input='# →\n', text=True, timeout=15)` → TimeoutExpired. Fix direction: `encoding='utf-8'` on the subprocess call + catch TimeoutExpired as a lint failure.
2. **BUG — wrap rewrite deletes prose between merged same-language blocks.** When executable blocks of the same language are separated by non-extractable content (prose, headings, other-language fences), they merge into one segment and the rewrite replaces the entire span from first to last block with a single "Run step N" line — deleting the judgment prose in between, contradicting the wrap contract in docs/conversion.md ("prose is left untouched"). Repro: convert superpowers/test-driven-development, diff `.converted/.backup/SKILL.md` vs `.converted/SKILL.md` — original lines 117–322 collapse to one line (9752 → 4175 chars for a 4-line bash segment). Same pattern in docx, pptx, using-git-worktrees, hook-development.
3. **BUG — discovery re-detects `.converted/SKILL.md` as a separate skill.** After staging, re-running `convert --name <skill> --root <root> --no-cache` prints two rows for the same name (real skill `unchanged` + staged stub `noop`, 0 segments). Discovery should exclude `.converted/` staging dirs. Repro: run the same convert twice in a row (seen on WSL tdd-workflow). Audit re-verification (2026-07-18) found a worse post-apply variant: re-running convert on an applied skill also discovers `.converted/.backup/SKILL.md` and **converts the backup** as a skill named "backup", creating nested `.converted/.backup/.converted/` staging (live SKILL.md and backup remain untouched, so apply itself stays idempotent-in-effect, but no `unchanged` row is printed post-apply — the rewritten live skill reports `noop`).
4. **MINOR — post-apply stub path inconsistency.** After `--apply` the rewritten stub says "This skill runs `.converted/<script>`" but apply installs the script as a **sibling** of SKILL.md per spec; the stub reference only resolves because the staged copy is also left behind — cleaning `.converted/` would break it while the canonical sibling copy sits unreferenced.
5. **OBSERVATION (env, docs-worthy).** verify-run's spawned bash returned rc=127 for `npm` even though npm resolves in interactive Git Bash — PATH inheritance nuance in the subprocess env makes node-based skills unverifiable on this host without a fixture-side shim.

## Findings

**Headline counts (45 attempts):**

| Metric | Count | Rate |
|---|---|---|
| Attempted | 45 (17 local, 13 WSL, 15 OSS) | — |
| Converted (staged cleanly) | 24 | 53% |
| Verify ok (rc=0) | 3 | 7% of attempts; 21% of the 14 SAFE-classified scripts |
| Applied (scratch copies only) | 3 | all 3 verify-ok conversions |
| Lint-gate refusals (status `unsafe`) | 11 | 24% |
| Tool crashes (bug #1) | 8 | 18% |
| Honest no-ops | 2 | 4% |

Safety triage of the 24 staged scripts: 14 SAFE (read-only / fail-harmless / relative-path-only), 10 UNSAFE-skipped (installers, npx auto-fetch, docker prune, credentialed API calls, scp+sudo, writes to real home paths) — never executed.

**Dominant failure causes, in order of impact:**

1. **Tool bug #1 (cp1252 lint crash)** — 8/45 attempts (38% of the 21 non-conversions). Not a property of the skills; a fixable defect.
2. **Illustrative, non-executable fence content** — 11/45 blocked by the lint gate exactly as designed: `<placeholder>` tokens, JS/orchestration pseudo-code inside bash fences, doc snippets that don't concatenate into a valid module. Real-world skill fences are frequently documentation, not programs.
3. **Environment mismatch at verify time** — 11 of 14 SAFE verify-runs failed (rc=127/128/1/2: missing npm, not-a-git-repo, absent input files, macOS-only tools, WSL-only binaries). All 3 verify-ok results required the documented `--fixture` workflow to seed the script's assumptions; an empty temp dir passed nothing. Every failure correctly flipped the result to `unsafe` and blocked `--apply` — the gate never let a bad script through.

**Replace-bucket precision (the tool's real-world precision on its strongest claim):**

- 19 replace-scored skills attempted (15 local, 2 WSL, 2 OSS).
- **7/19 (36.8%) staged cleanly** (status `converted`).
- **1/19 (5.3%) converted + verified cleanly** (agent-production-validator, and only via a seeded fixture).
- The 12 failures split evenly: 6 tool crashes (bug #1) and 6 lint-gate refusals. Caveat: the 6 crashed skills all staged before crashing, so with bug #1 fixed, staging precision could reach up to 13/19 (68%) — but those scripts were never linted, so that ceiling is unproven.
- For contrast, wrap bucket: 17/25 (68%) staged cleanly, 2/25 verify-ok — wrap's smaller lifted segments stage more reliably, but replace produces far larger reductions.

**Measured size reduction (per success):** all 24 conversions measured; see Red. % columns above. Applied (end-to-end) successes:

| Skill | Before | After | Reduction |
|---|---|---|---|
| agent-production-validator (local plugin) | 12,271 | 193 | 98.4% |
| test-driven-development (oss:superpowers) | 9,752 | 4,175 | 57.2% |
| writing-plans (oss:superpowers) | 6,943 | 6,879 | 0.9% |

- **Median reduction across the 3 applied conversions: 57.2%.**
- Median reduction across all 24 staged conversions: 57.1%.
- Replace-bucket conversions cluster at 93.6–99.5% reduction (cheatsheet skills collapse to a stub); wrap-bucket spans 0.9–72.9% depending on how much prose survives (bug #2 inflates some wrap reductions by wrongly deleting prose — e.g. test-driven-development's 57.2% includes deleted judgment text).

**Bottom line:** the pipeline's safety architecture (stage-only default, lint gate, verify gate, backup-before-apply) behaved correctly on every one of 45 real skills — zero originals harmed, zero bad scripts applied. Genuine end-to-end conversion works (3 proven, fixture-assisted). The measured limits: most real skill fences are illustrative rather than executable, verify needs fixtures to pass, and one Windows encoding bug (bug #1) cost 18% of all attempts and should be the first v1.x fix.
