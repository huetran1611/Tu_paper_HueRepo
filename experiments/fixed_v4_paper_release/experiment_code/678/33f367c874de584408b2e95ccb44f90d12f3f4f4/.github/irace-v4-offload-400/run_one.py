"""Run a single delegated n=200 task with the original Windows solver."""

import json
import os
from collections import Counter
from pathlib import Path
import platform
import subprocess
import sys
import time
from zipfile import ZipFile
from hashlib import sha256

import paper_tables_678 as design
import run_tables678_local as local


HOME = Path(__file__).resolve().parent
RUN = HOME / "job"


def main():
    batch, slot = map(int, sys.argv[1:])
    plan = design.read(HOME / "jobs.json")
    manifest = design.load(HOME / "bundle")
    batches = plan["batches"]
    tasks = [task for group in batches for task in group]
    if plan["protocol_id"] != manifest["protocol_id"]:
        raise ValueError("Plan does not match the frozen manifest")
    sizes = tuple(map(len, batches))
    budgets = {(200, 200, 200): {27000: 320, 54000: 280}, (200, 200): {27000: 400}}
    if len({task["key"] for task in tasks}) != len(tasks) or Counter(
        task["iterations"] for task in tasks
    ) != budgets.get(sizes):
        raise ValueError("Delegated task counts or keys differ")
    if not (1 <= batch <= len(batches) and 0 <= slot < len(batches[batch - 1])):
        raise ValueError("Unknown batch/slot")
    task = batches[batch - 1][slot]
    if task["n"] != 200 or task not in manifest["tasks"]:
        raise ValueError("The task is not part of the frozen n=200 campaign")
    expected_cap = 14400 if task["iterations"] == 54000 else 7200
    if local.process_cap(manifest, task) != expected_cap:
        raise ValueError("Unexpected wall-clock cap")

    inputs = RUN / "inputs"
    inputs.mkdir(parents=True, exist_ok=True)
    instance = next(i for i in manifest["instances"] if i["instance"] == task["instance"])
    with ZipFile(HOME / "bundle" / "inputs.zip") as archive:
        for entry in instance["files"]:
            raw = archive.read(entry["path"])
            if sha256(raw).hexdigest() != entry["sha256"]:
                raise ValueError("Input checksum mismatch")
            (inputs / Path(entry["path"]).name).write_bytes(raw)

    for name in ("libgcc_s_seh-1.dll", "libstdc++-6.dll", "libwinpthread-1.dll"):
        path = HOME / "bundle" / name
        if design.sha(path) != plan["runtime_sha256"][name]:
            raise ValueError(f"Runtime dependency changed: {name}")
    (RUN / "bundle").mkdir(exist_ok=True)
    for name in (manifest["binary"], *plan["runtime_sha256"]):
        src = HOME / "bundle" / name
        dst = RUN / "bundle" / name
        if not dst.exists():
            dst.hardlink_to(src)
    manifest["compiler_directory"] = str(RUN / "bundle")
    active = local.launch(RUN, manifest, task)
    try:
        active["process"].wait(timeout=max(0, active["started"] + expected_cap - time.monotonic()))
        timed_out = False
    except subprocess.TimeoutExpired:
        active["process"].kill()
        active["process"].wait()
        timed_out = True
    state = local.finish(RUN, manifest, active, timed_out)
    directory = RUN / "results" / task["key"]
    result = directory / ("completed.json" if state in {"completed", "capped"} else "outcome.json")
    row = design.read(result)
    row["execution_host"] = "github-actions-windows"
    row["runner_cpu"] = os.environ.get("PROCESSOR_IDENTIFIER") or platform.processor()
    row["github_run_id"] = os.environ["GITHUB_RUN_ID"]
    row["github_run_attempt"] = os.environ["GITHUB_RUN_ATTEMPT"]
    design.save(result, row)
    print(json.dumps({"key": task["key"], "state": state, "cap_seconds": expected_cap}))
    if state == "failed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
