"""Full four-fold Hanoi experiment: buffer 10%, shared unbuffered PI."""
import argparse
from collections import defaultdict
import csv
import gzip
import json
from pathlib import Path
import shutil
import subprocess

import numpy as np

from hanoi_p2_cloud_pilot import Profiles, sha256
from hanoi_pilot_selected import FLAGS, write_profile
from hanoi_protocol_pilot import audit_run, route_lines

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / ".github/data/hanoi_protocol_buffer10"
DAYS = ("monday", "saturday", "sunday")
SEEDS = tuple(range(42, 52))
SHARDS = 240


def dump(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


def csv_file(path, rows):
    if rows:
        with path.open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)


def tasks(instances):
    result = []
    for instance in instances:
        for fold in range(1, 5):
            for seed in SEEDS:
                settings = [("P0", "shared"), ("P1", "shared")]
                settings += [(policy, day) for policy in ("P2", "PI") for day in DAYS]
                for policy, day in settings:
                    result.append({"key": f"{policy}-{instance}-fold{fold}-seed{seed}-{day}",
                                   "instance": instance, "fold": fold, "seed": seed,
                                   "policy": policy, "day_type": day})
    return result


def shard_tasks(all_tasks, shard):
    if not 1 <= shard <= SHARDS:
        raise ValueError("Shard must be 1..240")
    return all_tasks[shard - 1::SHARDS]


def profiles_for(daily, hours, fold, policy, day):
    heldout = [fold - 1 + 4 * group for group in range(3)]
    train = [k for k in range(12) if k not in heldout]
    same = []
    if policy == "P0":
        window = (hours >= 8) & (hours < 17)
        if window.sum() != 9:
            raise ValueError("P0 needs nine hourly intervals [8,17)")
        speed = 1 / (1 / daily[train][..., window]).mean(axis=(0, 3))
        planning = np.repeat(speed[..., None], len(hours), axis=-1)
    elif policy == "P1":
        planning = 1 / (1 / daily[train]).mean(axis=0)
    elif policy == "P2":
        group = DAYS.index(day)
        same = [k for k in range(4 * group, 4 * group + 4) if k not in heldout]
        planning = 1 / (1 / daily[same]).mean(axis=0)
    elif policy == "PI":
        planning = daily[heldout[DAYS.index(day)]]
        train = []
    else:
        raise ValueError("Unknown policy")
    return planning, heldout, same if policy == "P2" else train


def buffered_instance(source, target, buffer):
    lines = source.read_text().splitlines()
    result = lines[:6]
    for line in lines[6:]:
        fields = line.split()
        if len(fields) != 7 or float(fields[-1]) != 3600:
            raise ValueError("Expected 100 customer records with original 3600-second freshness")
        fields[-1] = f"{float(fields[-1]) * (1 - buffer):.12g}"
        result.append(" ".join(fields))
    if len(result) != 106:
        raise ValueError("Expected 100 customers")
    target.write_text("\n".join(result) + "\n")


def run_solver(binary, instance, data, profile, work, seed, solution=None):
    work.mkdir(parents=True)
    flags = [f"--seed={seed}" if flag.startswith("--seed=") else flag for flag in FLAGS]
    command = [str(binary), str(instance), *flags, "--start-hour=8",
               f"--truck-distance-file={data / 'truck_distance_m.txt'}",
               f"--drone-distance-file={data / 'drone_distance_m.txt'}", f"--truck-vijl-file={profile}"]
    if solution is not None:
        command.append(f"--evaluate-solution={solution}")
    dump(work / "command.json", command)
    with (work / "solver.log").open("w") as log:
        process = subprocess.run(command, cwd=work, stdout=log, stderr=subprocess.STDOUT)
    if process.returncode not in ((0, 2) if solution else (0,)):
        raise RuntimeError(f"Solver exit {process.returncode}: {work}")
    metrics = audit_run(work)
    if solution is not None:
        if route_lines(solution) != route_lines(work / "output_solution_evaluated.txt"):
            raise ValueError("Replay changed the fixed plan")
        if metrics["capacity_violation"] > 1e-8 or metrics["energy_violation"] > 1e-8:
            raise ValueError("Traffic-only replay unexpectedly changed capacity/energy feasibility")
    else:
        history = work / "output_mode_0.txt"
        with history.open() as stream:
            iterations = list(csv.DictReader(stream))
        if len(iterations) != 9000 or int(iterations[-1]["iter"]) != 9000:
            raise ValueError("Optimization did not complete 9000 iterations")
        metrics["iterations"] = 9000
        with history.open("rb") as source, gzip.open(str(history) + ".gz", "wb") as target:
            shutil.copyfileobj(source, target)
        history.unlink()
    return metrics


def run_shard(args):
    store = Profiles(args.bundle)
    if len(store.instances) != 20:
        raise ValueError("Expected 20 instances")
    selected = shard_tasks(tasks(store.instances), args.shard)
    args.output.mkdir(parents=True, exist_ok=True)
    args.profiles.mkdir(parents=True, exist_ok=True)
    dates = store.data["dates"].astype(str).tolist()
    hours = store.data["hours"]
    protocol = {"start_hour": 8, "buffer": 0.1, "PI_buffer": 0,
                "p0_window": [8, 17], "seeds": list(SEEDS), "flags": FLAGS,
                "profiles_sha256": store.manifest["profiles_sha256"],
                "source_sha256": sha256(ROOT / "src_v2/tabubu_HanoiTraffic_v2.cpp"),
                "bridge_sha256": sha256(ROOT / "scripts/hanoi_pilot_event_bridge.cpp"),
                "runner_sha256": sha256(Path(__file__)), "binary_sha256": sha256(args.binary)}
    dump(args.output / "shard_manifest.json", {"shard": args.shard, "tasks": selected, "protocol": protocol})
    failures = []
    for task in selected:
        print(f"START {task['key']}", flush=True)
        work = args.output / task["key"]
        generated = args.profiles / task["key"]
        generated.mkdir(parents=True)
        try:
            data = args.bundle / "instances" / task["instance"]
            daily = store.daily(task["instance"])
            speed, heldout, train = profiles_for(daily, hours, task["fold"], task["policy"], task["day_type"])
            planning_profile = generated / "planning.txt"
            write_profile(planning_profile, speed, hours)
            work.mkdir()
            planning_input = work / "planning_instance.txt"
            buffer = 0 if task["policy"] == "PI" else 0.1
            buffered_instance(data / "instance.txt", planning_input, buffer)
            planning = run_solver(args.binary, planning_input, data, planning_profile, work / "planning", task["seed"])
            result = {"task": task, "protocol": protocol, "buffer": buffer,
                      "training_dates": [dates[k] for k in train],
                      "planning_profile_sha256": sha256(planning_profile), "planning": planning, "cases": []}
            if task["policy"] == "PI":
                result["test_date"] = dates[heldout[DAYS.index(task["day_type"])]]
            else:
                for day in (DAYS if task["policy"] in ("P0", "P1") else (task["day_type"],)):
                    test = heldout[DAYS.index(day)]
                    case = {"day_type": day, "test_date": dates[test], "replay": None}
                    if planning["feasible"]:
                        test_profile = generated / f"test_{day}.txt"
                        write_profile(test_profile, daily[test], hours)
                        case["profile_sha256"] = sha256(test_profile)
                        case["replay"] = run_solver(args.binary, data / "instance.txt", data, test_profile,
                              work / f"replay_{day}", task["seed"], work / "planning/output_solution_best.txt")
                    result["cases"].append(case)
            dump(work / "task_result.json", result)
            print(f"DONE {task['key']}: plan_feasible={planning['feasible']}", flush=True)
        except Exception as error:
            failures.append({"task": task, "error": str(error)})
            dump(work / "error.json", failures[-1])
            print(f"ERROR {task['key']}: {error}", flush=True)
        finally:
            shutil.rmtree(generated)
    dump(args.output / "shard_status.json", {"expected": len(selected), "failures": failures})
    if failures:
        raise SystemExit("Technical failures occurred; inspect artifacts, do not count these as NP")


def summarize(cases):
    rows = []
    for day in DAYS:
        for policy in ("P0", "P1", "P2"):
            group = [r for r in cases if r["day_type"] == day and r["policy"] == policy]
            returned = [r for r in group if r["plan_returned"]]
            failed = [r for r in returned if r["freshness_failure"]]
            paired = [r for r in returned if r["gap_pct"] is not None]
            date_rf = []
            for date in sorted({r["test_date"] for r in group}):
                subset = [r for r in returned if r["test_date"] == date]
                if subset:
                    date_rf.append(100 * sum(r["freshness_failure"] for r in subset) / len(subset))
            mean = lambda values: float(np.mean(values)) if values else None
            rows.append({"day_type": day, "policy": policy, "buffer": 0.1, "N": 800,
                         "observed_cases": len(group), "returned_NA": len(returned), "paired_NB": len(paired),
                         "NP_pct": 100 * (800 - len(returned)) / 800 if len(group) == 800 else None,
                         "RF_pct": 100 * len(failed) / len(returned) if returned else None,
                         "RF_date_min_pct": min(date_rf) if date_rf else None,
                         "RF_date_max_pct": max(date_rf) if date_rf else None,
                         "RF_defined_dates": len(date_rf), "gap_pct": mean([r["gap_pct"] for r in paired]),
                         "violating_replays": len(failed),
                         "mean_max_excess_pct_violating": mean([r["max_excess_pct"] for r in failed]),
                         "mean_max_excess_min_violating": mean([r["max_excess_min"] for r in failed]),
                         "mean_violated_customer_pct": mean([r["n_violated"] for r in returned]),
                         "RF_gt5_pct": 100 * sum(r["failure_gt5"] for r in returned) / len(returned) if returned else None})
    return rows


def valid_record(record, manifest):
    task, protocol = record["task"], record["protocol"]
    heldout = [task["fold"] - 1 + 4 * group for group in range(3)]
    dates = manifest["dates"]
    day = task["day_type"]
    expected_days = [] if task["policy"] == "PI" else list(DAYS if day == "shared" else (day,))
    if [case["day_type"] for case in record["cases"]] != expected_days:
        return False
    for case in record["cases"]:
        if (case["test_date"] != dates[heldout[DAYS.index(case["day_type"])]]
                or (case["replay"] is not None) != record["planning"]["feasible"]):
            return False
    if task["policy"] == "PI" and record.get("test_date") != dates[heldout[DAYS.index(day)]]:
        return False
    return (protocol["buffer"] == 0.1 and protocol["PI_buffer"] == 0 and protocol["start_hour"] == 8
            and protocol["p0_window"] == [8, 17] and protocol["seeds"] == list(SEEDS)
            and protocol["flags"] == FLAGS and protocol["profiles_sha256"] == manifest["profiles_sha256"]
            and record["buffer"] == (0 if task["policy"] == "PI" else 0.1)
            and record["planning"].get("iterations") == 9000
            and protocol["source_sha256"] == sha256(ROOT / "src_v2/tabubu_HanoiTraffic_v2.cpp")
            and protocol["bridge_sha256"] == sha256(ROOT / "scripts/hanoi_pilot_event_bridge.cpp")
            and protocol["runner_sha256"] == sha256(Path(__file__)))


def aggregate(args):
    args.output.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((args.bundle / "manifest.json").read_text())
    expected = {t["key"]: t for t in tasks(manifest["instances"])}
    records, duplicates, invalid = {}, [], []
    signatures = set()
    for path in sorted(args.input.glob("**/task_result.json")):
        record = json.loads(path.read_text())
        task = record["task"]
        key = task["key"]
        if key in records:
            duplicates.append(key)
        if key not in expected or expected[key] != task:
            invalid.append(str(path))
            continue
        protocol = record["protocol"]
        if not valid_record(record, manifest):
            invalid.append(str(path))
            continue
        signatures.add(tuple(protocol[k] for k in ("source_sha256", "bridge_sha256", "runner_sha256", "binary_sha256")))
        records[key] = record
    references = {}
    for record in records.values():
        task = record["task"]
        if task["policy"] == "PI" and record["planning"]["feasible"]:
            references[task["instance"], record["test_date"], task["seed"]] = record["planning"]["makespan_sec"]
    cases, plans = [], []
    for key, record in sorted(records.items()):
        task = record["task"]
        plans.append({**task, "buffer": record["buffer"], **record["planning"]})
        for case in record["cases"]:
            row = {"instance": task["instance"], "fold": task["fold"], "seed": task["seed"],
                   "day_type": case["day_type"], "test_date": case["test_date"], "policy": task["policy"],
                   "buffer": 0.1, "plan_key": key, "plan_returned": record["planning"]["feasible"]}
            replay = case["replay"]
            fields = ("makespan_sec", "finish_hour", "freshness_failure", "n_violated", "max_excess_pct",
                      "max_excess_min", "failure_gt5", "max_truck_trip_load_kg", "max_drone_trip_load_kg")
            row.update({name: replay[name] if replay else None for name in fields})
            pi = references.get((task["instance"], case["test_date"], task["seed"]))
            row["PI_makespan_sec"] = pi
            row["gap_pct"] = 100 * (replay["makespan_sec"] - pi) / pi if replay and pi is not None else None
            cases.append(row)
    missing = sorted(set(expected) - records.keys())
    summaries = summarize(cases)
    case_keys = [(r["instance"], r["test_date"], r["seed"], r["policy"]) for r in cases]
    complete = (not missing and not duplicates and not invalid and len(signatures) == 1
                and len(cases) == 7200 and len(set(case_keys)) == 7200
                and all(row["observed_cases"] == 800 for row in summaries))
    report = {"complete": complete, "buffer": 0.1, "start_hour": 8, "p0_window": [8, 17],
              "expected_optimizations": 6400, "completed_optimizations": len(records),
              "scheduled_replays": 7200, "observed_policy_cases": len(cases),
              "actual_replays": sum(r["plan_returned"] for r in cases),
              "feasible_PI_references": len(references), "missing_tasks": missing,
              "duplicate_tasks": duplicates, "invalid_records": invalid,
              "different_build_signatures": len(signatures),
              "note": "Technical failures are missing data, not planning non-success. Buffer 0.1 only; no S1 or buffer-zero policy experiment."}
    csv_file(args.output / "all_planning_runs.csv", plans)
    csv_file(args.output / "all_replay_cases.csv", cases)
    csv_file(args.output / "heldout_buffer10_summary.csv", summaries)
    dump(args.output / "coverage.json", report)
    print(json.dumps(report, indent=2))
    if not complete:
        raise SystemExit("Incomplete or inconsistent experiment; see coverage.json")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, default=BUNDLE)
    commands = parser.add_subparsers(dest="command", required=True)
    matrix = commands.add_parser("matrix")
    run = commands.add_parser("run-shard")
    run.add_argument("--shard", type=int, required=True)
    run.add_argument("--binary", type=Path, required=True)
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--profiles", type=Path, required=True)
    collect = commands.add_parser("aggregate")
    collect.add_argument("--input", type=Path, required=True)
    collect.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    for key in ("bundle", "binary", "output", "profiles", "input"):
        if hasattr(args, key):
            setattr(args, key, getattr(args, key).resolve())
    if args.command == "matrix":
        print(json.dumps({"shard": list(range(1, SHARDS + 1))}))
    elif args.command == "run-shard":
        run_shard(args)
    else:
        aggregate(args)


if __name__ == "__main__":
    main()
