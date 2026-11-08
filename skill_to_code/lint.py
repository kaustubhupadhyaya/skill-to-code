"""Static lint (F5.1) for generated scripts. Stdlib only.

Static only: parse/compile, never execute. This gates ``--apply`` — a script
that fails to parse must not be written through.

Dispatch by extension:

    .py   ->  ``python -m py_compile <file>``   (Python is always present)
    .sh   ->  ``bash -n <file>``                (skip-with-warning if bash absent)
    .ps1  ->  ``[ScriptBlock]::Create(...)``    (skip-with-warning if pwsh absent)

A skip-with-warning keeps ``ok=True`` (the box simply lacks that interpreter, so
we couldn't statically verify) and returns an explanatory note. A real parse
failure returns ``ok=False`` with the tool's output.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

_LINT_TIMEOUT = 30  # seconds; static parse should be near-instant


def _run(cmd: list[str], stdin_text: str | None = None) -> tuple[int, str]:
    """Run ``cmd`` capturing combined stdout+stderr. Returns (returncode, text)."""
    # Explicit UTF-8: the platform default (cp1252 on Windows) crashes the
    # stdin writer thread on chars like U+2705, so the child never sees EOF
    # and the run dies on timeout instead of linting.
    proc = subprocess.run(
        cmd,
        input=stdin_text,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        encoding="utf-8",
        errors="replace",
        timeout=_LINT_TIMEOUT,
    )
    return proc.returncode, proc.stdout or ""


def _lint_python(path: str) -> tuple[bool, str]:
    rc, out = _run([sys.executable, "-m", "py_compile", path])
    if rc == 0:
        return True, f"py_compile ok: {path}"
    return False, f"py_compile FAILED: {path}\n{out}".rstrip()


def _lint_shell(path: str) -> tuple[bool, str]:
    bash = shutil.which("bash")
    if not bash:
        return True, f"bash unavailable: skipped static check for {path}"
    # Feed the script on stdin rather than as a path argument. On Windows,
    # `bash` may resolve to WSL's launcher (C:\Windows\System32\bash.exe), which
    # mangles a Windows-style path argument (backslashes read as escapes) and
    # fails with rc 127. Reading from stdin sidesteps path translation entirely
    # and works for both Git Bash and WSL bash.
    with open(path, "r", encoding="utf-8") as fh:
        src = fh.read()
    rc, out = _run([bash, "-n", "-"], stdin_text=src)
    if rc == 0:
        return True, f"bash -n ok: {path}"
    return False, f"bash -n FAILED: {path}\n{out}".rstrip()


def _lint_powershell(path: str) -> tuple[bool, str]:
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if not shell:
        return True, f"powershell unavailable: skipped static check for {path}"
    # Parse without executing: compiling a ScriptBlock from the file text raises
    # on a syntax error but never runs the script's statements.
    script = (
        "$ErrorActionPreference='Stop';"
        f"$src=Get-Content -Raw -LiteralPath {_ps_quote(path)};"
        "[void][ScriptBlock]::Create($src)"
    )
    rc, out = _run([shell, "-NoProfile", "-NonInteractive", "-Command", script])
    if rc == 0:
        return True, f"powershell parse ok: {path}"
    return False, f"powershell parse FAILED: {path}\n{out}".rstrip()


def _ps_quote(s: str) -> str:
    """Single-quote a string for PowerShell (double any embedded quote)."""
    return "'" + s.replace("'", "''") + "'"


_DISPATCH = {
    ".py": _lint_python,
    ".sh": _lint_shell,
    ".ps1": _lint_powershell,
}


def lint_script(path: str) -> tuple[bool, str]:
    """Statically lint one script, dispatching by extension.

    Returns ``(ok, output)``. ``ok`` is False on a real parse/compile failure
    or a linter timeout — a script we could not verify in time must not pass
    the gate. An unknown extension is treated as a skip-with-warning (ok=True).
    The interpreter is never invoked on the script; only a parser/compiler is.
    """
    ext = os.path.splitext(path)[1].lower()
    fn = _DISPATCH.get(ext)
    if fn is None:
        return True, f"no linter for extension {ext!r}: skipped {path}"
    try:
        return fn(path)
    except subprocess.TimeoutExpired:
        return False, f"lint timeout after {_LINT_TIMEOUT}s: {path}"


def lint_scripts(paths: list[str]) -> tuple[bool, str]:
    """Lint several scripts. ``ok`` is the AND of all; notes are newline-joined."""
    ok = True
    notes: list[str] = []
    for p in paths:
        p_ok, note = lint_script(p)
        ok = ok and p_ok
        notes.append(note)
    return ok, "\n".join(notes)
