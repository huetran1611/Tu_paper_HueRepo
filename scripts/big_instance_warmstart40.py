"""Resumeable local multi-trip searches initialized from better single-trip plans."""
import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from decimal import Decimal
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

from audit_seven_truck_loads import demands, trips
from package_big_instance_results import DESTINATION, ROOT, SOURCE, digest, read_csv
from summarize_seven_experiment_runs import parse_solution

SESSION = DESTINATION / "warmstart_capacity40_20261019"
SOURCE_SHA = "a1318b157540810c887f6982f188065cce86ac6820b743afc66554c6c86d8a00"
COMMIT = "902415a453ae5174403151323de4398f7d456c6d"


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, indent=2) + "\n")
    temp.replace(path)


def select_pairs(rows):
    pairs = defaultdict(dict)
    for row in rows:
        if row["experiment"] != "trip":
            continue
        key = row["instance"], row["drone_capacity_kg"], row["result_slot_run"]
        if row["mode"] in pairs[key]:
            raise ValueError("Duplicate matching mode")
        pairs[key][row["mode"]] = row
    if len(pairs) != 480 or any(set(v) != {"one_trip", "multi_trip"} for v in pairs.values()):
        raise ValueError("Incomplete 480 paired cases")
    return [(v["one_trip"], v["multi_trip"]) for _, v in sorted(pairs.items())
            if float(v["one_trip"]["makespan_s"]) < float(v["multi_trip"]["makespan_s"])-1e-6]


def prepare():
    if SESSION.exists():
        raise FileExistsError(f"Refusing to overwrite {SESSION}")
    rows = read_csv(DESTINATION / "all_solutions_and_parameters.csv")
    selected = select_pairs(rows)
    assert len(selected) == 66
    SESSION.mkdir()
    snapshot = SESSION / "baseline_snapshot"
    snapshot.mkdir()
    for name in ("all_solutions_and_parameters.csv", "manifest.json", "README.md", "mota.md"):
        shutil.copy2(DESTINATION / name, snapshot / name)
    shutil.copytree(DESTINATION / "report", snapshot / "report")
    source = SOURCE / "sources" / COMMIT / "tabubu_TimeDependent.cpp"
    assert digest(source) == SOURCE_SHA
    shutil.copy2(source, SESSION / "big_multitrip_original.cpp")
    shutil.copy2(ROOT / "scripts/big_instance_warmstart_bridge.cpp", SESSION / "bridge.cpp")
    tree = {r["path"]: r["sha"] for r in json.loads((SOURCE / "source_tree.json").read_text())["tree"]
            if r["type"] == "blob"}
    checked = {}
    tasks = []
    for single, multiple in selected:
        assert single["eligible"] == multiple["eligible"] == "True"
        for field in ("instance", "customers", "drone_capacity_kg", "trucks", "drones", "input_file", "truck_vmax_file", "truck_theta_file"):
            assert single[field] == multiple[field], field
        files = {}
        for role, field in (("input", "input_file"), ("vmax", "truck_vmax_file"), ("theta", "truck_theta_file")):
            original = multiple[field]
            current = ROOT / "instance_time_dependent/big_instance/normal" / Path(original).name
            if original not in checked:
                data = current.read_bytes()
                git_blob = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
                assert git_blob == tree[original], str(current)
                checked[original] = {"path": str(current.relative_to(ROOT)), "git_blob_sha": git_blob,
                                     "sha256": hashlib.sha256(data).hexdigest()}
            files[role] = checked[original]
        tasks.append({"slot": multiple["result_slot_global_id"], "single": single, "previous_multi": multiple,
                      "input_files": files})
    plan = {"created_utc": datetime.now(timezone.utc).isoformat(), "tasks": tasks,
        "baseline_csv_sha256": digest(DESTINATION / "all_solutions_and_parameters.csv"),
        "historical_source_sha256": SOURCE_SHA, "historical_commit": COMMIT,
        "bridge_sha256": digest(SESSION / "bridge.cpp"), "truck_capacity_kg": 40,
        "selection": "All 66 matched cases with single-trip cost < multi-trip cost",
        "data_files": checked, "default_workers": 2, "same_historical_search_parameters": True,
        "search_rng": "Historical time-based RNGs retained; no new distinct-seed claim"}
    plan["protocol_id"] = hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()
    dump(SESSION / "plan.json", plan)
    dump(SESSION / "status.json", {"status": "prepared", "planned": len(tasks), "completed": 0})
    print("Prepared", len(tasks), "tasks; historical input/velocity hashes verified", flush=True)


def compile_solver():
    command = ["clang++", "-O3", "-std=c++20", str(SESSION / "bridge.cpp"),
               "-I", str(SESSION), "-o", str(SESSION / "solver")]
    subprocess.run(command, check=True)
    dump(SESSION / "build.json", {"command": command, "solver_sha256": digest(SESSION / "solver"),
         "bridge_sha256": digest(SESSION / "bridge.cpp"), "historical_source_sha256": digest(SESSION / "big_multitrip_original.cpp")})


def validate_result(task, work, pilot=False):
    audit = json.loads((work / "audit.json").read_text())
    text = (work / "output_solution_best.txt").read_text()
    old = task["previous_multi"]
    parsed = parse_solution(text, int(old["customers"]))
    assert parsed["reported_feasibility"] == "FEASIBLE" and parsed["route_audit_ok"]
    assert audit["truck_capacity_kg"] == 40 and audit["drone_capacity_kg"] == float(old["drone_capacity_kg"])
    assert abs(audit["initial_makespan_s"]-float(task["single"]["makespan_s"])) < 1e-5
    assert audit["makespan_s"] <= audit["initial_makespan_s"]+1e-7
    assert abs(audit["makespan_s"]-parsed["makespan_s"]) < 1e-5
    if not pilot:
        assert not audit["evaluation_only"] and not audit["smoke_test"] and audit["actual_iterations"] > 0
        assert audit["planned_iterations"] == int(old["planned_iterations"])
    assert all(audit[field] <= 1e-8 for field in ("deadline_violation", "capacity_violation", "energy_violation"))
    weights = demands(ROOT / task["input_files"]["input"]["path"], int(old["customers"]))
    maxima = {}
    for kind, field, cap in (("truck", "truck_routes", Decimal(40)),
                            ("drone", "drone_routes", Decimal(old["drone_capacity_kg"]))):
        loads = [sum((weights[node] for node in customers), Decimal(0))
                 for route in json.loads(parsed[field]) for customers in trips(route)]
        maximum = max(loads, default=Decimal(0))
        assert maximum <= cap, (task["slot"], kind, maximum)
        assert abs(float(maximum)-audit[f"max_{kind}_trip_load_kg"]) < 1e-7
        maxima[f"max_{kind}_trip_load_kg"] = str(maximum)
    events = read_csv(work / "customer_events.csv")
    assert sorted(int(r["customer"]) for r in events) == list(range(1, int(old["customers"])+1))
    assert all(float(r["post_pickup_sec"]) <= float(r["limit_sec"])+1e-8 for r in events)
    return {"slot": task["slot"], "status": "validated", "audit": audit, "metrics": parsed,
            "exact_loads": maxima, "output_solution_sha256": digest(work / "output_solution_best.txt")}


def run_one(task, work=None, extra=(), hard_timeout=None):
    work = work or SESSION / "tasks" / task["slot"]
    pilot = bool(extra)
    if (work / "result.json").exists() and not pilot:
        return validate_result(task, work)
    work.mkdir(parents=True, exist_ok=True)
    for entry in task["input_files"].values():
        assert digest(ROOT / entry["path"]) == entry["sha256"], entry["path"]
    warm = work / "warmstart_single.txt"
    warm.write_text(task["single"]["solution_text"])
    row = task["previous_multi"]
    command = [str(SESSION / "solver"), str(ROOT / task["input_files"]["input"]["path"]),
        f"--warmstart={warm}", f"--drone-capacity={row['drone_capacity_kg']}",
        f"--truck-vmax-file={ROOT / task['input_files']['vmax']['path']}",
        f"--truck-theta-file={ROOT / task['input_files']['theta']['path']}",
        f"--segments={row['planned_segments']}", f"--iters={row['iterations_per_segment']}",
        f"--no-improve={row['no_improve_threshold']}", f"--time-limit={row['time_limit_s']}", *extra]
    dump(work / "command.json", command)
    start = time.monotonic()
    with (work / "solver.log").open("w") as stream:
        subprocess.run(command, cwd=work, stdout=stream, stderr=subprocess.STDOUT,
                       check=True, timeout=hard_timeout if hard_timeout is not None else
                       (180 if pilot else float(row["time_limit_s"])+300))
    result = validate_result(task, work, pilot)
    result.update(process_elapsed_s=time.monotonic()-start,
                  protocol_id=json.loads((SESSION / "plan.json").read_text())["protocol_id"])
    dump(work / "result.json", result)
    history = work / "output_mode_0.txt"
    if history.exists():
        with history.open("rb") as source, gzip.open(work / "output_mode_0.txt.gz", "wb") as target:
            shutil.copyfileobj(source, target)
        history.unlink()
    return result


def pilot():
    plan = json.loads((SESSION / "plan.json").read_text())
    checked = []
    for size, cap in (("200", "5.0"), ("200", "10.0"), ("500", "5.0"), ("500", "10.0")):
        task = next(t for t in plan["tasks"] if (t["single"]["customers"], t["single"]["drone_capacity_kg"]) == (size, cap))
        result = run_one(task, SESSION / "pilot" / f"evaluate_{size}_{cap}", ("--evaluate-only",))
        checked.append({"customers": size, "drone_capacity_kg": cap, "warm_cost": result["audit"]["makespan_s"]})
    task = plan["tasks"][0]
    smoke = run_one(task, SESSION / "pilot/smoke", ("--smoke-iterations=3",))
    report = {"evaluations": checked, "smoke_actual_iterations": smoke["audit"]["actual_iterations"],
              "smoke_initial_cost": smoke["audit"]["initial_makespan_s"],
              "smoke_final_cost": smoke["audit"]["makespan_s"], "pilot_not_published": True}
    dump(SESSION / "pilot_verification.json", report)
    print(json.dumps(report, indent=2), flush=True)


def run(workers):
    os.nice(5)
    plan = json.loads((SESSION / "plan.json").read_text())
    assert (SESSION / "pilot_verification.json").exists()
    assert digest(SESSION / "solver") == json.loads((SESSION / "build.json").read_text())["solver_sha256"]
    state = {"status": "running", "planned": len(plan["tasks"]), "completed": 0, "workers": workers,
             "started_utc": datetime.now(timezone.utc).isoformat(), "pid": os.getpid(), "errors": []}
    dump(SESSION / "status.json", state)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(run_one, task): task for task in plan["tasks"]}
        for future in as_completed(futures):
            task = futures[future]
            try:
                result = future.result()
                print("DONE", task["slot"], "old", task["previous_multi"]["makespan_s"],
                      "warm", task["single"]["makespan_s"], "new", result["audit"]["makespan_s"], flush=True)
            except Exception as error:
                state["errors"].append({"slot": task["slot"], "error": str(error)})
                print("ERROR", task["slot"], str(error), flush=True)
            state["completed"] += 1
            dump(SESSION / "status.json", state)
    if state["errors"]:
        state["status"] = "needs_attention_results_not_replaced"
        dump(SESSION / "status.json", state)
        raise RuntimeError("Some tasks failed; final result files remain unchanged")
    try:
        from big_instance_warmstart40_collect import finalize
        finalize(plan, state)
    except Exception as error:
        state.update(status="finalization_needs_attention", finalization_error=str(error))
        dump(SESSION / "status.json", state)
        raise


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("action", choices=("prepare", "compile", "pilot", "run"))
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args()
    if args.action == "prepare": prepare()
    elif args.action == "compile": compile_solver()
    elif args.action == "pilot": pilot()
    else: run(args.workers)


if __name__ == "__main__":
    main()
