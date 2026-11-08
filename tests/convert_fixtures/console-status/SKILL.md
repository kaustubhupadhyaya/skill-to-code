---
name: console-status
description: Mechanically check repo status and stage the tree. A recorded console transcript with prompts and output interleaved.
---

# console-status

Run the exact commands below. Do not deviate.

Input: `BRANCH` (the branch to stage).
Output: a fully staged working tree.

## Steps

Check the status, then stage everything. This is a recorded transcript; the
prompt-prefixed lines are the commands and the rest is their output:

```console
$ git status --short
 M src/app.py
 M README.md
?? notes.txt
$ git add -A
$ git status --short
A  notes.txt
M  README.md
M  src/app.py
```

Then confirm the staged file count and print the current branch:

```console
$ git diff --cached --name-only | wc -l
3
$ git rev-parse --abbrev-ref HEAD
main
```
