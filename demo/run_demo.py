"""Reproducible end-to-end demo for skill-to-code.

Runs the real CLI against the bundled example skills in a throwaway copy:

    scan -> convert (stage) -> convert --verify-run --apply -> run the artifact

The demo never reads ~/.claude, never writes to this repository, and does not
run the test suite. Exit code 0 means every stage produced the expected result.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYTHON = sys.executable
ENV = {**os.environ, "PYTHONPATH": str(ROOT)}


def run(args: list[str], cwd: Path) -> str:
    print("$ " + " ".join(args))
    proc = subprocess.run(
        args,
        cwd=str(cwd),
        env=ENV,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=120,
    )
    output = proc.stdout or ""
    print(output.rstrip())
    if proc.returncode != 0:
        raise SystemExit(f"command failed with exit {proc.returncode}: {args}")
    return output


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="stc-demo-") as tmp:
        tmp_path = Path(tmp)
        stage_skills = tmp_path / "stage-skills"
        apply_skills = tmp_path / "apply-skills"
        shutil.copytree(ROOT / "examples" / "skills", stage_skills)
        shutil.copytree(ROOT / "examples" / "skills", apply_skills)
        skill_md = stage_skills / "build-report" / "SKILL.md"
        original = skill_md.read_bytes()

        # 1. Read-only scan. --root replaces the live discovery roots, so the
        #    demo sees only the bundled skills.
        scan_out = run(
            [
                PYTHON,
                "-m",
                "skill_to_code",
                "scan",
                "--root",
                str(stage_skills),
                "--format",
                "json",
            ],
            ROOT,
        )
        scan = json.loads(scan_out)
        buckets = {record["name"]: record["bucket"] for record in scan["skills"]}
        assert scan["count"] == 2, scan
        assert buckets == {"build-report": "replace", "release-notes": "skip"}, buckets
        print(f"[1/4] scan ok: {scan['count']} skills, buckets={sorted(buckets.values())}")

        # 2. Dry-run conversion. Staging writes only under <skill>/.converted/;
        #    the real SKILL.md must stay byte-identical.
        run(
            [
                PYTHON,
                "-m",
                "skill_to_code",
                "convert",
                "--root",
                str(stage_skills),
                "--name",
                "build-report",
            ],
            ROOT,
        )
        staged_script = stage_skills / "build-report" / ".converted" / "build-report.py"
        assert staged_script.is_file(), staged_script
        assert skill_md.read_bytes() == original, "dry run modified the original"
        print("[2/4] stage ok: .converted/build-report.py written, original untouched")

        # 3. Full gate on a second pristine copy: execute the generated script
        #    in a seeded temp dir, then apply. A failed verify would block apply.
        apply_original = (apply_skills / "build-report" / "SKILL.md").read_bytes()
        apply_out = run(
            [
                PYTHON,
                "-m",
                "skill_to_code",
                "convert",
                "--root",
                str(apply_skills),
                "--name",
                "build-report",
                "--verify-run",
                "--fixture",
                str(ROOT / "examples" / "fixture"),
                "--apply",
            ],
            ROOT,
        )
        assert "VERIFY" in apply_out, apply_out
        assert "ok (rc=0" in apply_out, apply_out
        sibling = apply_skills / "build-report" / "build-report.py"
        backup = apply_skills / "build-report" / ".converted" / ".backup" / "SKILL.md"
        applied_md = apply_skills / "build-report" / "SKILL.md"
        assert sibling.is_file(), sibling
        assert applied_md.read_bytes() != apply_original, "apply left SKILL.md unchanged"
        assert backup.read_bytes() == apply_original, "backup does not match the original"
        print("[3/4] apply ok: verify passed, stub written, backup matches original")

        # 4. Run the applied artifact somewhere harmless.
        run_dir = tmp_path / "run"
        shutil.copytree(ROOT / "examples" / "fixture", run_dir)
        shutil.copyfile(sibling, run_dir / "build-report.py")
        run([PYTHON, str(run_dir / "build-report.py")], run_dir)
        report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
        assert len(report) == 1, report
        assert report[0]["name"] == "notes.txt", report
        print(f"[4/4] artifact ok: report.json has {len(report)} entry")

    print("DEMO OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
