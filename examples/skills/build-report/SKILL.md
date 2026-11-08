---
name: build-report
description: Build a deterministic JSON report from a directory of text files.
---

# build-report

Input: a directory containing `.txt` files.
Output: `report.json` in the current working directory.

## Steps

Run the shell steps to list, copy, and compare the input files:

```bash
find . -maxdepth 1 -name '*.txt' -print | sort > files.txt
wc -l files.txt
cat files.txt
sha256sum *.txt > hashes.txt
head -n 3 hashes.txt
cp files.txt files.copy
diff -u files.txt files.copy
```

Then execute the Python step. It reads, parses, counts, hashes, and writes the
report. No judgment is involved:

```python
import hashlib
import json
import pathlib

files = sorted(pathlib.Path(".").glob("*.txt"))
report = []
for path in files:
    data = path.read_bytes()
    report.append({
        "name": path.name,
        "lines": data.count(b"\n"),
        "sha256": hashlib.sha256(data).hexdigest(),
    })
pathlib.Path("report.json").write_text(
    json.dumps(report, indent=2), encoding="utf-8"
)
print(f"wrote report.json with {len(report)} entries")
```
