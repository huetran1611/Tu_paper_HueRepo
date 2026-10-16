"""Set03/fold1/seed42 pilot for the held-out Hanoi policy/buffer protocol."""
import argparse
import csv
from concurrent.futures import ThreadPoolExecutor
from datetime import date
import hashlib
import json
from pathlib import Path
import subprocess
import threading
import time

import numpy as np

from hanoi_pilot_selected import FLAGS, write_profile

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA = ROOT / "instance_hanoi/datasets/hanoi_traffic/Data_Ch\u1ed1t_9.2026"
DAYS = ("monday", "saturday", "sunday")


def dump(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save_csv(path, rows):
    if rows:
        with path.open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)


def audit_run(work):
    audit = json.loads((work / "event_audit.json").read_text())
    with (work / "customer_events.csv").open() as stream:
        events = list(csv.DictReader(stream))
    if sorted(int(row["customer"]) for row in events) != list(range(1, 101)):
        raise ValueError(f"Invalid customer coverage: {work}")
    violated = [row for row in events if float(row["post_pickup_sec"]) > float(row["limit_sec"]) + 1e-8]
    audit.update({"n_violated": len(violated), "freshness_failure": bool(violated),
                  "max_excess_min": max(float(row["excess_sec"]) / 60 for row in events),
                  "max_excess_pct": max(float(row["excess_pct"]) for row in events),
                  "failure_gt5": any(float(row["post_pickup_sec"]) > 1.05 * float(row["limit_sec"]) + 1e-8 for row in events),
                  "finish_hour": 8 + audit["makespan_sec"] / 3600})
    audit["feasible"] = all(audit[key] <= 1e-8 for key in ("deadline_violation", "capacity_violation", "energy_violation"))
    if audit["max_truck_trip_load_kg"] > 40 + 1e-8 or audit["max_drone_trip_load_kg"] > 2.27 + 1e-8:
        if audit["feasible"]:
            raise ValueError(f"Feasibility/load disagreement: {work}")
    return audit


def route_lines(path):
    return [line.split("|")[0].strip() for line in path.read_text().splitlines()
            if line.startswith(("Truck 1:", "Drone 1:"))]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--output", type=Path, default=ROOT / "results/hanoi_protocol_pilot_20261016/set03_fold1_seed42")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--workers", type=int, choices=range(1, 4), default=3)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if (output / "manifest.json").exists() and not args.resume:
        raise ValueError("Output already contains a run; use a new output directory")
    data = args.data_root.resolve() / "instances/set_03"
    generated = output / "inputs"
    generated.mkdir(exist_ok=args.resume)
    with np.load(data / "daily_path_speeds_full_precision.npz") as archive:
        daily = archive["speed_kph"].copy()
        hours = archive["hours"].copy()
        dates = archive["dates"].astype(str).tolist()
    heldout = [dates.index(date) for date in ("2026-09-07", "2026-09-05", "2026-09-06")]
    train = [k for k in range(12) if k not in heldout]
    window = (hours >= 8) & (hours < 17)
    if window.sum() != 9 or daily.shape != (12, 101, 101, len(hours)):
        raise ValueError("Unexpected profile layout")
    inverse = 1 / daily
    p0 = 1 / inverse[train][..., window].mean(axis=(0, 3))
    profiles = {"P0": np.repeat(p0[..., None], len(hours), axis=-1),
                "P1": 1 / inverse[train].mean(axis=0)}
    for group, day in enumerate(DAYS):
        same = [k for k, value in enumerate(dates) if k in train and
                date.fromisoformat(value).weekday() == (0, 5, 6)[group]]
        if len(same) != 3:
            raise ValueError("Invalid same-type training dates")
        profiles[f"P2_{day}"] = 1 / inverse[same].mean(axis=0)
        profiles[f"test_{day}"] = daily[heldout[group]]
    for name, speeds in profiles.items():
        write_profile(generated / f"{name}.txt", speeds, hours)
    original = (data / "instance.txt").read_text().splitlines()
    for buffer in (0, 10, 20):
        lines = original[:6]
        for line in original[6:]:
            fields = line.split()
            if len(fields) != 7:
                raise ValueError("Unexpected customer row")
            fields[-1] = f"{float(fields[-1]) * (1 - buffer / 100):.12g}"
            lines.append(" ".join(fields))
        (generated / f"instance_buffer{buffer}.txt").write_text("\n".join(lines) + "\n")
    manifest = {"instance": "set_03", "fold": 1, "seed": 42, "start_hour": 8,
                "p0_window": "[08:00,17:00)", "train_dates": [dates[k] for k in train],
                "test_dates": {day: dates[k] for day, k in zip(DAYS, heldout)},
                "flags": FLAGS, "expected_optimizations": 18, "expected_replays": 27,
                "data_root": str(args.data_root.resolve()), "binary_sha256": sha(args.binary),
                "source_sha256": sha(ROOT / "src_v2/tabubu_HanoiTraffic_v2.cpp"),
                "bridge_sha256": sha(ROOT / "scripts/hanoi_pilot_event_bridge.cpp"),
                "input_hashes": {name: sha(data / name) for name in
                                  ("instance.txt", "truck_distance_m.txt", "drone_distance_m.txt", "daily_path_speeds_full_precision.npz")},
                "profile_hashes": {name: sha(generated / f"{name}.txt") for name in profiles}}
    if (output / "manifest.json").exists():
        if json.loads((output / "manifest.json").read_text()) != manifest:
            raise ValueError("Resume configuration differs from saved manifest")
    else:
        dump(output / "manifest.json", manifest)
    runs, rows = [], []
    lock = threading.Lock()
    prior_runs = {}
    if args.resume and (output / "runs.csv").exists():
        with (output / "runs.csv").open() as stream:
            prior_runs = {row["key"]: row for row in csv.DictReader(stream)}

    def invoke(key, profile, buffer=0, solution=None):
        work = output / key
        work.mkdir(parents=True, exist_ok=args.resume)
        command = [str(args.binary.resolve()), str(generated / f"instance_buffer{buffer}.txt"), *FLAGS,
                   "--start-hour=8", f"--truck-distance-file={data / 'truck_distance_m.txt'}",
                   f"--drone-distance-file={data / 'drone_distance_m.txt'}",
                   f"--truck-vijl-file={generated / (profile + '.txt')}"]
        if solution is not None:
            command.append(f"--evaluate-solution={solution}")
        cached = args.resume and (work / "event_audit.json").exists()
        if cached:
            if json.loads((work / "command.json").read_text()) != command:
                raise ValueError("Cached command differs")
            elapsed = float(prior_runs.get(key, {}).get("elapsed_sec",
                        (work / "event_audit.json").stat().st_mtime - (work / "command.json").stat().st_mtime))
        else:
            if (work / "command.json").exists():
                raise ValueError(f"Incomplete run exists; preserve and inspect before retrying: {work}")
            dump(work / "command.json", command)
            print(f"START {key}", flush=True)
            start = time.monotonic()
            with (work / "solver.log").open("w") as log:
                result = subprocess.run(command, cwd=work, stdout=log, stderr=subprocess.STDOUT)
            if result.returncode not in ((0, 2) if solution is not None else (0,)):
                raise RuntimeError(f"Solver exit {result.returncode}: {work}")
            elapsed = time.monotonic() - start
        metrics = audit_run(work)
        if solution is not None and route_lines(solution) != route_lines(work / "output_solution_evaluated.txt"):
            raise ValueError("Replay changed routes")
        with lock:
            runs.append({"key": key, "replay": solution is not None, "elapsed_sec": elapsed, **metrics})
            save_csv(output / "runs.csv", runs)
        print(f"DONE {key}: feasible={metrics['feasible']}, finish={metrics['finish_hour']:.4f}h, violated={metrics['n_violated']}", flush=True)
        return work, metrics

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        pi_futures = {day: pool.submit(invoke, f"plans/PI_{day}", f"test_{day}") for day in DAYS}
        pi = {day: future.result()[1] for day, future in pi_futures.items()}
        plan_futures = {}
        for buffer in (0, 10, 20):
            for policy in ("P0", "P1", "P2"):
                for day in (DAYS if policy == "P2" else ("shared",)):
                    profile = f"P2_{day}" if policy == "P2" else policy
                    plan_futures[buffer, policy, day] = pool.submit(
                        invoke, f"plans/{policy}_buffer{buffer}_{day}", profile, buffer)
        plans = {key: future.result() for key, future in plan_futures.items()}
        replay_settings = [(buffer, policy, day) for buffer in (0, 10, 20)
                           for policy in ("P0", "P1", "P2") for day in DAYS]
        for buffer, policy, day in replay_settings:
            plan, planning = plans[buffer, policy, day if policy == "P2" else "shared"]
            base = {"instance": "set_03", "fold": 1, "seed": 42, "day_type": day,
                        "test_date": manifest["test_dates"][day], "policy": policy, "buffer_pct": buffer,
                        "plan_key": str(plan.relative_to(output)), "plan_returned": planning["feasible"],
                        "pi_feasible": pi[day]["feasible"]}
            detail = {key: None for key in ("makespan_sec", "finish_hour", "n_violated", "freshness_failure",
                          "max_excess_min", "max_excess_pct", "failure_gt5", "max_truck_trip_load_kg", "max_drone_trip_load_kg", "gap_pct")}
            if planning["feasible"]:
                _, replay = invoke(f"replays/{policy}_buffer{buffer}_{day}", f"test_{day}",
                                   solution=plan / "output_solution_best.txt")
                detail.update({key: replay[key] for key in detail if key != "gap_pct"})
                if pi[day]["feasible"]:
                    detail["gap_pct"] = 100 * (replay["makespan_sec"] - pi[day]["makespan_sec"]) / pi[day]["makespan_sec"]
            rows.append({**base, **detail})
            save_csv(output / "replay_cases.csv", rows)
    dump(output / "validation.json", {"optimizations": sum(not row["replay"] for row in runs),
         "replays": sum(row["replay"] for row in runs), "scheduled_replays": len(rows),
         "returned_plans": sum(row["plan_returned"] for row in rows),
         "pi_pairs": sum(row["gap_pct"] is not None for row in rows),
         "all_route_and_event_audits_passed": True,
         "note": "Small pilot only; not an estimate across 20 instances/four folds/ten seeds."})
    print(f"COMPLETE {output}", flush=True)


if __name__ == "__main__":
    main()
