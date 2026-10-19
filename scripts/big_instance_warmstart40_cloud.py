"""Freeze and execute the 66 normal-only warm starts on GitHub Actions."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import zipfile

import big_instance_warmstart40 as warm

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / ".github/data/big-warmstart40.zip"
SESSION = ROOT / "build/big_warmstart40"


def export():
    plan = json.loads((warm.SESSION / "plan.json").read_text())
    plan["execution_backend"] = "github_actions"
    plan["solver_soft_limit_sec"] = 3540
    plan["process_hard_limit_sec"] = 3600
    plan["historical_time_limit_sec"] = 7200
    plan["default_workers"] = 1
    for task in plan["tasks"]:
        for row in (task["single"], task["previous_multi"]):
            assert row["experiment"] == "trip" and row["scenario"] not in ("heavy", "light")
        task["previous_multi"]["time_limit_s"] = "3540"
        for role, field in (("input", "input_file"), ("vmax", "truck_vmax_file"), ("theta", "truck_theta_file")):
            original = task["previous_multi"][field]
            assert original.startswith("instance_time_dependent/")
            assert "heavy" not in original and "light" not in original
            task["input_files"][role]["path"] = "historical/" + original
    plan.pop("protocol_id", None)
    plan["protocol_id"] = hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()
    BUNDLE.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(BUNDLE, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("plan.json", json.dumps(plan, indent=2))
        for name in ("bridge.cpp", "big_multitrip_original.cpp"):
            archive.write(warm.SESSION / name, name)
    print(f"Frozen {len(plan['tasks'])} normal-only tasks in {BUNDLE}")


def load():
    SESSION.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(BUNDLE) as archive:
        archive.extractall(SESSION)
    warm.SESSION = SESSION
    return json.loads((SESSION / "plan.json").read_text())


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("action", choices=("export", "matrix", "build", "task"))
    parser.add_argument("--slot")
    args = parser.parse_args()
    if args.action == "export":
        export()
        return
    plan = load()
    assert len(plan["tasks"]) == 66
    if args.action == "matrix":
        matrix = {"include": [{"slot": t["slot"], "instance": t["previous_multi"]["instance"]}
                              for t in plan["tasks"]]}
        with open(os.environ["GITHUB_OUTPUT"], "a") as stream:
            stream.write("matrix=" + json.dumps(matrix, separators=(",", ":")) + "\n")
        return
    if args.action == "build":
        warm.compile_solver()
        return
    task = next(t for t in plan["tasks"] if t["slot"] == args.slot)
    work = SESSION / "tasks" / task["slot"]
    work.mkdir(parents=True, exist_ok=True)
    for entry in task["input_files"].values():
        assert warm.digest(ROOT / entry["path"]) == entry["sha256"]
    build = json.loads((SESSION / "build.json").read_text())
    assert warm.digest(SESSION / "solver") == build["solver_sha256"]
    assert warm.digest(SESSION / "bridge.cpp") == build["bridge_sha256"]
    assert warm.digest(SESSION / "big_multitrip_original.cpp") == build["historical_source_sha256"]
    result = warm.run_one(task, hard_timeout=3600)
    result["execution_backend"] = "github_actions"
    result["github_run_id"] = os.environ.get("GITHUB_RUN_ID")
    result["github_run_attempt"] = os.environ.get("GITHUB_RUN_ATTEMPT")
    result["process_hard_limit_sec"] = 3600
    warm.dump(work / "result.json", result)
    print(json.dumps({"slot": task["slot"], "old": task["previous_multi"]["makespan_s"],
        "warm": task["single"]["makespan_s"], "new": result["audit"]["makespan_s"]}))


if __name__ == "__main__":
    main()
