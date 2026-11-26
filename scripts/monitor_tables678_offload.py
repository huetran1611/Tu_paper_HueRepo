"""Collect the 600 delegated jobs and publish the combined report when ready."""

import argparse
from collections import Counter
import json
from pathlib import Path
import subprocess
import sys
import time

import paper_tables_678 as design
import run_tables678_local as local


HERE = Path(__file__).resolve().parent


def github(*args):
    return subprocess.run(["gh", "run", *args], capture_output=True, text=True, check=True, timeout=900)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--plan", default="delegation_plan.json")
    parser.add_argument("--runs", default="github-runs.json")
    parser.add_argument("--status", default="github-status.json")
    parser.add_argument("--import-only", action="store_true")
    args = parser.parse_args()
    run = args.run.resolve()
    plan = design.read(run / args.runs)
    delegation = design.read(run / args.plan)
    manifest = local.load(run / "bundle")
    downloads = run / "github-artifacts"
    downloads.mkdir(exist_ok=True)
    status = {}
    retry_seconds = 600

    while True:
        for i, id_ in plan["runs"].items():
            id_ = str(id_)
            batch = downloads / id_
            batch.mkdir(exist_ok=True)
            receipt = batch / "imported.json"
            if receipt.exists():
                status[i] = design.read(receipt)
                continue
            try:
                remote = json.loads(github("view", id_, "--repo", plan["repository"], "--json",
                                           "status,conclusion,headSha").stdout)
                if remote["headSha"] != plan["commit"]:
                    raise ValueError("Unexpected GitHub workflow commit")
                status[i] = remote
                if remote["status"] != "completed":
                    continue
                github("download", id_, "--repo", plan["repository"], "--dir", str(batch),
                       "--pattern", f"irace-v4-n200-b{i}-s*")
                subprocess.run([sys.executable, "-B", str(HERE / "import_tables678_offload.py"),
                                str(run), str(batch), "--plan", args.plan, "--runs", args.runs], check=True)
                tasks = delegation["batches"][int(i) - 1]
                results = run / "results"
                remaining = [t["key"] for t in tasks
                             if not any((results / t["key"] / name).exists()
                                        for name in ("completed.json", "outcome.json"))]
                status[i] = {**remote, "run_id": id_, "imported": len(tasks) - len(remaining),
                             "missing_keys": remaining}
                if not remaining:
                    design.save(receipt, status[i])
            except (subprocess.SubprocessError, OSError, ValueError) as exc:
                status[i] = {"run_id": id_, "error": str(exc)}
                print(f"Batch {i}: {exc}", flush=True)

        design.save(run / args.status, {"batches": status, "updated_unix": time.time()})
        local_status = design.read(run / "status.json")
        if all((downloads / str(id_) / "imported.json").exists() for id_ in plan["runs"].values()):
            if args.import_only:
                return
            if local_status["state"] != "running":
                counts = Counter()
                for t in manifest["tasks"]:
                    directory = run / "results" / t["key"]
                    if (directory / "completed.json").exists():
                        record = design.read(directory / "completed.json")
                        counts["capped" if record["termination"] == "time_limit" else "completed"] += 1
                    elif (directory / "outcome.json").exists():
                        counts[design.read(directory / "outcome.json")["state"]] += 1
                if sum(counts.values()) == len(manifest["tasks"]):
                    local.report_campaign(run)
                    state = "finished_with_partial_results" if counts["partial"] or counts["failed"] else "finished"
                    design.save(run / "status.json", {**local_status, **counts, "state": state,
                                                      "delegated": 0, "updated_unix": time.time()})
                    print(f"Final report: {run / 'report' / 'REPORT.md'}", flush=True)
                    return
        time.sleep(retry_seconds)


if __name__ == "__main__":
    main()
