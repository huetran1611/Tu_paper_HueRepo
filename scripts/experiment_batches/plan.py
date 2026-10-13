import argparse
import hashlib
import json
import os
import runpy
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
COUNTS = {"big": 320, "trip": 960, "vehicle": 320}
RESULT_DIRS = {
    "big": "big_instance_dd227_results",
    "trip": "td_trip_capacity_results",
    "vehicle": "instance_vehicle_sweep_results",
}
COLLECT_DIRS = {
    "big": "collected_big_instance_dd227",
    "trip": "all_td_trip_capacity_artifacts",
    "vehicle": "collected_instance_vehicle_sweep",
}


def all_tasks():
    result = []
    for kind, expected in COUNTS.items():
        namespace = runpy.run_path(str(SCRIPT_DIR / f"{kind}_tasks.py"))
        tasks = (namespace["tasks_200"] + namespace["tasks_500"]
                 if kind == "trip" else namespace["tasks"])
        if len(tasks) != expected:
            raise ValueError(f"Expected {expected} {kind} tasks, found {len(tasks)}")
        for task in tasks:
            task = dict(task)
            for field in ("file", "vmax_file", "theta_file"):
                task[field] = task[field].replace("\\", "/")
            task.update(experiment=kind, global_id=f"{len(result) + 1:04d}")
            result.append(task)
    return result


def plan_hash(tasks):
    payload = json.dumps(tasks, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def batch_tasks(tasks, batch):
    if batch not in range(1, 8):
        raise ValueError("Batch must be between 1 and 7")
    return tasks[(batch - 1) * 250:batch * 250]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch", required=True, type=int, choices=range(1, 8))
    args = parser.parse_args()
    tasks = all_tasks()
    digest = plan_hash(tasks)
    selected = batch_tasks(tasks, args.batch)
    matrix = [{
        "job_id": task["global_id"],
        "experiment": task["experiment"],
        "result_dir": RESULT_DIRS[task["experiment"]],
        "task_json": json.dumps(task, separators=(",", ":")),
    } for task in selected]
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            handle.write(f"matrix={json.dumps(matrix, separators=(',', ':'))}\n")
            handle.write(f"plan_hash={digest}\n")
            handle.write(f"task_count={len(selected)}\n")
    else:
        print(json.dumps(matrix, separators=(",", ":")))


if __name__ == "__main__":
    main()
