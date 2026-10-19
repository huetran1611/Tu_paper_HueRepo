# Normal-only multi-trip warm starts

Workflow: `.github/workflows/big-instance-warmstart40.yml`.

66 independent optimization jobs: 29 cases with 200 customers and 37
with 500 customers. Heavy/light and vehicle-sweep cases are excluded.
Each case matches instance, drone payload and nominal repetition slot;
historical repetitions were not explicit independent seeds.

The frozen bundle `.github/data/big-warmstart40.zip` contains the 66
task records, full single-trip warm starts and historical solver source.
Input and speed files are checked out from source commit
`902415a453ae5174403151323de4398f7d456c6d` and checked against frozen
SHA-256 hashes. Truck capacity is 40 kg. Other historical search
parameters and iteration budgets are retained.

Each solver has a 3,540-second soft search limit, leaving approximately
one minute for writing and validating output, and a strict 3,600-second
process timeout. GitHub job timeout includes setup/download/upload time
and is not the solver budget. Twenty jobs may run concurrently subject
to account limits. A separate preparation job builds the Linux solver.

Artifacts `warm40-slot-<slot>-attempt-<attempt>` retain explicit routes,
command, customer freshness events, audit and frozen plan. Valid results
must cover every customer exactly once, respect exact truck/drone loads,
pass the historical freshness/energy evaluator, and not worsen the
feasible single-trip warm start. A hard-timeout job is a failed job,
not a feasible returned result. Other jobs continue on failure.

No local optimization daemon is required. The workflow does not overwrite
local final tables. After download, use only validated outputs to replace
the 66 selected losing multi-trip rows, retaining the baseline snapshot.
These are additional warm-start searches with a shorter time cap than
the original 7,200 seconds; report this explicitly, not as equal-budget
independent reruns or unmodified original observations.
