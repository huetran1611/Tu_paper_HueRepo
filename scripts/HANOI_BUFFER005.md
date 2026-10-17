# Hanoi Buffer 0.5 Percent

Workflow: `.github/workflows/hanoi-buffer005.yml`.

The user explicitly selected **0.5 percent**, not epsilon 0.5 (50 percent).
The new planning freshness is `(1 - 0.005) * 3600 = 3582 seconds`, or
59 minutes 42 seconds. Replay still checks the original 3600-second limit.
Task suffix `b005` and artifact prefix `hn005` identify this setting;
`buffer_pct` in the result record is the number `0.5` and epsilon is `0.005`.

The workflow reuses initialization capsule `38888658850`: identical frozen
Linux solver, source helpers, traffic profiles, selected parameters,
departure 08:00, P0 window [08:00,17:00), seeds 42..51 and all four folds.
No solver is rebuilt. Truck capacity is 40 kg, drone capacity is 2.27 kg.
No old results are overwritten, no PI is rerun and no source data is modified.

## Workload

- P0: 20 instances x 4 folds x 10 seeds = 800 optimizations.
- P1: 20 instances x 4 folds x 10 seeds = 800 optimizations.
- P2: 20 instances x 4 folds x 10 seeds x 3 day types = 2400 optimizations.
- Total: 4000 optimizations in 16 batches of exactly 250 compute jobs.
- At most 7200 replays: P0/P1 plans each replay on all three held-out dates.

Technical failures are missing records, not planning non-success. A plan
not feasible at 3582 seconds is recorded as planning non-success and is not
replayed. Freshness-violating replays are retained to completion.

## Dispatch

Run batch 1..16 using the same published commit and original setup ID:

```bash
gh workflow run hanoi-buffer005.yml --ref codex/hanoi-buffer005 \
  -f setup_run_id=38888658850 -f batch=1 -f max_parallel=20
```

The account-wide GitHub runner quota still limits concurrency. Each batch
also contains prepare/report jobs. Raw and packed artifacts have 90-day
retention, unique run/attempt names and a hash-bound extension identity.
Use **Re-run failed jobs** for technical failures without changing seeds.
Identical retries are deduplicated; conflicting retries block batch success.

This workflow produces raw and validated per-batch results. It does not
automatically aggregate earlier buffer levels or change the existing
buffer20 aggregation workflow. Subsequent combined reporting must collect
this extension's records explicitly and reuse the original matched PI.

The separate runner intentionally leaves the already-published buffer20
runner unchanged: its source hash identifies the existing buffer20 artifacts.
