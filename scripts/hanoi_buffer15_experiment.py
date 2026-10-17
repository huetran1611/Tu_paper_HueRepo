"""Run buffer 15 PERCENT (epsilon=0.15), not buffer 50 percent."""
import argparse
from collections import Counter
from pathlib import Path
import tempfile
import json

import hanoi_full_experiment as full

BUFFER_PERCENT = 15
EPSILON = BUFFER_PERCENT / 100
PLANNING_FRESHNESS_SEC = 3060
REPLAY_FRESHNESS_SEC = 3600
BATCH_SIZE = 250


def tasks(parent):
    result = [{**task, "buffer_pct": BUFFER_PERCENT, "key": task["key"][:-2] + "15"}
              for task in parent["tasks"] if task["policy"] != "PI" and task["buffer_pct"] == 0]
    if len(result) != 4000 or len({t["key"] for t in result}) != 4000:
        raise ValueError("Expected exactly 4000 distinct new optimizations")
    if Counter(t["policy"] for t in result) != {"P0": 800, "P1": 800, "P2": 2400}:
        raise ValueError("Unexpected policy coverage")
    return result


def batch_tasks(parent, batch):
    if batch not in range(1, 17):
        raise ValueError("Buffer15pct batch must be 1..16")
    return tasks(parent)[(batch - 1) * BATCH_SIZE:batch * BATCH_SIZE]


def context(capsule):
    parent = full.load_capsule(capsule)
    if (parent["buffers_pct"] != [0, 10] or parent["PI_buffer_pct"] != 0
            or parent["start_hour"] != 8 or parent["p0_window"] != [8, 17]
            or len(parent["tasks"]) != 10400):
        raise ValueError("Expected original frozen baseline/buffer10 experiment")
    tasks(parent)
    extension_id = full.digest({"parent_protocol_id": parent["protocol_id"],
        "buffer_pct": BUFFER_PERCENT, "epsilon": EPSILON,
        "planning_freshness_sec": PLANNING_FRESHNESS_SEC,
        "replay_freshness_sec": REPLAY_FRESHNESS_SEC,
        "runner_sha256": full.sha256(Path(__file__))})
    parent["_capsule"] = capsule
    return parent, extension_id


def run_task(args, parent, extension_id):
    task = next((t for t in batch_tasks(parent, args.batch) if t["key"] == args.task_key), None)
    if task is None:
        raise ValueError("Task is not assigned to this batch")
    args.output.mkdir(parents=True)
    base = full.base
    base.dump(args.output / "task.json", task)
    store = full.Profiles(args.capsule / "bundle")
    data = store.bundle / "instances" / task["instance"]
    daily = store.daily(task["instance"])
    hours, dates = store.data["hours"], store.data["dates"].astype(str).tolist()
    with tempfile.TemporaryDirectory(prefix="hanoi-buffer15-") as temporary:
        profile = Path(temporary) / "planning.txt"
        speed, heldout, train = base.profiles_for(daily, hours, task["fold"], task["policy"], task["day_type"])
        full.write_profile(profile, speed, hours)
        planning_input = args.output / "planning_instance.txt"
        base.buffered_instance(data / "instance.txt", planning_input, EPSILON)
        limits = {float(line.split()[-1]) for line in planning_input.read_text().splitlines()[6:]}
        if limits != {float(PLANNING_FRESHNESS_SEC)}:
            raise ValueError("Planning freshness must be 3060 seconds, not 1800 seconds")
        planning = base.run_solver(args.capsule / "solver", planning_input, data, profile,
                                   args.output / "planning", task["seed"])
        record = {"protocol_id": parent["protocol_id"], "extension_id": extension_id,
            "planning_freshness_sec": PLANNING_FRESHNESS_SEC, "replay_freshness_sec": REPLAY_FRESHNESS_SEC,
            "task": task, "planning": planning, "training_dates": [dates[k] for k in train],
            "planning_profile_sha256": full.sha256(profile), "cases": []}
        for day in (base.DAYS if task["day_type"] == "shared" else (task["day_type"],)):
            index = heldout[base.DAYS.index(day)]
            case = {"day_type": day, "test_date": dates[index], "replay": None}
            if planning["feasible"]:
                profile = Path(temporary) / f"test_{day}.txt"
                full.write_profile(profile, daily[index], hours)
                case["profile_sha256"] = full.sha256(profile)
                case["replay"] = base.run_solver(args.capsule / "solver", data / "instance.txt", data,
                    profile, args.output / f"replay_{day}", task["seed"],
                    args.output / "planning/output_solution_best.txt")
            record["cases"].append(case)
        full.validate_record(record, task, parent)
        base.dump(args.output / "task_result.json", record)


def collect(path, parent, expected, extension_id):
    records, report = full.collect_records(path, parent, expected)
    for key, record in list(records.items()):
        if (record.get("extension_id") != extension_id
                or record.get("planning_freshness_sec") != PLANNING_FRESHNESS_SEC
                or record.get("replay_freshness_sec") != REPLAY_FRESHNESS_SEC):
            report["invalid"].append({"key": key, "error": "Wrong runner or freshness protocol"})
            del records[key]
    report["missing"] = sorted(set(expected) - records.keys())
    return records, report


def pack(args, parent, extension_id):
    expected = {t["key"]: t for t in batch_tasks(parent, args.batch)}
    records, report = collect(args.input, parent, expected, extension_id)
    for key, record in records.items():
        full.base.dump(args.output / key / "task_result.json", record)
    full.base.dump(args.output / "batch_coverage.json", {"batch": args.batch, "extension_id": extension_id,
        "buffer_pct": BUFFER_PERCENT, "planning_freshness_sec": PLANNING_FRESHNESS_SEC,
        "replay_freshness_sec": REPLAY_FRESHNESS_SEC, **report})
    if report["missing"] or report["invalid"] or report["conflicting_retries"]:
        raise SystemExit("Incomplete batch; exact missing/conflicting/invalid tasks recorded")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("check", "matrix", "run-task", "pack-batch"))
    parser.add_argument("--capsule", type=Path, required=True)
    parser.add_argument("--batch", type=int)
    parser.add_argument("--task-key")
    parser.add_argument("--input", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    for name in ("capsule", "input", "output"):
        if getattr(args, name) is not None:
            setattr(args, name, getattr(args, name).resolve())
    parent, extension_id = context(args.capsule)
    if args.command == "check":
        print(json.dumps({"extension_id": extension_id, "short_id": extension_id[:12],
            "buffer_pct": BUFFER_PERCENT, "epsilon": EPSILON,
            "planning_freshness_sec": PLANNING_FRESHNESS_SEC, "replay_freshness_sec": REPLAY_FRESHNESS_SEC,
            "optimizations": 4000, "batches": 16, "scheduled_replays": 7200}))
    elif args.command == "matrix":
        print(json.dumps({"include": [{"key": t["key"]} for t in batch_tasks(parent, args.batch)]}))
    else:
        {"run-task": run_task, "pack-batch": pack}[args.command](args, parent, extension_id)


if __name__ == "__main__":
    main()
