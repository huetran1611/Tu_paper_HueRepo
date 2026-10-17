# Hanoi Buffer 20% Extension

Workflow: `.github/workflows/hanoi-buffer20.yml`.

This adds only buffer 0.2 to the frozen baseline/buffer10 experiment. It
downloads initialization capsule `38888658850` and uses its exact Linux
solver, code, data, selected flags, seeds 42..51, departure 08:00 and four
folds. P0 still uses [08:00,17:00). No solver is rebuilt, no traffic data
is regenerated and no PI optimization is rerun.

Planning uses 2880-second (48-minute) freshness. Fixed-plan replay always
uses the original 3600-second (60-minute) instance. Truck capacity remains
40 kg and drone capacity remains 2.27 kg.

## Workload

| Policy | New optimizations | Scheduled replays |
|---|---:|---:|
| P0 | 800 | 2400 |
| P1 | 800 | 2400 |
| P2 | 2400 | 2400 |
| Total | 4000 | 7200 |

P0/P1 each reuse their plan on all three held-out day types. P2 is specific
to the held-out day type. There are 16 batches of exactly 250 optimization
jobs, plus prepare and batch-report jobs per batch. Actual replay count is
lower if planning returns no feasible solution. Missing technical results
are not counted as planning non-success.

## Run

Commit and push the new workflow, composite action and runner before use.
For each batch 1..16, select mode `batch`, setup ID `38888658850` and a
parallelism limit (default 20). Use the same code commit for all batches.

```bash
gh workflow run hanoi-buffer20.yml --ref YOUR_BRANCH \
  -f mode=batch -f setup_run_id=38888658850 -f batch=1 -f max_parallel=20
```

After all 16 batches finish successfully:

```bash
gh workflow run hanoi-buffer20.yml --ref YOUR_BRANCH \
  -f mode=aggregate -f setup_run_id=38888658850 -f batch=1 -f max_parallel=20
```

The aggregate mode retrieves the original five workflows' packed records
and this extension's matching packed records. It validates 14400 distinct
optimization records (including the original 2400 shared PI references)
and 21600 scheduled replay cases across buffers 0, 0.1 and 0.2. It exports
27 summary rows, combined CSVs, paired comparisons against buffer0 and a
combined LaTeX table. Incomplete coverage blocks LaTeX publication.

## Preservation And Recovery

The new runner is saved to the runner's temporary directory before the
original bootstrap checks out the frozen source commit. This avoids editing
the frozen code or bypassing its source/binary/data hash checks. Extension
identity includes both the parent protocol and the new runner's SHA-256.
The extension action metadata is also restored after checkout so GitHub can
perform its composite-action post-job cleanup; it is not part of the frozen code.

Raw and packed artifact names include the extension identity, source run
and attempt. Original artifacts are never overwritten. Use **Re-run failed
jobs** for technical failures; do not change a seed to replace an unfavorable
solution. Identical retries are deduplicated, conflicting retries block
aggregation. Freshness failures, negative gaps and undefined values retain
the original reporting semantics.

Capsule and original batch artifacts must remain available (90-day retention
when originally uploaded). Local directory renames do not affect the frozen
capsule. A new initialization would create a different experiment and must
not silently replace this parent when comparing buffer levels.

Local tests use Conda base:

```bash
PYTHONPATH=scripts /Users/huetran/anaconda3/bin/python \
  -m unittest discover -s scripts -p test_hanoi_buffer20_experiment.py
```
