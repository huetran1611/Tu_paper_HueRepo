# Hanoi V2 GitHub Actions Rerun

Entry workflow: `.github/workflows/hanoi-v2.yml`.
Reusable batch: `.github/workflows/hanoi-v2-batch.yml`.
Immutable bootstrap: `.github/actions/hanoi-v2-bootstrap/action.yml`.

## Exact Scope

- New V2 group-permuted traffic for the same 20 Hanoi instances and 12 dates.
- Buffers **0%, 5%, 10%, 15% only**; no 0.5%, 20%, heavy/light or Big instances.
- Four folds; each fold holds out one Monday, Saturday and Sunday.
- Seeds 42--51, departure 08:00, P0 pooling window [08:00,17:00).
- P0/P1 train on nine dates and reuse their fixed plans for three test day types.
- P2 trains on the three remaining dates of its own type.
- PI is optimized anew on each test date, without buffer; shared across policies/buffers.
- One truck at 40 kg and one drone at 2.27 kg; all original solver flags and 9,000 iterations.
- Planning freshness 60, 57, 54, 51 minutes; replay always checks the original 60 minutes.
- Service times and exact original solver/evaluator are unchanged.
- No incumbent transfer, posthoc pooled PI, cross-buffer replacement or reused V1 replay.

16,000 policy optimizations + 2,400 PI = **18,400 optimizations**.
28,800 scheduled policy replay cases; 800 cases per day/policy/buffer point.

| Group | Optimization jobs | Batches (at most 250 jobs each) |
|---|---:|---:|
| PI | 2,400 | 1--10 |
| P0 | 3,200 | 1--13 |
| monday | 4,268 | 1--18 |
| saturday | 4,268 | 1--18 |
| sunday | 4,264 | 1--18 |

Day groups distribute shared P1 jobs across the three workflows and include
same-type P2. P1 is NOT optimized three times for the three test day types.
There are 77 batch workflow runs, plus one initialize and one aggregate.
Each batch also has preparation/packing jobs; these are not optimizations.
max_parallel is per batch, NOT a global account limit. Avoid launching every
batch concurrently unless the available GitHub quota and cost are acceptable.

## Run Order

1. Commit the two workflows, bootstrap action, runner, and BOTH files in
   `.github/hanoi-v2/` to the branch used for the experiment. No expanded
   615-MB CSV tree is needed on GitHub. Confirm the new entry workflow is visible
   in Actions before dispatching it; a new dispatch-only workflow may need its
   definition registered on the default branch first. The ZIP contains the compact V2 inputs,
   original Linux solver and hash-verified historical runtime/source files.
2. Dispatch `hanoi-v2.yml` with `mode=initialize` once. Wait for success and
   record its run ID. It validates/freezes all inputs, computes S1 from profiles
   (no route search) and uploads `hanoi-v2-capsule`.
3. Dispatch `mode=batch` using that NEW setup_run_id and the group/batch pairs
   in the table. PI batches can be completed first for easier auditing; policy
   batches do not require PI results to start. Every optimization has its own
   independent matrix job and raw artifact.
4. When all 77 batches succeed, dispatch `mode=aggregate` with the same setup
   ID. It downloads only V2 batch artifacts and checks all expected task keys.
   Missing/invalid/conflicting retries are disclosed; they are not counted as NP.

Example after the code is pushed (replace REF and NEW_SETUP_ID):

```bash
gh workflow run hanoi-v2.yml --ref REF -f mode=initialize
gh workflow run hanoi-v2.yml --ref REF -f mode=batch \
  -f setup_run_id=NEW_SETUP_ID -f group=PI -f batch=1 -f max_parallel=20
gh workflow run hanoi-v2.yml --ref REF -f mode=aggregate \
  -f setup_run_id=NEW_SETUP_ID
```

## Preservation and Reporting

Raw artifacts use `hnv2-<setup>-raw-<task>-run-<run>-a<attempt>`.
Batch artifacts use `hanoi-v2-<setup>-batch-...`.
Artifacts include failed-job outputs, full route text, command, events and audits.
Packed task records embed explicit planning routes and replay customer events,
so final analysis does not depend on GitHub links to route files.
Retention is 90 days: download and archive the complete capsule and results.
Do not blindly overwrite or choose conflicting retry results using test outcomes.

Final outputs include raw CSVs, an Excel workbook, unbuffered traffic-information
LaTeX, S1 CSV/LaTeX, and bufferI figures for BOTH casewise mean gap and the newer
ratio-of-mean-durations gap. Neither filters replay freshness for durations.
Hollow figure markers and coverage columns disclose incomplete plan/PI pairing.
PI is a newly computed heuristic reference, not a proven optimum or posthoc pool.

Nothing overwrites `result_hanoi_final` or automatically imports results into V2.
Optimization batches and aggregation are dispatch-only. For initial registration
without changing the default branch, changing `.github/triggers/hanoi-v2-initialize`
on branch `codex/hanoi-v2-20261020` starts capsule initialization/S1 only.
Other commits do not automatically start compute.
The one-hour GitHub job timeout includes setup; the optimization itself retains
the original 9,000-iteration budget, not the Big-instance one-hour search cap.
This code has local data/matrix/integration tests; the Linux optimization jobs
must still be verified on Actions. No local production search is required.

To regenerate the compact upload package from current verified V2 data:

```bash
/Users/huetran/anaconda3/bin/python -B scripts/hanoi_v2_experiment.py export-inputs
```

The historical source is recovered read-only from commit
`d8498413299123c36bd36037e70f23533afe6510`, verified against the original capsule.
Do not replace its solver binary/source with a modified current working-tree
version and still describe the rerun as changing only traffic data.

## Authorized Continuation After Pilot

`.github/workflows/hanoi-v2-continue.yml` waits for Monday batch 1 (run
39416901265) of V2 setup 39416770489. On successful completion it downloads
the packed pilot, checks all 250 records against the frozen protocol, and
dispatches only the remaining 76 unique group/batch pairs. Existing matching
runs are skipped; conflicting duplicates require review. Technical failure or
incomplete records stop continuation. RF and test feasibility are NOT gating
criteria. All four buffers remain 0%, 5%, 10%, 15%.

The controller runs on GitHub, not as a local daemon. It waits at most 120
minutes for the pilot and preserves a launch-state artifact even on failure.
Dispatching all batches does not mean optimization has completed. Aggregation
still requires every batch to finish. Its explicit push trigger is
`.github/triggers/hanoi-v2-continue-after-pilot` on the dedicated V2 branch.
