import glob
import json
import os
from pathlib import Path

root = Path("instance_time_dependent")
variants = {
    200: [
        {"variant": "truck6_drone4", "trucks": 6, "drones": 4},
        {"variant": "truck4_drone6", "trucks": 4, "drones": 6},
    ],
    500: [
        {"variant": "truck11_drone9", "trucks": 11, "drones": 9},
        {"variant": "truck9_drone11", "trucks": 9, "drones": 11},
    ],
}
expected_per_size = 16

instances_by_size = {}
for size in (200, 500):
    paths = []
    for path in glob.glob(str(root / f"{size}.*.txt")):
        name = Path(path).name
        if name.endswith((".vmax_ij.txt", ".theta_ijl.txt")):
            continue
        base = name.removesuffix(".txt")
        parts = base.split(".")
        if len(parts) != 3:
            continue
        required = [
            root / f"{base}.txt",
            root / f"{base}.vmax_ij.txt",
            root / f"{base}.theta_ijl.txt",
        ]
        missing = [str(p) for p in required if not p.exists()]
        if missing:
            raise SystemExit(f"Missing required files for {base}: {missing}")
        paths.append({
            "base": base,
            "file": str(root / f"{base}.txt"),
            "vmax_file": str(root / f"{base}.vmax_ij.txt"),
            "theta_file": str(root / f"{base}.theta_ijl.txt"),
            "customer_count": size,
        })
    paths.sort(key=lambda row: tuple(int(part) for part in row["base"].split(".")))
    instances_by_size[size] = paths

errors = []
for size, rows in instances_by_size.items():
    if len(rows) != expected_per_size:
        errors.append(f"Expected {expected_per_size} instance/{size}.*.txt files, found {len(rows)}")
if errors:
    raise SystemExit("; ".join(errors))

tasks = []
task_id = 0
for size in (200, 500):
    for inst in instances_by_size[size]:
        for variant in variants[size]:
            for run in (1, 2, 3, 4, 5):
                task_id += 1
                tasks.append({
                    "task_id": f"{task_id:04d}",
                    **inst,
                    **variant,
                    "run": run,
                })

expected_tasks = 320
if len(tasks) != expected_tasks:
    raise SystemExit(f"Expected {expected_tasks} solver tasks, found {len(tasks)}")
