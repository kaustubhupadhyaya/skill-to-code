"""Optional sandboxed verify-run (M4, F5.2/T5.3) for generated scripts. Stdlib only.

Where lint.py *parses* a staged script, verify EXECUTES it once, as an opt-in
smoke test, and treats a run-time failure the way lint treats a parse failure.
The CLI folds the derived boolean into the apply gate (see docs/verify.md).

SAFETY (non-negotiable, mirrors docs/verify.md):
  - Execution ALWAYS happens in a throwaway ``tempfile.mkdtemp()`` working
    directory, optionally seeded by copying a ``--fixture DIR`` into it. Nothing
    ever runs inside or against a real skill directory.
  - Bounded by a timeout; ``stdin`` is closed. A timeout is a failure.
  - Interpreter chosen by extension via ``shutil.which`` (mirrors lint.py);
    a missing interpreter is a graceful SKIP (``ran=False``), never a failure.
  - The temp dir is always removed in a ``finally``.

This is a SMOKE TEST, not an OS security sandbox: no filesystem or network
isolation. Only run it on scripts you have read and trust.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time

from skill_to_code.types import ConversionResult, VerifyResult


def _resolve_interpreter(ext: str) -> tuple[str, list[str] | None]:
    """Map a script extension to ``(interpreter_label, argv_prefix)``.

    ``argv_prefix`` is the command prefix the script path is appended to, or
    ``None`` when the interpreter cannot be used. The two ``None`` cases are
    distinguished by the label: a non-empty label means the interpreter is
    known but absent (skip: "interpreter not found"); an empty label means the
    extension has no interpreter at all (skip: "no interpreter for extension").
    """
    ext = ext.lower()
    if ext == ".py":
        # Python is always present — it is running this code.
        return "python", [sys.executable]
    if ext == ".sh":
        bash = shutil.which("bash")
        return "bash", ([bash] if bash else None)
    if ext == ".ps1":
        shell = shutil.which("pwsh") or shutil.which("powershell")
        return "powershell", ([shell, "-NoProfile", "-File"] if shell else None)
    return "", None


def _as_text(data) -> str:
    """Coerce captured output (str | bytes | None) to text."""
    if data is None:
        return ""
    if isinstance(data, bytes):
        return data.decode("utf-8", errors="replace")
    return data


def verify_script(
    script_path: str,
    *,
    fixture: str | None = None,
    timeout_s: float = 20.0,
    timeout: float | None = None,
) -> VerifyResult:
    """Execute one staged script as a smoke test and report the outcome.

    ``fixture`` is a directory copied into the temp cwd before running (to seed
    inputs). ``timeout_s`` (alias ``timeout``) bounds the run. Returns a
    ``VerifyResult``; a missing interpreter yields a graceful skip
    (``ran=False``) with no temp dir created.
    """
    if timeout is not None:
        timeout_s = timeout

    ext = os.path.splitext(script_path)[1]
    label, prefix = _resolve_interpreter(ext)
    if prefix is None:
        reason = (
            f"interpreter not found: {label}"
            if label
            else f"no interpreter for extension {ext.lower()!r}"
        )
        return VerifyResult(skipped_reason=reason, interpreter=label)

    tempdir = tempfile.mkdtemp(prefix="s2c-verify-")
    try:
        if fixture:
            shutil.copytree(fixture, tempdir, dirs_exist_ok=True)
        dest = os.path.join(tempdir, os.path.basename(script_path))
        shutil.copyfile(script_path, dest)
        argv = prefix + [dest]

        start = time.monotonic()
        try:
            proc = subprocess.run(
                argv,
                cwd=tempdir,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout_s,
            )
        except subprocess.TimeoutExpired as exc:
            return VerifyResult(
                ran=True,
                ok=False,
                returncode=None,
                stdout=_as_text(exc.stdout),
                stderr=_as_text(exc.stderr),
                duration_s=time.monotonic() - start,
                timed_out=True,
                interpreter=label,
            )
        return VerifyResult(
            ran=True,
            ok=(proc.returncode == 0),
            returncode=proc.returncode,
            stdout=proc.stdout or "",
            stderr=proc.stderr or "",
            duration_s=time.monotonic() - start,
            timed_out=False,
            interpreter=label,
        )
    finally:
        shutil.rmtree(tempdir, ignore_errors=True)


def _primary_script_path(result: ConversionResult) -> str | None:
    """The dominant-language staged script for a result, else the first one.

    REPLACE stages a single script; WRAP may stage one per language — the
    dominant-language script is the representative one to smoke-test.
    """
    paths = list(result.script_paths)
    if not paths:
        return None
    plan = result.plan
    dominant = plan.dominant_lang
    if dominant:
        ext = next((seg.ext for seg in plan.segments if seg.lang == dominant), "")
        if ext:
            target = f"{plan.script_name}{ext}"
            for p in paths:
                if os.path.basename(p) == target:
                    return p
    return sorted(paths)[0]


def verify_conversion(
    result: ConversionResult,
    *,
    fixture: str | None = None,
    timeout_s: float = 20.0,
    timeout: float | None = None,
) -> VerifyResult:
    """Verify the primary generated script of a staged ConversionResult.

    A result with no staged script (ineligible/noop/skipped) is a graceful
    skip. See ``verify_script`` for the execution contract.
    """
    if timeout is not None:
        timeout_s = timeout
    primary = _primary_script_path(result)
    if primary is None:
        return VerifyResult(skipped_reason="no staged script to verify")
    return verify_script(primary, fixture=fixture, timeout_s=timeout_s)
