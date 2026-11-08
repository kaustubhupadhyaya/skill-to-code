"""BUG-1 regression: non-cp1252 characters and timeouts must not crash lint."""

import shutil
import subprocess

import pytest

from skill_to_code import lint

EMOJI_LINE = 'echo "✅ done → next ❌ ⚠"\n'

HAS_BASH = shutil.which("bash") is not None


def _write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return str(p)


def test_bash_with_unicode_returns_ok(tmp_path):
    path = _write(tmp_path, "good.sh", "#!/usr/bin/env bash\n" + EMOJI_LINE)
    ok, out = lint.lint_script(path)
    assert isinstance(ok, bool)
    assert isinstance(out, str)
    assert ok is True


def test_python_with_unicode_returns_ok(tmp_path):
    path = _write(tmp_path, "good.py", 'print("✅ → ❌ ⚠")\n')
    ok, out = lint.lint_script(path)
    assert ok is True
    assert isinstance(out, str)


@pytest.mark.skipif(not HAS_BASH, reason="bash not on PATH")
def test_bash_unicode_with_syntax_error_fails(tmp_path):
    src = "#!/usr/bin/env bash\n" + EMOJI_LINE + "if [ -f x ]; then\n  echo oops\n"
    path = _write(tmp_path, "bad.sh", src)
    ok, out = lint.lint_script(path)
    assert ok is False
    assert "bash -n FAILED" in out


def test_timeout_is_lint_failure_not_exception(tmp_path, monkeypatch):
    path = _write(tmp_path, "slow.py", 'print("hi")\n')

    def raise_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="fake", timeout=lint._LINT_TIMEOUT)

    monkeypatch.setattr(lint.subprocess, "run", raise_timeout)
    ok, out = lint.lint_script(path)
    assert ok is False
    assert "timeout" in out.lower()
