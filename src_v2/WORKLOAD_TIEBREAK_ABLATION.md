# Makespan-only workload tie-breaking ablations

The two C++ entry points are:

| Version | Entry point | Iterations per attempt |
|---|---|---|
| Makespan only, workload tie-breaking | `tabubu_TimeDependent_v2_makespan_workload_tiebreak.cpp` | N |
| Makespan only, workload tie-breaking, doubled budget | `tabubu_TimeDependent_v2_makespan_workload_tiebreak_double_budget.cpp` | 2N |

Both use `tabubu_TimeDependent_workload_tiebreak_impl.hpp`. Compile either entry point by itself; the header is included automatically. The original `tabubu_TimeDependent_v2.cpp` is unchanged. The derived implementation records its source SHA-256 in its first line.

Search candidates are ranked lexicographically:

1. Original penalized makespan: `Cmax * (1 + lambda_cap * cap + lambda_energy * energy + lambda_deadline * deadline)`.
2. Only when this primary score ties within 1e-8 seconds, lower workload `W = sqrt(sum_k(T_k*T_k))`, using total route time for every truck and drone. Workload also has an absolute tie tolerance of 1e-8 seconds.
3. If both keys tie, retain the original reservoir random tie-break among neighborhood candidates.

The final feasible incumbent is ranked by raw makespan first, then workload. The cross-attempt sort uses exact makespan and workload comparisons to retain a strict weak ordering. Workload is never added as a weighted term to makespan. There is no alternate workload-optimization mode.

The search contains no simulated annealing, destroy step, repair step or stagnation perturbation. A candidate is accepted only when it improves the lexicographic search key. Each attempt resets tabu state and penalties, and reseeds the tie-breaking RNG with `seed + attempt_index`; this keeps initial construction matched even after preceding 1x/2x attempts consume different random numbers. The original initial-solution constructor, nine makespan-oriented neighborhoods, critical-vehicle filtering, tabu tenures/attributes, adaptive penalties and adaptive neighborhood roulette scheduling are retained. Rejected moves do not commit pending tabu attributes. Insertion while building the initial solution remains part of initialization, not a remove-and-repair search step.

`N` is the final configured iteration budget after `--iters`, `--segments` and `--auto-tune` are resolved. The doubled version multiplies that budget by two and recomputes the number of segments. The base time limit defaults to 7200 seconds, so the normal process stops at N iterations or two hours and the doubled process stops at 2N iterations or four hours, whichever comes first. A positive `--time-limit` overrides the base seconds and is multiplied by the same 1x/2x factor. `--time-limit=0` explicitly disables the cap for diagnostic runs.

The monotonic process deadline starts at main entry, includes initialization, and is shared across attempts. A checkpoint is tested before each search iteration and before candidate scoring. When it expires, the interrupted iteration is discarded and the best completed incumbent is saved. Initialization and individual route evaluations are not forcibly interrupted; the cap is cooperative, with a small finalization overrun possible. The experiment uses exactly one attempt per process. Its external watchdog allows five minutes of shutdown/export grace beyond the native cap, then treats a stuck process as a technical failure; it is not an alternative search budget. The GitHub solve job timeout is 360 minutes (six hours), leaving room for runner setup and artifact export after the maximum four-hour solver budget.

`Search termination` records `iteration_limit` or `time_limit` and the number of completed iterations. Aggregation accepts correctly validated time-limited results without pretending that the iteration maximum was reached. A fresh protocol-v2 setup bundle is required; old unlimited-time bundles are rejected. Batch workflows accept a setup run ID or resolve the latest successful setup on their branch. No new batches are launched automatically by changing these files.

With no iteration flags, the original iteration defaults remain N=200000 and 200 attempts, but all attempts now share the process time cap. Set `--attempts=1` for the usual matched-seed comparison.

## Build and run

From the repository root:

```sh
clang++ -O3 -std=c++20 src_v2/tabubu_TimeDependent_v2_makespan_workload_tiebreak.cpp -o build/tabubu_TimeDependent_v2_makespan_workload_tiebreak
clang++ -O3 -std=c++20 src_v2/tabubu_TimeDependent_v2_makespan_workload_tiebreak_double_budget.cpp -o build/tabubu_TimeDependent_v2_makespan_workload_tiebreak_double_budget
```

The same commands work with `g++` on Linux. Run each experiment from a separate output directory because the historical output filenames are retained.

For the same instance, seed, fleet and other settings, pass `--attempts=1 --iters=9000 --seed=42` to both versions: the first executes 9000 iterations and the second 18000. Add the same `--segment-iters`, time-dependent speed files and capacity flags to both as required by the experiment.

To isolate workload's effect, run the first executable again with **`--workload-tie-break=0`**. This provides a makespan-only control with the same search controller, neighborhood set, budget and lack of SA/destroy/repair. Comparing against the unmodified original solver would also change those mechanisms and would not isolate workload. The flag defaults to 1 in both versions.

Output includes `Workload L2` in `output_solution_best.txt` and counters for workload-only tie wins/accepted moves on stdout. The usual `output_mode_0.txt` iteration history is preserved. Removed feature flags such as `--T0`, `--alpha`, `--h-mode`, `--h-div` and `--r-destroy` are rejected; unknown options are rejected rather than silently ignored.

## Validation

```sh
clang++ -O2 -std=c++20 scripts/test_workload_tiebreak.cpp -o build/test_workload_tiebreak
/Users/huetran/anaconda3/bin/python scripts/test_workload_ablation.py
```

The tests cover primary-objective priority, workload ties, aspiration, disabled-workload behavior, feasible full-customer assignments, iteration/segment/auto-tuned 1x versus 2x budgets, identical trajectory prefixes without a time cap, repeatability and rejection of removed feature flags. Validation is saved to `build/workload_ablation_validation.json`. These checks verify implementation behavior; they do not establish a general solution-quality improvement from tie-breaking.
