# Four-fold P2 pilot: 240 independent GitHub optimization jobs

Purpose: check whether the entire truck-and-drone system finishes by 17:00
when dispatched at 10:00. This runs the optimization algorithm, not S1.

The workflow `hanoi-p2-fourfold-sequential.yml` now creates a matrix of 240
optimization jobs, each processing exactly one case followed by held-out
replay. Total: 20 instances x three day types x four folds. The filename
is retained so existing links continue to work; execution is no longer
sequential. The default concurrency is 20 jobs, configurable from 1 to 40
at manual dispatch, subject to the account's GitHub runner limits.
There are two additional utility jobs: one builds/tests the solver once,
and one checks coverage and uploads the consolidated Excel report.
Sparse checkout downloads only code and the compact data bundle.

Configuration:
- One truck, capacity 40 kg; one drone, capacity 2.27 kg.
- One seed (42), one initial-solution attempt, 9,000 search iterations.
- c_seg=2 => 200-iteration segments for 100 customers.
- c_tau=2 with existing max(20, int(c_tau*sqrt(n))) => tenure 20.
- H_mode=1, H_div=4, gamma1..4=0.70,0.30,0.10,0.30.
- SA T0=0.005 in the existing raw objective units; alpha=0.9982.
- kappa=1, target violation=0.005, destroy fraction=0.20, eta=1.
- Demands, deadlines and 60-second service times remain unchanged.
- No per-solver wall-clock cutoff; each optimization job has a 60-minute limit.

Each P2 profile is the hourly harmonic mean of the three same-type training
dates, excluding the fold's test date. All four dates of each type are tested.
The compact bundle reconstructs the exact full-precision OD profiles from
the renamed September data directory. Export validation compares all 20
instances' daily speeds with their original NPZ archives (zero difference).
Instance and distance files are copied byte-for-byte; hashes are recorded.
These speeds are synthetic, not independent observations of the labeled dates.

P2 uses available hourly traffic 06:00-18:00 so the pilot can detect finishes
after 17:00; this does not change the agreed P0 window used for S1. The
corrected evaluator changes speeds at clock-hour boundaries and holds the
last speed after the available profile ends. Profiles are exported with 12
significant digits, without the one-decimal presentation rounding.

Completion means dispatch time plus makespan, including service and final
depot returns. A case passes only if both its plan and held-out replay are
feasible and finish no later than 17:00. A complete pass requires all 240
cases, the expected configuration and no duplicates. This is evidence for
one seed and these scenarios, not a universal bound for all seeds/policies.

Artifacts:
- `hanoi-p2-fourfold-case-<task-key>`: logs, final routes, histories,
  task commands, profile hashes, individual results, partial summaries.
- `hanoi-p2-fourfold-final-report`: `all_runs.csv`, `report.json`, and
  `P2_4fold_start10_results.xlsx`. Missing cases or bad configuration fail
  aggregation; infeasible or late cases are explicitly listed/reported.

The input bundle is `.github/data/hanoi_p2_fold_pilot`. It is about 11 MB
and excludes map downloads, caches, secrets, generated speed text exports,
and historical results. Rebuild locally with:

```sh
python scripts/hanoi_p2_cloud_pilot.py build-bundle --data-root "$HANOI_DATA_ROOT"
```

The offline bundle exporter also requires the existing path-builder's
dependencies. GitHub runners only need `requirements-hanoi-p2-pilot.txt`.
