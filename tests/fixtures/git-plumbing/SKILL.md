---
name: git-plumbing
description: Mechanically stage, commit, and push the current change set. Pure git plumbing, no judgment.
---

# git-plumbing

Run the exact commands below in order. Do not deviate.

Input: `MESSAGE` (the commit message string) and `BRANCH` (the branch to push to).
Output: a new commit pushed to `origin/BRANCH`.

## Steps

Stage everything, then commit and push. Run each command:

```bash
$ git add -A
$ git commit -m "$MESSAGE"
$ git push origin "$BRANCH"
```

Fetch and fast-forward first if the push is rejected:

```bash
$ git fetch origin
$ git checkout "$BRANCH"
$ git pull --ff-only origin "$BRANCH"
$ git push origin "$BRANCH"
```

To list, copy, and move the generated patch files, run:

```sh
$ git format-patch -1 HEAD
$ cp *.patch ./out/
$ mv ./out/*.patch ./archive/
```

The helper script does the whole sequence: it will read the message, run
git add, commit, push, and format the output. Execute it directly:

```bash
$ python convert.py --message "$MESSAGE" --branch "$BRANCH"
```
