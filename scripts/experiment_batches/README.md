# Five-run experiments

Every configuration on every instance has five independent solver invocations.
Truck capacity remains 1000 kg. Drone capacity is 2.27 kg for heavy/light and
vehicle sweep; trip/capacity uses 2.27, 5.0 and 10.0 kg. Drone energy remains
7200000 J. The existing solver invocation and result parsing are preserved.

| Experiment | Calculation | Solver processes |
| --- | --- | --- |
| Heavy/light | 32 instances x 2 traffic scenarios x 5 runs | 320 |
| Trip/capacity | 32 instances x 2 trip modes x 3 drone capacities x 5 runs | 960 |
| Vehicle sweep | 32 instances x 2 fleet configurations x 5 runs | 320 |
| Total | | 1600 |

Run all seven dispatch workflows from the same commit and with the same solver
time limit. Each solve job invokes exactly one solver process. Prepare jobs build
the binaries; they are additional GitHub jobs, not solver processes.

No workflow sets `max-parallel`. GitHub schedules as many solver jobs concurrently
as runner availability and the account's concurrency limit permit. All seven
flows share that account limit; 250 jobs in a flow does not guarantee 250 runners.

| Batch | Workflow file | Solver jobs | Contents |
| --- | --- | --- | --- |
| 1 | big-instance-heavy-light-dd227.yml | 250 | Heavy/light |
| 2 | experiment-batch-2.yml | 250 | 70 heavy/light + 180 trip/capacity |
| 3 | time-dependent-200-500-trip-capacity.yml | 250 | Trip/capacity |
| 4 | experiment-batch-4.yml | 250 | Trip/capacity |
| 5 | experiment-batch-5.yml | 250 | Trip/capacity |
| 6 | experiment-batch-6.yml | 250 | 30 trip/capacity + 220 vehicle sweep |
| 7 | instance-200-500-vehicle-sweep.yml | 100 | Vehicle sweep |

After all seven runs succeed, dispatch `experiment-batches-summary.yml`. Supply
the seven numeric run IDs in `run_ids`, separated by commas or spaces. Order does
not matter. The collector downloads artifacts across all pages and verifies the
complete plan, unique task IDs, matching commits and matching time limits before
writing the summaries. Use the same experiment plan for the summary workflow.

Download the final `experiments-1600-processes-summary` artifact. It contains
CSV/Excel summaries for heavy/light and vehicle sweep, all-run and best/mean CSVs
for trip/capacity, process totals, and the source run IDs. Mean columns use five
runs. Result artifacts are retained for 90 days.

Run the offline verification from the repository root:

```powershell
python -m unittest discover -s scripts/experiment_batches -p test_pipeline.py -v
```
