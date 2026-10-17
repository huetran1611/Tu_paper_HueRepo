# Hanoi Buffer 15 Percent

Workflow: `.github/workflows/hanoi-buffer15.yml`.

Planning freshness is `(1 - 0.15) * 3600 = 3060 seconds` (51 minutes).
Fixed-plan replay always evaluates the original 3600-second limit.
Records use `buffer_pct=15`, task suffix `b15` and artifact prefix `hn15`.

The workflow reuses capsule `38888658850`: identical frozen Linux solver,
data, parameters, 20 instances, four folds, seeds 42..51, departure 08:00
and P0 window [08:00,17:00). Truck capacity stays 40 kg and drone capacity
stays 2.27 kg. No solver is rebuilt and no PI optimization is rerun.

New optimizations: P0 800, P1 800, P2 2400; total 4000 in 16 batches of
250 compute jobs, plus prepare and report jobs. At most 7200 fixed-plan
replays follow. P0/P1 each reuse their plan across all three held-out day
types. P2 plans are specific to the held-out day type.

```bash
gh workflow run hanoi-buffer15.yml --ref codex/hanoi-buffer15 \
  -f setup_run_id=38888658850 -f batch=1 -f max_parallel=20
```

Run all batches 1..16 at the same commit. GitHub's account-wide concurrency
limit is shared with other running workflows, including buffer 0.5 percent.
Submitting this study does not cancel, change or overwrite that study.

Raw and packed artifacts have unique extension/run/attempt names and 90-day
retention. The extension identity includes the original protocol and runner
source hash. The bootstrap saves its runner and restores its action metadata
after frozen checkout so GitHub post-job cleanup can complete.

Use **Re-run failed jobs** for technical failures without changing seeds.
Missing records are not planning non-success. Identical retries are deduplicated;
conflicts and incomplete coverage fail the batch while preserving valid records.
Freshness-violating replays remain in the saved results.

This workflow saves raw and validated batch records, not a combined report
across every buffer level. Subsequent reporting must explicitly collect the
15-percent records and match them with the original unbuffered PI references.
Existing buffer runners and their hash-bound results are left unchanged.
