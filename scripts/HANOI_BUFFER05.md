# Hanoi Buffer 5 Percent

The user explicitly requested 5 percent, not the previously run 0.5 percent.
Workflow: `.github/workflows/hanoi-buffer05.yml`.

Planning freshness: `(1 - 0.05) * 3600 = 3420 seconds` (57 minutes).
Fixed-route replay checks the original 3600-second limit.
Records use buffer_pct=5, epsilon=0.05, task suffix b05, artifact prefix hn05.
The older 0.5-percent records use buffer_pct=0.5 and b005; never relabel them.

Capsule 38888658850 supplies the identical original Linux solver, data,
selected flags, departure 08:00, P0 window [08:00,17:00), four folds,
20 instances and seeds 42..51. Truck capacity is 40 kg; drone is 2.27 kg.
No solver is rebuilt. Original unbuffered PI is reused, not optimized again.

Workload: P0 800 + P1 800 + P2 2400 = 4000 optimizations, across 16 batches
of 250 compute jobs each, followed by at most 7200 fixed-route replays.
P0/P1 plans each replay on all three held-out day types. Technical failures
remain missing records, not planning non-success. All valid raw records
are preserved, including freshness-violating replay durations.

```bash
gh workflow run hanoi-buffer05.yml --ref codex/hanoi-buffer05 \
  -f setup_run_id=38888658850 -f batch=1 -f max_parallel=20
```

Run batches 1..16 at the same commit. Account-wide GitHub runner limits apply.
Use Re-run failed jobs for technical failures without replacing seeds.
Raw/packed artifacts have 90-day retention and unique protocol/run/attempt names.
The bootstrap preserves action metadata across frozen checkout for cleanup.

This workflow saves per-batch data, not a combined report. The requested
5/10/15/20-percent figure cannot include a measured 5-percent point until
this campaign finishes; the 0.5-percent campaign is not a substitute.
