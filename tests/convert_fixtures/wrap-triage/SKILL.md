---
name: wrap-triage
description: Pull metrics and then decide whether the regression warrants a rollback. Mechanical fetch, judgment call at the end.
---

# wrap-triage

Input: the `SERVICE` name to inspect.
Output: a rollback / hold recommendation.

## Fetch the metrics

First pull the raw metrics for the service. Run this to fetch and save them:

```bash
curl -s "https://metrics.example.com/$SERVICE" > metrics.json
```

Now parse the JSON and pull out the p99 latency series. Run this helper to read
and extract the numbers:

```python
import json
data = json.load(open("metrics.json"))
open("p99.txt", "w").write("\n".join(str(x) for x in data["latency_p99"]))
```

Finally sort and count the series into a small table. Run this to sort, filter,
and format the list:

```bash
sort -rn p99.txt | head -20 > top.txt
wc -l top.txt
```

## Make the call

Read `top.txt` and decide whether the p99 spike is a real regression. Use
judgment here; a single slow sample is not a reason to page the on-call engineer.
