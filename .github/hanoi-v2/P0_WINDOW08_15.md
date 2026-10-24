# P0-only Hanoi V2 rerun on [08:00,15:00)

Workflow: `.github/workflows/hanoi-v2-p0-window08-15.yml`.
Open **Actions → Hanoi V2 - P0 only - window 08 to 15 → Run workflow**
on the branch containing this workflow and its helper script.
The workflow supports a manual trigger once registered by GitHub. On branch `codex/hanoi-v2-p0-window08-15`, changing `.github/triggers/hanoi-v2-p0-window15` and pushing starts the complete experiment with 20 concurrent shards.

- 20 original V2 instances × 4 folds × 10 original seeds × buffers 0/5/10/15% = 3,200 independent P0 optimizations.
- Every feasible plan is replayed unchanged on three held-out dates (9,600 scheduled replay slots).
- Static P0 uses the harmonic mean over nine training dates and seven hourly intervals [08:00,15:00). Start time is 08:00.
- Original V2 input zip, Linux solver, pinned dependencies, 9,000 iterations, truck capacity 40 kg and drone capacity 2.27 kg are unchanged.
- P1, P2 and PI are not optimized. The preparation job recalculates route-independent S1 diagnostics for the three policies at departure 08:00.
- A new protocol ID and all modified source hashes isolate the experiment from the original [08,17) results.
- 160 shards, 20 sequential tasks each; default maximum 20 concurrent shards. Failed technical tasks remain distinct from planning non-success.

Artifacts: `hanoi-v2-p0-window15-capsule`, `hanoi-v2-p0-window15-shard-*`, and `hanoi-v2-p0-window15-report`.
The report includes planning/replay CSVs, coverage, P0 summaries and S1. Shard JSONs embed routes and per-customer event CSVs.
Download into a new directory such as `result_final/result_hanoi_v2/p0_window08_15/`.
Existing results are not overwritten. These are raw reruns: bottom-up buffer selection, cross-policy transfer and pooled PI updates have not been applied.
Gap calculation requires joining the new replays to the existing PI references; the workflow does not manufacture new PI values or silently reuse the old P0 plans.

Local checks use Conda base explicitly. Optimization itself runs on Linux GitHub runners using the frozen Linux binary.
