# Hanoi: baseline versus 10% freshness buffer

This setup implements the main unbuffered table and comparison with buffer
0.1. It does not rerun S1 and does not run buffer 0.2.

## Protocol

- Source data: `instance_hanoi/datasets/hanoi_traffic/Data_Chốt_9.2026`.
- GitHub payload: `.github/data/hanoi_protocol_buffer10`. Despite its older
  directory name, it contains the original daily traffic profiles, not
  buffered traffic. All 20 instances were reconstructed exactly from the
  source data (maximum speed discrepancy zero).
- 20 instances, 100 customers each, 1 truck (40 kg), 1 drone (2.27 kg).
- Dispatch 08:00; service times unchanged; original freshness 3,600 seconds.
- Four folds hold out one Monday, Saturday, and Sunday each. Fold 1 dates:
  07/09, 05/09, 06/09/2026; fold 2: 14/09, 12/09, 13/09; fold 3:
  21/09, 19/09, 20/09; fold 4: 28/09, 26/09, 27/09.
- P0: harmonic aggregation of nine training dates and the nine hourly
  intervals `[08:00,17:00)`, separately for each OD pair. Its speed is static.
- P1: hourly harmonic aggregation of nine training dates.
- P2: hourly harmonic aggregation of three same-type training dates.
- P0/P1 plans are computed once per instance/fold/seed/buffer and replayed
  unchanged on all three held-out dates. P2 is specific to the day type.
- Seeds 42..51; 9,000 Tabu iterations; 200 iterations per segment; remaining
  selected parameters are in `hanoi_pilot_selected.FLAGS`.
- Policies run at buffer 0 (planning limit 60 min) AND buffer 0.1 (54 min).
  Every replay uses the original 60-minute limit. PI uses the test profile
  and the original limit, is computed once, and is shared between buffers.
- Freshness is from pickup completion to the return of that customer's
  own trip. Makespan includes service and ends at the final vehicle return.
- Beyond the available traffic horizon, the solver holds the final speed.
  Replays finishing after 18:00 are explicitly counted in the report.
- September profiles are synthetic, not twelve independent observed dates.

## Five Workflows

| File | Optimizations | Batches | Last batch |
|---|---:|---:|---:|
| `hanoi-full-p0.yml` | 1,600 | 7 | 100 jobs |
| `hanoi-full-monday.yml` | 2,134 | 9 | 134 jobs |
| `hanoi-full-saturday.yml` | 2,134 | 9 | 134 jobs |
| `hanoi-full-sunday.yml` | 2,132 | 9 | 132 jobs |
| `hanoi-full-pi.yml` | 2,400 | 10 | 150 jobs |

Total: 10,400 optimizations, at most 14,400 replays, 44 batch runs.
Each batch has at most 250 compute jobs, plus two orchestration/report jobs.
Each compute job does exactly one optimization, then zero (PI), one (P2),
or three (P0/P1) replays. These are not 10,400 simultaneously running jobs.
The account's GitHub concurrency quota still applies across all workflows.

Each day workflow owns all P2 plans for that day type and a deterministic
share of P1 plans. Every P1 plan is owned by exactly one day workflow and
replayed on ALL three held-out day types; P1 is not optimized three times.
Both buffers for a P1 instance/fold/seed have the same owner. Final summaries
group by the actual replay day, not by the job's owner workflow. The day
workflows together perform 1,600 P1 and 4,800 P2 optimizations.

## Run Order

The workflows and all local helper scripts/data must be committed and pushed
before use. These files have been prepared locally; preparing them does not
dispatch a workflow, make a commit, or push a branch.

1. Open **Hanoi PI** in GitHub Actions, choose `mode=initialize`, and run it.
   Leave `setup_run_id` blank. This builds and tests the solver, checks all
   source instances, and uploads `hanoi-full-capsule`. Record this run ID.
   Do not rerun initialization in place: start a new initialization run if
   code/data/protocol changes. The new run is a separate experiment.
2. After initialization succeeds, run P0 or one of the day workflows with that
   `setup_run_id`, `batch=1`, and e.g. `max_parallel=20`. Check its artifacts
   before dispatching the other batches.
3. Run every batch in the table above. For PI, use `mode=batch`, the same
   initialization ID, and batches 1..10. Policy batches may run independently
   or concurrently; none needs another policy's results to save its own work.
4. Once all required batches have finished, run **Hanoi PI** again with
   `mode=aggregate` and the same initialization ID. It fetches packed records
   across all five workflows, checks coverage, pairs PI, and exports reports.

GitHub CLI equivalents (after pushing and replacing the shell variables):

```sh
gh workflow run hanoi-full-pi.yml --ref "$BRANCH" -f mode=initialize
# After it succeeds, set SETUP to that initialization run's numeric ID.
gh workflow run hanoi-full-p0.yml --ref "$BRANCH" -f setup_run_id="$SETUP" -f batch=1 -f max_parallel=20
gh workflow run hanoi-full-monday.yml --ref "$BRANCH" -f setup_run_id="$SETUP" -f batch=1 -f max_parallel=20
gh workflow run hanoi-full-pi.yml --ref "$BRANCH" -f mode=batch -f setup_run_id="$SETUP" -f batch=1 -f max_parallel=20
# Repeat the relevant batch numbers before final aggregation.
gh workflow run hanoi-full-pi.yml --ref "$BRANCH" -f mode=aggregate -f setup_run_id="$SETUP"
```

## Results And Recovery

- Every compute job uploads its own saved solution, customer events, exact
  commands, solver log, compressed 9,000-iteration trace, and result record.
- Artifact names include initialization ID, task identity, workflow run ID,
  and retry attempt. A retry cannot overwrite earlier artifacts.
- A batch uploads `batch_coverage.json` and its completed records even if
  some jobs fail. Technical failure is missing data, NOT planning NP.
- Use **Re-run failed jobs** for technical failures; keep the same
  initialization ID, policy, batch, seed, and buffer. Do not change seeds or
  replace unfavorable results. Uploads have 90-day retention (subject to
  repository policy); aggregate and download results before expiration.
- Identical successful retries are deduplicated and counted once. Different
  records for the same task are reported as conflicts, not silently selected.
- A freshness-violating replay is retained, with its duration and gap.
  Overall solver infeasibility is not used as a substitute for freshness RF.
- All batches use the same frozen solver, code commit, traffic data and
  manifest. Source/data mismatches stop the run rather than mixing results.

The final artifact contains:

- `all_planning_runs.csv`: all 10,400 planning/PI records.
- `all_replay_cases.csv`: all 14,400 scheduled policy cases; absent plans have
  blank replay metrics, not zero values.
- `heldout_summary.csv`: 18 rows, nine per buffer; NP, pooled RF and its four
  date-specific range, paired mean gap, conditional maximum excess, severity,
  and returned-plan/PI-pair counts.
- `hanoi_information_table.tex`: the requested **without a buffer** table.
- `hanoi_buffer10_table.tex`: the matching table for buffer 0.1.
- `buffer_comparison.csv`: nine policy/day comparisons. RF/NP changes use
  percentage points; duration comparisons use the intersection of available
  baseline/buffer plans, without removing freshness failures. PI-gap changes
  further require a feasible shared PI reference. Coverage is explicit.
- `coverage.json`: missing, conflicting, invalid, deduplicated, and horizon
  counts. Incomplete experiments retain diagnostic CSVs but emit no LaTeX table.

RF ranges describe four test dates, not confidence intervals. PI is a
feasible heuristic reference, not a proven optimum. Negative gaps are kept.
When no replay violates freshness, conditional mean maximum excess is blank,
not zero. Missing PI does not invalidate RF but leaves the relevant gap blank.

## Files To Include When Publishing

Include the five `hanoi-full-*.yml` workflows, the
`.github/actions/hanoi-capsule-bootstrap` composite action, the complete
`.github/data/hanoi_protocol_buffer10` compact bundle, and these scripts:

```text
hanoi_full_experiment.py
test_hanoi_full_experiment.py
hanoi_pilot_event_bridge.cpp
test_hanoi_pilot_event_bridge.py
test_hanoi_solver_clock.cpp
hanoi_buffer10_cloud.py
hanoi_p2_cloud_pilot.py
hanoi_protocol_pilot.py
hanoi_pilot_selected.py
requirements-hanoi-full.txt
requirements-hanoi-p2-pilot.txt
```

The existing production solver `src_v2/tabubu_HanoiTraffic_v2.cpp` must be
included at the same revision. Do not stage the full multi-GB source dataset,
local collector state, API credentials, or unrelated working-tree changes.
