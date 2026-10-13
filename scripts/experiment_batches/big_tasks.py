import glob
import json
import os
from pathlib import Path

scenarios = ["light", "heavy"]
scenario_roots = {name: Path("big_instance") / name for name in scenarios}
instances = []

for scenario, root in scenario_roots.items():
    for path in sorted(glob.glob(str(root / "*.txt"))):
        name = Path(path).name
        if name.endswith(".vmax_ij.txt") or name.endswith(".theta_ijl.txt"):
            continue
        base = name.removesuffix(".txt")
        parts = base.split(".")
        if len(parts) != 3:
            continue
        customer_count = int(parts[0])
        required = [
            root / f"{base}.txt",
            root / f"{base}.vmax_ij.txt",
            root / f"{base}.theta_ijl.txt",
        ]
        missing = [str(p) for p in required if not p.exists()]
        if missing:
            raise SystemExit(f"Missing required files for {scenario}/{base}: {missing}")
        instances.append({
            "scenario": scenario,
            "base": base,
            "file": str(root / f"{base}.txt"),
            "vmax_file": str(root / f"{base}.vmax_ij.txt"),
            "theta_file": str(root / f"{base}.theta_ijl.txt"),
            "number_of_customer": customer_count,
            "weight": 1 if customer_count == 200 else 3,
        })

instances.sort(key=lambda x: (x["scenario"], x["number_of_customer"], x["base"]))
if len(instances) != 64:
    raise SystemExit(f"Expected 64 big instances across heavy/light, found {len(instances)}")

tasks = []
task_id = 0
for inst in instances:
    for run in (1, 2, 3, 4, 5):
        task_id += 1
        task = dict(inst)
        task.update({
            "task_id": f"{task_id:04d}",
            "drone_capacity": "2.27",
            "run": run,
        })
        tasks.append(task)

if len(tasks) != 320:
    raise SystemExit(f"Expected 320 tasks, found {len(tasks)}")
