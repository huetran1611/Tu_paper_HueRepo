import glob
import json
import os
from pathlib import Path

root = Path("instance_time_dependent")
drone_caps = ["2.27", "5.0", "10.0"]
modes = ["multi_trip", "one_trip"]
runs = [1, 2, 3, 4, 5]

instances = []
for path in sorted(glob.glob(str(root / "*.txt"))):
    name = Path(path).name
    if name.endswith(".vmax_ij.txt") or name.endswith(".theta_ijl.txt"):
        continue
    base = name.removesuffix(".txt")
    parts = base.split(".")
    if len(parts) != 3:
        continue
    customer_count = int(parts[0])
    if customer_count not in (200, 500):
        continue
    gridsize = int(parts[1])
    required = [
        root / f"{base}.txt",
        root / f"{base}.vmax_ij.txt",
        root / f"{base}.theta_ijl.txt",
    ]
    missing = [str(p) for p in required if not p.exists()]
    if missing:
        raise SystemExit(f"Missing required files for {base}: {missing}")
    instances.append({
        "base": base,
        "file": str(root / f"{base}.txt"),
        "vmax_file": str(root / f"{base}.vmax_ij.txt"),
        "theta_file": str(root / f"{base}.theta_ijl.txt"),
        "number_of_customer": customer_count,
        "gridsize": gridsize,
        "weight": 1 if customer_count == 200 else 3,
    })

instances.sort(key=lambda x: (x["number_of_customer"], x["gridsize"], x["base"]))
if len(instances) != 32:
    raise SystemExit(f"Expected 32 instances for 200/500, found {len(instances)}")

tasks_200 = []
tasks_500 = []
task_id = 0
for inst in instances:
    bucket = tasks_200 if inst["number_of_customer"] == 200 else tasks_500
    for mode in modes:
        for dd in drone_caps:
            for run in runs:
                task_id += 1
                task = dict(inst)
                task.update({
                    "task_id": f"{task_id:04d}",
                    "mode": mode,
                    "drone_capacity": dd,
                    "run": run,
                })
                bucket.append(task)

if len(tasks_200) != 480 or len(tasks_500) != 480:
    raise SystemExit(f"Expected 480 tasks per size, found 200={len(tasks_200)}, 500={len(tasks_500)}")
