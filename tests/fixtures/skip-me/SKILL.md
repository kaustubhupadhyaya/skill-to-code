---
name: skip-me
description: This fixture carries a .skipconvert sentinel and must be excluded from discovery entirely, regardless of how it would otherwise score.
---

# skip-me

## Input

- Anything.

## Output

- Nothing — this skill must never appear in scan results.

Run the commands, execute the script, copy the files:

```bash
$ python convert.py
$ cp a b
$ git commit -am done
```

Even though this body is mechanical and would otherwise score highly, the
`.skipconvert` sentinel in this directory must cause discovery to drop it.
