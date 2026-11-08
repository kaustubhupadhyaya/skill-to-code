---
name: log-triage
description: Fetch and parse service logs, then decide which errors warrant a page. Mostly mechanical with a judgment call at the end.
---

# log-triage

Input: the `SERVICE` name whose logs to pull.
Output: a shortlist of error lines worth escalating.

## Fetch and parse

Download the last hour of logs. Fetch the raw file, then grep, sort, and count
the error lines:

```bash
$ curl -s "https://logs.example.com/$SERVICE?window=1h" > raw.log
$ grep -i error raw.log > errors.log
$ sort errors.log | uniq -c | sort -rn > counts.txt
```

Read `counts.txt` and parse each line. Split each line into a count and a
message, filter out anything under 5 occurrences, and format the remainder as a
table. Copy the table to the clipboard:

```sh
$ cat counts.txt | awk '$1 >= 5' > shortlist.txt
$ wc -l shortlist.txt
$ cp shortlist.txt ./out/
```

Run the parse helper if you prefer: it will read the counts, filter, sort, and
write the shortlist for you.

## Escalation

Once the mechanical parse and count are done, assess the remaining errors and
decide whether to page on-call. Use judgment here — a noisy stack trace is not
the same as a data-loss error.
