---
name: dep-upgrade
description: Upgrade a dependency and reason about whether the change is safe to ship. A genuine mix of mechanical steps and judgment.
---

# dep-upgrade

Input: the `LIB` name to upgrade.
Output: an updated lockfile plus a ship / hold recommendation.

## Mechanical steps

Do the mechanical part first. Fetch the latest version, update the lockfile,
build, and run the tests. Run each command in order:

```bash
$ npm install "$LIB@latest"
$ npm run build
$ npm test
```

Then read the changelog, download the release notes, and extract the list of
breaking changes:

```sh
$ curl -s "https://registry.example.com/$LIB/changelog" > changelog.md
$ grep -i breaking changelog.md > breaking.txt
$ wc -l breaking.txt
```

Copy the diff of the lockfile so the reviewer can read it, and format the
breaking-change list as a checklist.

## Review the change

Now the judgment part. Interpret the changelog and decide whether to ship.
Weigh the security fix against the API churn. If you are unsure, recommend a
staged rollout.
