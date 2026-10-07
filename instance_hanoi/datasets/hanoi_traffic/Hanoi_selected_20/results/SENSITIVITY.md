# Freshness and Static-Speed Sensitivity

The experiment crosses freshness limits of 48, 60, and 72 minutes with
four policies: P0_mean, P0_q25, P1, and P2. All policies are reoptimized
at each freshness limit; a relaxed or tightened limit is not merely
applied to an old route. Each optimization uses the same original
20 instances, seeds 1-10, 9,000 iterations, 300 iterations per segment,
and a 600-second search limit. All other customer and vehicle inputs
are preserved. Truck/drone trips use the same rules as the existing
P0/P1/P2 experiment.

P0_mean is the original instance-specific arithmetic mean speed.
P0_q25 is the per-instance 25th percentile of the exact same collection
of unrounded hourly arc speeds: three profiles, 12 hours (6-17), and
all 101 x 101 ordered node pairs, including self-pairs. The sample
quantile uses linear interpolation at position (N-1)*0.25 (R type 7).
P0_q25 is constant across all arcs and hours. Both static policies
optimize once per instance/seed at 7h and replay across three profiles
and all four start hours. P1 optimizes per instance/hour/seed; P2 per
instance/profile/hour/seed. P1 and P2 retain their existing speed rules.

Per freshness scale:

| Policy | Optimizations | Evaluations |
| --- | ---: | ---: |
| P0_mean | 200 | 2,400 |
| P0_q25 | 200 | 2,400 |
| P1 | 800 | 2,400 |
| P2 | 2,400 | 2,400 |
| Total | 3,600 | 9,600 |

All three scales require 10,800 optimizations and 28,800 evaluations.
Lower static speed is conservative in its travel-time assumption;
it is not guaranteed to improve feasibility or completion time.
Results must determine whether the substantive conclusions persist.

## GitHub Actions

Run `.github/workflows/hanoi-sensitivity-v2.yml`. It calls the reusable
worker workflow once for each freshness scale. Each scale has 250 jobs
with at most 15 optimizations per job. The default permits 12 solver
jobs per scale (36 total). Result upload runs even after a batch fails.
Each scale produces a summary workbook, summary CSV, validation JSON,
and raw optimization/replay records and final routes in batch artifacts.

The parent workflow must be available on GitHub before dispatch:

```sh
gh workflow run hanoi-sensitivity-v2.yml -f max_parallel_per_scale=12
```

Downloaded raw batch artifacts can be combined across all three scales:

```sh
python3 scripts/hanoi_sensitivity.py aggregate \
  --input-root collected_sensitivity_batches \
  --output sensitivity_results/hanoi_sensitivity.xlsx
```

The aggregator checks every expected optimization/evaluation identity
and freshness scale. Incomplete runs produce a diagnostic workbook
but exit with an error. It rejects duplicate identities. Conditional
regret pairs against P2 on scale, instance, actual profile, hour, and
seed, and requires both solutions to be feasible. Published regret is
blank below 30 eligible pairs; raw means and pair counts are retained.
Day-level rates use 800 cases per policy; Overall pools records.
P2 departure means use feasible cases, with their counts shown.

## Local Smoke Check

The `--smoke` option runs two search iterations with a 60-second limit.
Its output is marked as smoke in config files and does not establish
scientific sensitivity results. Production aggregation rejects smoke
configs. `--smoke` aggregation permits partial grids but records their
incompleteness in the Validation sheet.

Focused checks:

```sh
python3 -B -m unittest discover -s scripts -p test_hanoi_sensitivity.py
```
