import argparse
import csv
import json
import os
import re
import runpy
import statistics
import subprocess
from collections import Counter, defaultdict
from pathlib import Path

from plan import COLLECT_DIRS, COUNTS, SCRIPT_DIR, all_tasks, batch_tasks, plan_hash


def gh_json(*arguments):
    completed = subprocess.run(["gh", *arguments], check=True,
                               capture_output=True, text=True)
    return json.loads(completed.stdout)


def validate_manifests(manifests, tasks):
    expected = {task["global_id"]: task for task in tasks}
    digest = plan_hash(tasks)
    seen = set()
    commits = set()
    limits = set()
    for manifest in manifests:
        task = manifest["task"]
        identity = task["global_id"]
        if identity in seen:
            raise ValueError(f"Duplicate solver result {identity}")
        if task != expected.get(identity) or manifest["plan_hash"] != digest:
            raise ValueError(f"Result {identity} does not match the experiment plan")
        if manifest["batch"] != (int(identity) - 1) // 250 + 1:
            raise ValueError(f"Result {identity} belongs to the wrong batch")
        if manifest["return_code"] != 0:
            raise ValueError(f"Solver {identity} failed")
        seen.add(identity)
        commits.add(manifest["commit"])
        limits.add(manifest["time_limit_sec"])
    if seen != set(expected):
        raise ValueError(f"Expected 1600 results; missing {len(set(expected) - seen)}")
    if len(commits) != 1 or len(limits) != 1:
        raise ValueError("All seven flows must use the same commit and time limit")


def download_runs(repo, run_ids, tasks):
    batches_seen = set()
    for run_id in run_ids:
        info = gh_json("api", f"repos/{repo}/actions/runs/{run_id}")
        if info["status"] != "completed" or info["conclusion"] != "success":
            raise ValueError(f"Run {run_id} must have completed successfully")
        pages = gh_json("api", "--paginate", "--slurp",
                        f"repos/{repo}/actions/runs/{run_id}/artifacts?per_page=100")
        artifacts = [artifact for page in pages for artifact in page["artifacts"]
                     if re.fullmatch(r"experiment-(big|trip|vehicle)-batch-[1-7]-job-\d{4}",
                                     artifact["name"])]
        batches = {int(artifact["name"].split("-batch-")[1].split("-")[0])
                   for artifact in artifacts}
        if len(batches) != 1 or batches & batches_seen:
            raise ValueError(f"Run {run_id} has missing or duplicate batch artifacts")
        batch = batches.pop()
        expected_names = {
            f"experiment-{task['experiment']}-batch-{batch}-job-{task['global_id']}"
            for task in batch_tasks(tasks, batch)
        }
        if {artifact["name"] for artifact in artifacts} != expected_names:
            raise ValueError(f"Run {run_id} does not contain every result for batch {batch}")
        if any(artifact["expired"] for artifact in artifacts):
            raise ValueError(f"Run {run_id} has expired artifacts")
        batches_seen.add(batch)
        for kind in COUNTS:
            if any(artifact["name"].startswith(f"experiment-{kind}-") for artifact in artifacts):
                destination = Path(COLLECT_DIRS[kind]) / str(run_id)
                subprocess.run([
                    "gh", "run", "download", str(run_id), "--repo", repo,
                    "--pattern", f"experiment-{kind}-batch-{batch}-job-*",
                    "--dir", str(destination),
                ], check=True)
    if batches_seen != set(range(1, 8)):
        raise ValueError("Provide one run for each batch 1 through 7")


def summarize_trip():
    with open("time_dependent_trip_capacity_summary.csv", newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != 960 or any(row["process_status"] != "success" for row in rows):
        raise ValueError("Trip/capacity summary requires 960 successful solver results")
    groups = defaultdict(list)
    for row in rows:
        groups[(row["instance"], row["mode"], row["drone_capacity"])].append(row)
    output = []
    for key, group in sorted(groups.items()):
        if {int(row["run"]) for row in group} != set(range(1, 6)) or len(group) != 5:
            raise ValueError(f"Expected five distinct runs for {key}")
        values = [float(row["best_objective"]) for row in group
                  if row["best_objective"] not in ("", "None")]
        elapsed = [float(row["elapsed_seconds"]) for row in group]
        candidates = [row for row in group if row["best_objective"] not in ("", "None")]
        feasible = [row for row in candidates if row["solution_status"] == "FEASIBLE"]
        best = min(feasible or candidates,
                   key=lambda row: float(row["best_objective"]), default=None)
        output.append({
            "instance": key[0], "mode": key[1], "drone_capacity": key[2],
            "best_run": best["run"] if best else "",
            "best_objective": best["best_objective"] if best else "",
            "mean_obj_5_runs": statistics.fmean(values) if values else "",
            "mean_elapsed_seconds_5_runs": statistics.fmean(elapsed),
            "run_count": len(group),
            "feasible_runs": len(feasible),
        })
    with open("time_dependent_trip_capacity_best_summary.csv", "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(output[0]))
        writer.writeheader()
        writer.writerows(output)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--run-ids", required=True)
    args = parser.parse_args()
    run_ids = [int(value) for value in re.split(r"[\s,;]+", args.run_ids.strip())]
    if len(run_ids) != 7 or len(set(run_ids)) != 7:
        raise ValueError("Exactly seven distinct GitHub Actions run IDs are required")
    tasks = all_tasks()
    download_runs(args.repo, run_ids, tasks)
    manifests = [json.loads(path.read_text(encoding="utf-8"))
                 for directory in COLLECT_DIRS.values()
                 for path in Path(directory).rglob("task_manifest.json")]
    validate_manifests(manifests, tasks)
    os.environ["EXPECTED_TASK_COUNT"] = "320"
    for kind in COUNTS:
        runpy.run_path(str(SCRIPT_DIR / f"{kind}_summary.py"))
    summarize_trip()
    totals = Counter(manifest["task"]["experiment"] for manifest in manifests)
    with open("experiment_process_totals.csv", "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["experiment", "solver_processes"])
        writer.writerows(totals.items())
        writer.writerow(["total", sum(totals.values())])
    Path("experiment_run_sources.json").write_text(json.dumps({
        "repository": args.repo, "run_ids": run_ids,
        "commit": manifests[0]["commit"], "plan_hash": plan_hash(tasks),
        "solver_processes": len(manifests),
    }, indent=2), encoding="utf-8")
    print("Validated and summarized all 1600 solver processes")


if __name__ == "__main__":
    main()
