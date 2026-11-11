# Automatic continuation of Tables 6–8

This supersedes the original simultaneous launch policy in PAPER_TABLES_678.md.
The frozen dataset, solver, seeds, Table-5 parameters and 9600 task mappings stay unchanged.

- One active batch per campaign, with `max-parallel: 20` in the reusable worker.
- Setup/resume dispatches only the first missing batch. The coordinator advances
  the chain on a batch `workflow_run: completed` event. It does not wait for solvers.
- A scheduled watchdog checks every 20 minutes, providing recovery if an event is
  missed. GitHub may delay scheduled runs; this is not a precise timing guarantee.
- Setup/resume and all coordinator invocations share `paper-t678-dispatch`
  concurrency. An active campaign batch blocks further dispatches.
- Only unexpired `-ok` task artifacts count as finished. Missing slots are resumed
  in a NEW batch run, avoiding the old startup_failure rerun problem. Solver jobs
  without a saved completion artifact restart from the beginning.
- Five runs of the same batch pause automatic retries to avoid an endless loop.
  Inspect its errors; setup/resume can explicitly restart the chain afterward.
- Frozen-bundle requests retry six times after the initial attempt, respecting
  Retry-After / X-RateLimit-Reset, plus exponential delay and random jitter.
  Permanent errors such as 404 fail immediately. Redirects strip the API token.
- Job timeout is now 240 minutes to accommodate API waits. The solver's existing
  7200-second wall limit is unchanged; download waits happen before solver launch.

## Current campaign

Start **Paper T678 automatic coordinator** once with `setup_run_id=43077064461`.
It inventories existing results and dispatches only missing slots. Do not manually
dispatch all 39 batches. Existing active batches are allowed to finish first.
The computer used to start the action can be turned off.

Repository Actions variables:

- `PAPER_T678_SETUP_ID`: campaign used by the watchdog (default: 43077064461).
  Change this for a future newly prepared campaign. Completion events carry their
  own original setup ID.
- `PAPER_T678_AUTORUN=false`: pause the coordinator, including scheduled/manual
  invocations. This does not cancel already running solver jobs. Remove it or set
  it to true to resume. Disable the coordinator workflow after all results have
  been collected to stop scheduled inventory checks.

The 20-job limit applies to automatically managed solver batches. Unrelated
workflows, local irace, manually launched batches and other campaigns have their
own execution limits. Report collection remains a separate manual workflow.

Validation: local unit tests exercise serialization, missing-slot continuation,
retry-loop protection, header-based backoff, token stripping and API error retries.
Generated batch workflows retain 250/100 jobs and their original task mappings.
