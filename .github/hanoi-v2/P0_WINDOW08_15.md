# P0-only Hanoi V2 rerun on [08:00,15:00)

Active layout: 13 workflows `hanoi-v2-p0-window15-batch-01.yml` through `-13.yml`, using `hanoi-v2-p0-window15-single-batch.yml`.
Batches 1–12 contain 250 optimization jobs each; batch 13 contains 200. Each matrix job runs exactly one P0 optimization and its three sequential held-out replays. The matrix permits up to 250 jobs concurrently per workflow; account-wide GitHub runner limits still apply.

All batches download the same verified capsule artifact from run 40059267291 (its prepare job succeeded before cancellation). Only inputs, frozen code and S1 are reused, not optimization results. The artifact expires after 90 days; a fresh preparation is required after expiry.

Scope: 20 instances × 4 folds × 10 seeds × 4 buffers (0/5/10/15%) = 3200 optimizations and 9600 scheduled replay slots. P0 uses nine train dates and seven hourly intervals [08:00,15:00); departure 08:00. Original solver, 9000 iterations, truck capacity 40 kg and drone capacity 2.27 kg are unchanged. P1/P2/PI are not optimized.

Push `.github/triggers/hanoi-v2-p0-window15-single-jobs` on branch `codex/hanoi-v2-p0-window08-15` to start all 13 workflows. Each batch exports raw task artifacts and a validated batch report; failed jobs can be rerun independently. Raw task JSONs embed original routes and customer-event CSVs. Download all reports before applying bottom-up buffer selection or updating pooled PI.

The former 160-shard workflow is retained for provenance, with its push trigger removed. Its run was cancelled at the user's request.
