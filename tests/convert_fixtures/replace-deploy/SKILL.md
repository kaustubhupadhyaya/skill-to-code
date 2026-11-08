---
name: replace-deploy
description: Mechanically build, package, and upload the release artifact. Pure plumbing, no judgment.
---

# replace-deploy

Run the exact commands below in order. Do not deviate.

Input: `TAG` (the release tag) and `BUCKET` (the upload target).
Output: an artifact uploaded to the bucket.

## Steps

Build and package the artifact, then upload it. Run each command:

```bash
git fetch origin
git checkout "$TAG"
make build
tar -czf release.tgz ./dist
```

Verify the checksum and upload the archive:

```bash
sha256sum release.tgz > release.sha256
aws s3 cp release.tgz "s3://$BUCKET/release.tgz"
aws s3 cp release.sha256 "s3://$BUCKET/release.sha256"
```

A small helper computes the manifest before upload. Run it:

```python
import json, hashlib, pathlib
digest = hashlib.sha256(pathlib.Path("release.tgz").read_bytes()).hexdigest()
pathlib.Path("manifest.json").write_text(json.dumps({"sha256": digest}))
```
