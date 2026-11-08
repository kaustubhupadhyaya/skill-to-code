---
name: decoy-config
description: Read, parse, and format the service config. Looks mechanical, but every fenced block is data, not a runnable command.
---

# decoy-config

Input: the `config.json` to read.
Output: a normalized config, formatted and sorted.

## Format

Run the formatter to parse, sort, and rewrite the config. Fetch, read, and format
each field, then copy and write the result back to disk.

    $ format-config --in config.json --out normalized.json
    $ sort-keys normalized.json

The expected input config looks like this:

```json
{
  "service": "billing",
  "retries": 3,
  "timeout_ms": 500,
  "endpoints": ["a", "b", "c"]
}
```

And the normalized output should match this shape exactly:

```yaml
service: billing
retries: 3
timeout_ms: 500
endpoints:
  - a
  - b
  - c
```

The schema that governs both is here for reference:

```json
{
  "type": "object",
  "required": ["service", "retries", "timeout_ms"],
  "properties": {
    "service": {"type": "string"},
    "retries": {"type": "integer"}
  }
}
```
