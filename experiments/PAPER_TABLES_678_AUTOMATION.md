# Tables 6?8: refill free solver capacity

Current policy supersedes whole-batch serialization. The dataset, frozen solver,
Table-5 parameters, seeds and 9600 task mappings remain unchanged.

- The coordinator aims for at most **20 outstanding solver jobs per campaign**,
  counting running, queued/waiting and reserved-but-not-yet-visible jobs.
- If a batch has three jobs left and nothing else is pending, it prepares up to
  17 missing slots. Those slots may belong to another batch or to another wave
  of the same batch. Each workflow still contains at most 250 jobs and each job
  runs exactly one solver; automatically dispatched waves contain at most 20.
- Per-batch workflow concurrency is removed so a slow job cannot block a new wave.
  Coordinator and setup share the `paper-t678-dispatch` concurrency lock.
- Completed `-ok` artifacts and running/pending slots are excluded. Successful
  jobs whose artifact publication is temporarily invisible are also reserved.
- A reservation plan is uploaded BEFORE dispatch. Its ten-minute lease protects
  against controller crashes and GitHub run-list visibility delays. New run names
  carry their selected slots so queued runs reserve capacity before matrix jobs
  appear. Unknown legacy runs reserve the entire batch until jobs are visible.
- Dispatch rechecks capacity after uploading the lease. A stale plan is not sent;
  ambiguous POST requests are not retried. A failed slot is paused after five
  attempts, without blocking other slots in its batch.
- The controller runs on batch completion and on a five-minute scheduled watchdog.
  It exits after checking/refilling; it does not hold a runner waiting for solvers.
  GitHub can delay events and scheduled runs, so instantaneous refill is not guaranteed.
- Inventory caches completed-run artifact/job metadata for up to six hours.
  Rerun attempt/status changes invalidate the cache. Active runs are refreshed on
  every check. This reduces REST quota consumption compared with rescanning all
  completed jobs every five minutes.
- Setup/resume only requests the coordinator; it cannot bypass the global budget
  by launching a whole batch directly.

## Existing manual runs

Manually launched/rerun jobs are counted. If a legacy manual run already has more
than 20 jobs queued/running, the coordinator waits until that backlog falls below
20. It does not cancel user jobs. New manual runs can exceed the managed limit;
they are external to the coordinator's reservation lock.

## Operation

Start **Paper T678 automatic coordinator** with original setup ID `43077064461`.
It keeps valid results and fills missing slots only. The local computer can be off.

Repository Actions variables:

- `PAPER_T678_SETUP_ID`: campaign used by the scheduled watchdog (default 43077064461).
- `PAPER_T678_AUTORUN=false`: pause coordinator invocations without cancelling solvers.

Disable the coordinator workflow after collecting the complete campaign to stop
periodic inventory checks. Report collection remains a separate manual workflow;
it aggregates multiple waves/reruns using the latest valid completion artifact
for each `(batch, slot)`. Reservation artifacts are not solver results.

Bundle downloads retain header-aware rate-limit backoff and token stripping on
redirects. Solver wall limit remains 7200 seconds, while job timeout allows up to
240 minutes including API waits. No local irace artifact is changed by this work.

Validation: 15 local tests cover 9600-task mapping, one solver per job, tail refill,
queue/lease accounting, overlapping batches, real duplicate job counts, rerun cache
invalidation, publish-before-dispatch, stale plans, manual launches during planning,
per-slot retry limits and existing bundle-download/report behavior.
