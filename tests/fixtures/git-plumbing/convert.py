"""Sibling helper: runs the mechanical git sequence. Present so the
sibling-script signal fires for the git-plumbing fixture."""

import subprocess
import sys


def main(message: str, branch: str) -> int:
    subprocess.run(["git", "add", "-A"], check=True)
    subprocess.run(["git", "commit", "-m", message], check=True)
    subprocess.run(["git", "push", "origin", branch], check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1], sys.argv[2]))
