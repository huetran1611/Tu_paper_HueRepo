# Workload tie-breaking: 960 independent solver runs

Two variants, both makespan-only with L2-workload tie-breaking; no SA, destroy, repair or objective switching.

- Dataset `6_100`: all 80 instances in `instance_time_dependent/6_100_customers`, including 6, 10, 12, 20, 50 and 100 customers.
- Dataset `200`: only the 16 instances with 200 customers in `instance_time_dependent/big_instance/normal`. The 500-customer files in that directory are intentionally excluded.
- Every instance retains the truck/drone fleet counts from its header. Truck capacity 40 kg; drone capacity 2.27 kg.
- Five matched seeds per variant and instance: 42, 43, 44, 45, 46. One attempt and exactly one solver process per experiment job.
- 96 × 2 × 5 = 960 experiment jobs, in batches of 250, 250, 250 and 210. Two tiny smoke processes during setup are validation only, not experiment repetitions.

| Customers | Base iterations | Doubled iterations | Iterations/segment |
|---:|---:|---:|---:|
| 6 | 162 | 324 | 18 |
| 10 | 360 | 720 | 30 |
| 12 | 432 | 864 | 36 |
| 20 | 900 | 1800 | 60 |
| 50 | 3600 | 7200 | 150 |
| 100 | 9000 | 18000 | 300 |
| 200 | 27000 | 54000 | 600 |

The formula is the current solver's `n * 9 * ceil(sqrt(n))`, and segment length `n * ceil(sqrt(9))`. The driver passes `--auto-tune --attempts=1 --time-limit=0 --workload-tie-break=1`. The doubled executable multiplies the resolved budget by 2. Stagnation does not stop search. Only a full, consecutive iteration CSV of the expected length passes validation. A GitHub-hosted job still has the platform's 360-minute maximum; a platform cancellation/timeout is a technical failure and is never reported as a completed run.

Each job uses its matching `.vmax_ij.txt` and `.theta_ijl.txt` files. The input ZIP contains all 288 files, verified against SHA-256 hashes in inputs.json. The source snapshots, native Linux binaries and manifest are shared through one setup artifact.

The setup workflow compiles both executables and semantic tests with g++, verifies all inputs and task counts, and performs two complete small TD smoke runs. After setup succeeds, the four batch workflows reuse its immutable artifact and create one matrix job per solver invocation. The account-wide GitHub concurrency limit governs the actual number running simultaneously.

Artifacts retain per-run objective, L2 workload, feasibility, actual iteration counts, seed/configuration, routes, iteration history and compressed stdout/stderr. Each batch validates exact coverage and exports all_runs.csv and summary.csv. Missing/failed tasks are disclosed separately from infeasible returned solutions. Rerunning failed jobs is supported.

Launch on branch `codex/workload-tiebreak-5seeds` by pushing the setup trigger, then filling the successful setup run ID into the four batch callers and pushing `.github/triggers/workload-tiebreak-batches`. Trigger files are intentionally separate so batch optimization cannot begin before compilation and validation succeed.
