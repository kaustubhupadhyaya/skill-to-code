# Verify-run (M4): optional sandboxed smoke test + rollback

This is the spec the verify modules code against (Epic 5, F5.2 / T5.3). It is the
authority; the `VerifyResult` dataclass in `skill_to_code/types.py` is the wire
contract. Read both, plus `docs/conversion.md` for the surrounding apply flow.

Static lint (F5.1, `skill_to_code/lint.py`) already gates `--apply`: a script
that fails to **parse** is never written through. Verify-run adds the next rung:
optionally **execute** the generated script once, in isolation, and treat a
run-time failure the same way lint treats a parse failure.

> **Verify-run EXECUTES real code.** Generated scripts are lifted verbatim from
> real skills and may do destructive things (delete files, hit the network, spawn
> processes). Verify-run is therefore **strictly opt-in** and runs **only** in a
> throwaway temp directory. It is a smoke test, **not** a security sandbox.

## Safety contract (non-negotiable)

1. **Opt-in, never implied.** Verify only happens when the user passes
   `--verify-run`. It is never a default, never turned on by another flag. When
   used, the CLI prints a one-line warning up front that verify **executes** the
   generated scripts.
2. **Temp-dir-only execution.** Every run happens in a fresh
   `tempfile.mkdtemp()` working directory, optionally seeded by copying a
   `--fixture DIR` into it (`shutil.copytree`). The target script is copied into
   that temp dir and invoked there with `cwd=<tempdir>`. Nothing ever executes
   **inside or against a real skill directory**. The temp dir is removed
   (`shutil.rmtree`, best-effort) in a `finally` after the run.
3. **Bounded.** Every run is capped by `--verify-timeout` (default **20s**).
   `stdin` is closed (`subprocess.DEVNULL`). A timeout kills the process, sets
   `timed_out=True`, and counts as a **failure**.
4. **Correct interpreter, graceful skip.** Dispatch by the staged script's
   extension, discovering the interpreter with `shutil.which` (mirroring
   `lint.py`):

   | Ext    | Interpreter invocation                         | Interpreter absent            |
   |--------|------------------------------------------------|-------------------------------|
   | `.py`  | `python <file>` (use `sys.executable`)         | n/a — Python is always present |
   | `.sh`  | `bash <file>`                                  | skip: `ran=False`, reason set  |
   | `.ps1` | `powershell -NoProfile -File <file>` (or `pwsh`) | skip: `ran=False`, reason set |

   When the interpreter is absent, verify **skips gracefully**: `ran=False`,
   `ok=False`, `skipped_reason="interpreter not found: <name>"`. A skip is
   **not** a failure and must **not** block `--apply` — same warn-not-block
   philosophy as lint's tool-absent case.
5. **Capture everything.** Record `returncode`, `stdout`, `stderr`,
   `duration_s` (wall clock via `time.monotonic()`), and `timed_out`.
6. **`ok` definition.** `ok = (ran and returncode == 0 and not timed_out)`.
7. **Rollback on failure.** A verify **failure** (`ran and not ok` — nonzero
   exit or timeout) marks the conversion **unsafe** so `apply()` refuses it,
   exactly like a lint failure. An unverified-failing conversion is **never**
   applied: the staged `.converted/` tree remains for inspection, the real
   `SKILL.md` is untouched. See "Apply-gate wiring" below.

## Honest limitation

Verify-run is a **smoke test, not an OS security sandbox.** There is **no
filesystem isolation and no network isolation** — a run happens in a temp
working directory, but a script that writes an absolute path, deletes something
outside `cwd`, or makes a network call can still do so. The temp dir bounds
*relative* file operations and gives the script a clean CWD; it does **not**
contain a determined or malicious script. Only run `--verify-run` on scripts you
have read and trust. This limitation is intentional and documented rather than
papered over.

## Data contract (`types.py`)

```
VerifyResult(
    ran: bool,               # interpreter actually executed the script
    ok: bool,                # ran and returncode == 0 and not timed_out
    returncode: int | None,  # exit code; None when it never ran
    stdout: str,
    stderr: str,
    duration_s: float,       # wall-clock seconds
    timed_out: bool,         # killed by the timeout budget
    skipped_reason: str,     # why it didn't run (interpreter absent, etc.)
    interpreter: str,        # "python" / "bash" / "powershell"
)
```

All fields default to the "did not run" state (`ran=False`, `ok=False`,
`returncode=None`), so constructing a skip is a one-liner:
`VerifyResult(skipped_reason="interpreter not found: bash", interpreter="bash")`.
Serialize with `verify_result_to_dict(vr)` (stable, JSON-safe — same style as
`conversion_plan_to_dict`).

## Module shape (verify.py, owned by the verify agent)

Verify operates on **one already-staged script** and returns **one**
`VerifyResult` (the data contract is single-valued: one `interpreter`, one
`returncode`). Recommended surface:

```
verify_script(
    script_path: str,
    *,
    fixture: str | None = None,     # dir copied into the temp cwd before running
    timeout_s: float = 20.0,
) -> VerifyResult
```

- Discover the interpreter by extension via `shutil.which`; if absent, return a
  skip result immediately (no temp dir created).
- `mkdtemp()`; if `fixture`, `copytree(fixture, tempdir, dirs_exist_ok=True)`;
  copy `script_path` into `tempdir`.
- `subprocess.run([interp..., script_in_temp], cwd=tempdir,
  stdin=DEVNULL, stdout=PIPE, stderr=PIPE, text=True, timeout=timeout_s)`,
  wrapped in `time.monotonic()`.
- On `subprocess.TimeoutExpired`: `ran=True`, `timed_out=True`, `ok=False`,
  capture any partial output the exception carries.
- Always `rmtree(tempdir, ignore_errors=True)` in `finally`.
- The convert module (`convert.py`) is **not** touched by verify — verify never
  writes into a skill dir and never calls `apply()`.

## Apply-gate wiring (how the CLI threads verify into `--apply`)

`apply()` in `convert.py` already refuses to write through when
`result.lint_ok` is False (`status="unsafe"`). Verify reuses **that same
gate** — no change to `convert.py` or `ConversionResult` is required, which is
why this round leaves both untouched. The CLI (`_convert_one`) does the
threading:

- **Ordering.** Stage → **lint** → (if lint passed *and* `--verify-run`) →
  **verify**. Verify runs *after* lint and only when lint passed: there is no
  point executing a script that does not even parse, and if lint already failed
  the result is already `unsafe`. Lint is cheap and static; verify is expensive
  and executes — do the cheap gate first.
- **Only for eligible plans with staged scripts.** No scripts → nothing to run.
  Ineligible/noop/unchanged/skipped results are never verified.
- **Fold the failure into the existing gate.** After computing verify, if
  `verify.ran and not verify.ok` (a real failure — nonzero exit or timeout):
  set `result.lint_ok = False`, set `result.status = "unsafe"`, and append the
  verify summary (interpreter, returncode/timeout, trimmed stderr) to
  `result.lint_output`. Then `apply()` refuses exactly as it does for a lint
  failure — the rollback. This honors the locked decision "`--apply` gated by
  backup + lint (now + verify)" without splitting the gate into two booleans:
  the reason detail lives in the `VerifyResult` and in `lint_output`, while the
  single boolean `apply()` keys off (`lint_ok`) stays authoritative.
- **A skip never blocks.** `verify.ran == False` (interpreter absent, or
  `--verify-run` not passed) leaves `lint_ok` untouched — warn, don't block,
  mirroring lint's tool-absent path.
- **Multiple staged scripts (WRAP).** A WRAP skill can stage one helper per
  language. Call `verify_script` per staged path; the gate blocks if **any**
  produced `ran and not ok`. For the summary/report, surface the first failing
  `VerifyResult` (or the dominant-language script's result when all passed).
  Because `ConversionResult` carries no verify field this round, the CLI keeps
  the per-result `VerifyResult`(s) in a local parallel map for rendering; only
  the derived boolean is folded into `lint_ok`.
- **`--apply` without `--verify-run`** behaves exactly as today: lint-only gate,
  no execution.

### Gate truth table

| lint_ok (in) | --verify-run | verify state            | resulting gate      | --apply result |
|--------------|--------------|-------------------------|---------------------|----------------|
| False        | any          | not run (short-circuit) | unsafe              | refused        |
| True         | no           | not run                 | lint_ok (unchanged) | applied        |
| True         | yes          | skipped (no interp)     | lint_ok (unchanged) | applied (warn) |
| True         | yes          | ran, ok                 | lint_ok (unchanged) | applied        |
| True         | yes          | ran, not ok / timed_out | **lint_ok → False** | **refused**    |

## CLI surface (owned by the CLI agent — `cli.py`)

Adds to the `convert` subcommand:

```
--verify-run                 # opt-in: execute staged scripts as a smoke test (prints a warning)
--verify-timeout SECONDS     # per-script execution budget (default: 20)
--fixture DIR                # seed the temp cwd by copying DIR into it before running
```

Print a one-line warning on `--verify-run` that it executes generated code, and
include each verified skill's verify outcome (ran/ok/skipped, returncode or
`timed_out`) in the convert summary output.
