"""Unified diff of an original vs a rewritten SKILL.md. Stdlib only.

Pure text -> text via ``difflib``. No filesystem access here; the CLI reads the
two SKILL.md texts and passes them in.
"""

from __future__ import annotations

import difflib


def unified_diff(original_text: str, rewritten_text: str, path: str) -> str:
    """Return a unified diff (original -> rewritten), labeled by ``path``.

    Empty string when the two texts are identical. ``keepends`` splitting keeps
    difflib's line accounting correct for texts with or without a trailing
    newline.
    """
    original_lines = original_text.splitlines(keepends=True)
    rewritten_lines = rewritten_text.splitlines(keepends=True)
    diff = difflib.unified_diff(
        original_lines,
        rewritten_lines,
        fromfile=f"a/{path}",
        tofile=f"b/{path}",
        lineterm="",
    )
    return "\n".join(diff)
