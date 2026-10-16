"""Frozen Hanoi baseline and 10% buffer experiment, with shared unbuffered PI."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess
import tempfile
import zipfile

import numpy as np

import hanoi_buffer10_cloud as base
from hanoi_p2_cloud_pilot import Profiles, sha256
from hanoi_pilot_selected import FLAGS, write_profile

ROOT = Path(__file__).resolve().parents[1]
BUFFERS = (0, 10)
BATCH_SIZE = 250
GROUPS = ("P0", "monday", "saturday", "sunday", "PI")
CODE = ("src_v2/tabubu_HanoiTraffic_v2.cpp", "scripts/hanoi_pilot_event_bridge.cpp",
        "scripts/hanoi_full_experiment.py", "scripts/hanoi_buffer10_cloud.py",
        "scripts/hanoi_p2_cloud_pilot.py", "scripts/hanoi_pilot_selected.py",
        "scripts/hanoi_protocol_pilot.py", "scripts/requirements-hanoi-full.txt",
        "scripts/requirements-hanoi-p2-pilot.txt")


def tasks(instances):
    result = []
    positions = {instance: k for k, instance in enumerate(instances)}
    # PI is scheduled once, not once per buffer.
    for task in base.tasks(instances):
        owner = task["policy"]
        if owner == "P2":
            owner = task["day_type"]
        elif owner == "P1":
            position = ((positions[task["instance"]] * 4 + task["fold"] - 1) * len(base.SEEDS)
                        + base.SEEDS.index(task["seed"]))
            owner = base.DAYS[position % 3]
        for buffer in ((0,) if task["policy"] == "PI" else BUFFERS):
            result.append({**task, "owner": owner, "buffer_pct": buffer, "key": f"{task['key']}-b{buffer:02d}"})
    return result


def batch_tasks(all_tasks, batch, policy=None):
    if policy is not None:
        if policy not in GROUPS:
            raise ValueError("Unknown workflow group")
        all_tasks = [task for task in all_tasks if task["owner"] == policy]
    count = (len(all_tasks) + BATCH_SIZE - 1) // BATCH_SIZE
    if not 1 <= batch <= count:
        raise ValueError(f"Batch must be 1..{count}")
    return all_tasks[(batch - 1) * BATCH_SIZE:batch * BATCH_SIZE]


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def freeze(args):
    if args.output.exists():
        raise ValueError("Capsule directory already exists")
    store = Profiles(args.bundle)
    if len(store.instances) != 20:
        raise ValueError("Expected 20 fixed instances")
    for instance in store.instances:
        daily = store.daily(instance)
        if daily.shape != (12, 101, 101, 12) or not np.all(np.isfinite(daily) & (daily > 0)):
            raise ValueError("Invalid daily profiles")
    dates = store.data["dates"].astype(str).tolist()
    if any(datetime.fromisoformat(value).weekday() != (0, 5, 6)[k // 4] for k, value in enumerate(dates)):
        raise ValueError("Unexpected date ordering")
    args.output.mkdir(parents=True)
    shutil.copytree(args.bundle, args.output / "bundle")
    shutil.copy2(args.binary, args.output / "solver")
    manifest = {"experiment_id": args.experiment_id, "commit": args.commit,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "buffers_pct": list(BUFFERS), "PI_buffer_pct": 0, "start_hour": 8,
                "p0_window": [8, 17], "seeds": list(base.SEEDS), "flags": FLAGS,
                "binary_sha256": sha256(args.binary), "profiles_sha256": store.manifest["profiles_sha256"],
                "bundle_manifest_sha256": sha256(args.bundle / "manifest.json"),
                "code_sha256": {name: sha256(ROOT / name) for name in CODE},
                "tasks": tasks(store.instances), "batch_size": BATCH_SIZE}
    manifest["protocol_id"] = digest(manifest)
    base.dump(args.output / "experiment.json", manifest)
    counts = Counter(t["owner"] for t in manifest["tasks"])
    print(json.dumps({"optimizations": len(manifest["tasks"]),
                      "workflow_batches": {p: (n + BATCH_SIZE - 1) // BATCH_SIZE for p, n in counts.items()},
                      "scheduled_replays": 14400, "experiment_id": args.experiment_id}))


def load_capsule(path, check_code=True):
    manifest = json.loads((path / "experiment.json").read_text())
    raw = dict(manifest)
    saved = raw.pop("protocol_id")
    if digest(raw) != saved:
        raise ValueError("Experiment manifest hash mismatch")
    if sha256(path / "solver") != manifest["binary_sha256"]:
        raise ValueError("Frozen solver hash mismatch")
    if sha256(path / "bundle/manifest.json") != manifest["bundle_manifest_sha256"]:
        raise ValueError("Frozen bundle manifest hash mismatch")
    if check_code and any(sha256(ROOT / name) != value for name, value in manifest["code_sha256"].items()):
        raise ValueError("Source differs from frozen experiment; checkout its exact commit")
    return manifest


def run_task(args):
    manifest = load_capsule(args.capsule)
    selected = batch_tasks(manifest["tasks"], args.batch, args.policy)
    task = next((item for item in selected if item["key"] == args.task_key), None)
    if task is None:
        raise ValueError("Task not assigned to this batch")
    args.output.mkdir(parents=True)
    base.dump(args.output / "task.json", task)
    store = Profiles(args.capsule / "bundle")
    data = store.bundle / "instances" / task["instance"]
    daily = store.daily(task["instance"])
    hours, dates = store.data["hours"], store.data["dates"].astype(str).tolist()
    with tempfile.TemporaryDirectory(prefix="hanoi-profiles-") as temporary:
        generated = Path(temporary)
        speed, heldout, train = base.profiles_for(daily, hours, task["fold"], task["policy"], task["day_type"])
        profile = generated / "planning.txt"
        write_profile(profile, speed, hours)
        planning_input = args.output / "planning_instance.txt"
        base.buffered_instance(data / "instance.txt", planning_input, task["buffer_pct"] / 100)
        planning = base.run_solver(args.capsule / "solver", planning_input, data, profile,
                                   args.output / "planning", task["seed"])
        result = {"protocol_id": manifest["protocol_id"], "task": task, "planning": planning,
                  "training_dates": [dates[k] for k in train], "cases": [],
                  "planning_profile_sha256": sha256(profile)}
        if task["policy"] == "PI":
            result["test_date"] = dates[heldout[base.DAYS.index(task["day_type"])]]
        else:
            for day in (base.DAYS if task["day_type"] == "shared" else (task["day_type"],)):
                index = heldout[base.DAYS.index(day)]
                case = {"day_type": day, "test_date": dates[index], "replay": None}
                if planning["feasible"]:
                    profile = generated / f"{day}.txt"
                    write_profile(profile, daily[index], hours)
                    case["profile_sha256"] = sha256(profile)
                    case["replay"] = base.run_solver(args.capsule / "solver", data / "instance.txt", data,
                          profile, args.output / f"replay_{day}", task["seed"],
                          args.output / "planning/output_solution_best.txt")
                result["cases"].append(case)
        # A crash has no result record and cannot be mistaken for planning NP.
        base.dump(args.output / "task_result.json", result)


def validate_record(record, task, manifest):
    if record["protocol_id"] != manifest["protocol_id"] or record["task"] != task:
        raise ValueError("Wrong experiment or task")
    planning = record["planning"]
    if planning.get("iterations") != 9000:
        raise ValueError("Incomplete iteration budget")
    def check_metrics(metrics):
        for name in ("makespan_sec", "max_excess_pct", "max_excess_min", "max_truck_trip_load_kg", "max_drone_trip_load_kg"):
            if not math.isfinite(metrics[name]) or metrics[name] < 0:
                raise ValueError("Invalid numeric metric")
        if metrics["makespan_sec"] <= 0 or metrics["freshness_failure"] != (metrics["n_violated"] > 0):
            raise ValueError("Invalid duration/freshness indicator")
        if metrics["feasible"] and (metrics["freshness_failure"] or metrics["max_truck_trip_load_kg"] > 40 + 1e-8
                                    or metrics["max_drone_trip_load_kg"] > 2.27 + 1e-8):
            raise ValueError("Feasibility disagreement")
    check_metrics(planning)
    bundle = json.loads((manifest["_capsule"] / "bundle/manifest.json").read_text())
    dates = bundle["dates"]
    heldout = [task["fold"] - 1 + 4 * group for group in range(3)]
    policy, day = task["policy"], task["day_type"]
    train = [k for k in range(12) if k not in heldout]
    if policy == "PI":
        train = []
    elif policy == "P2":
        train = [k for k in train if k // 4 == base.DAYS.index(day)]
    if record["training_dates"] != [dates[k] for k in train]:
        raise ValueError("Wrong train/test split")
    expected_days = [] if policy == "PI" else list(base.DAYS if day == "shared" else (day,))
    if [case["day_type"] for case in record["cases"]] != expected_days:
        raise ValueError("Wrong replay day types")
    if policy == "PI" and record["test_date"] != dates[heldout[base.DAYS.index(day)]]:
        raise ValueError("Wrong PI test date")
    for case in record["cases"]:
        if case["test_date"] != dates[heldout[base.DAYS.index(case["day_type"])]]:
            raise ValueError("Wrong replay date")
        if (case["replay"] is not None) != planning["feasible"]:
            raise ValueError("Missing replay or replay of an infeasible plan")
        if case["replay"] is not None:
            check_metrics(case["replay"])


def collect_records(input_path, manifest, expected):
    records, duplicates, conflicts, invalid = {}, [], [], []
    for path in sorted(input_path.glob("**/task_result.json")):
        try:
            record = json.loads(path.read_text())
            key = record["task"]["key"]
            if key not in expected:
                raise ValueError("Unexpected task")
            validate_record(record, expected[key], manifest)
            if key in records:
                if records[key] != record:
                    conflicts.append(key)
                else:
                    duplicates.append(key)
            else:
                records[key] = record
        except (ValueError, KeyError, TypeError) as error:
            invalid.append({"path": str(path), "error": str(error)})
    return records, {"missing": sorted(set(expected) - records.keys()), "identical_retries": duplicates,
                     "conflicting_retries": conflicts, "invalid": invalid}


def pack_batch(args):
    manifest = load_capsule(args.capsule)
    manifest["_capsule"] = args.capsule
    expected = {t["key"]: t for t in batch_tasks(manifest["tasks"], args.batch, args.policy)}
    records, report = collect_records(args.input, manifest, expected)
    for key, record in records.items():
        base.dump(args.output / key / "task_result.json", record)
    base.dump(args.output / "batch_coverage.json", {"policy": args.policy, "batch": args.batch, **report})
    if report["missing"] or report["conflicting_retries"] or report["invalid"]:
        raise SystemExit("Batch incomplete; results preserved and exact missing task keys recorded")


def latex_table(output, summaries, buffer):
    number = lambda value: "--" if value is None else f"{value:.2f}"
    lines = [r"\begin{tabular}{llrrrr}", r"\hline",
             r"Day & Policy & NP (\%) & RF (\%) [min,max] & Gap (\%) & Max. excess (\%) \\", r"\hline"]
    for row in summaries:
        if row["buffer"] == buffer:
            rf = number(row["RF_pct"]) + " [" + number(row["RF_date_min_pct"]) + "," + number(row["RF_date_max_pct"]) + "]"
            lines.append(" & ".join([row["day_type"].title(), "$P_" + row["policy"][-1] + "$",
                         number(row["NP_pct"]), rf, number(row["gap_pct"]),
                         number(row["mean_max_excess_pct_violating"])]) + r" \\")
    lines += [r"\hline", r"\end{tabular}"]
    caption = ("Held-out performance without a buffer." if buffer == 0 else r"Held-out performance with a 10\% planning buffer.")
    label = "tab:hanoi-information" if buffer == 0 else "tab:hanoi-information-buffer10"
    wrapped = [r"\begin{table}[tb]", r"\centering", r"\small",
               r"\caption{" + caption + " RF ranges span the four test dates. Mean maximum excess is calculated over violating replays only.}",
               r"\label{" + label + "}", *lines, r"\smallskip", r"\begin{minipage}{\linewidth}", r"\footnotesize",
               "RF uses all returned plans. Gap uses returned plans with feasible PI references, irrespective of replay freshness status. ",
               "Date ranges are descriptive, not confidence intervals. Returned-plan and PI-pair counts are listed below."]
    for row in summaries:
        if row["buffer"] == buffer:
            wrapped.append(f"{row['day_type'].title()} {row['policy']}: A={row['returned_NA']}/800, B={row['paired_NB']}/800; "
                           f"RF defined on {row['RF_defined_dates']}/4 dates.\\\\")
    wrapped += [r"\end{minipage}", r"\end{table}"]
    name = "hanoi_information_table.tex" if buffer == 0 else "hanoi_buffer10_table.tex"
    (output / name).write_text("\n".join(wrapped) + "\n")


def compare_buffers(cases, summaries):
    results = []
    for day in base.DAYS:
        for policy in ("P0", "P1", "P2"):
            group = [r for r in cases if r["day_type"] == day and r["policy"] == policy]
            by_buffer = {b: {(r["instance"], r["test_date"], r["seed"]): r for r in group
                             if r["buffer_pct"] == b and r["plan_returned"]} for b in BUFFERS}
            common = sorted(by_buffer[0].keys() & by_buffer[10].keys())
            pairs = [(by_buffer[0][key], by_buffer[10][key]) for key in common]
            pi_pairs = [(a, b) for a, b in pairs if a["gap_pct"] is not None and b["gap_pct"] is not None]
            stats = {int(r["buffer"] * 100): r for r in summaries if r["day_type"] == day and r["policy"] == policy}
            def difference(key):
                a, b = stats[0][key], stats[10][key]
                return b - a if a is not None and b is not None else None
            mean = lambda values: float(np.mean(values)) if values else None
            results.append({"day_type": day, "policy": policy,
                 "NA_buffer0": stats[0]["returned_NA"], "NA_buffer10": stats[10]["returned_NA"],
                 "NP_delta_pp": difference("NP_pct"), "RF_delta_pp_all_returned": difference("RF_pct"),
                 "common_returned_cases": len(pairs), "common_PI_pairs": len(pi_pairs),
                 "common_RF0_pct": mean([100 * a["freshness_failure"] for a, b in pairs]),
                 "common_RF10_pct": mean([100 * b["freshness_failure"] for a, b in pairs]),
                 "common_RF_delta_pp": mean([100 * (b["freshness_failure"] - a["freshness_failure"]) for a, b in pairs]),
                 "common_duration_change_pct": mean([100 * (b["makespan_sec"] - a["makespan_sec"]) / a["makespan_sec"] for a, b in pairs]),
                 "common_PI_gap_change_pp": mean([b["gap_pct"] - a["gap_pct"] for a, b in pi_pairs])})
    return results


def aggregate(args):
    manifest = load_capsule(args.capsule)
    manifest["_capsule"] = args.capsule
    expected = {t["key"]: t for t in manifest["tasks"]}
    records, report = collect_records(args.input, manifest, expected)
    inherited = []
    for path in args.input.glob("**/batch_coverage.json"):
        coverage = json.loads(path.read_text())
        if coverage["conflicting_retries"] or coverage["invalid"]:
            inherited.append(str(path))
    report["batch_integrity_issues"] = inherited
    args.output.mkdir(parents=True, exist_ok=True)
    references = {(r["task"]["instance"], r["test_date"], r["task"]["seed"]): r["planning"]["makespan_sec"]
                  for r in records.values() if r["task"]["policy"] == "PI" and r["planning"]["feasible"]}
    plans, cases = [], []
    for key, record in sorted(records.items()):
        task = record["task"]
        plans.append({**task, **record["planning"]})
        for case in record["cases"]:
            replay = case["replay"]
            row = {**task, "day_type": case["day_type"], "test_date": case["test_date"],
                   "plan_returned": record["planning"]["feasible"]}
            fields = ("makespan_sec", "finish_hour", "freshness_failure", "n_violated", "max_excess_pct",
                      "max_excess_min", "failure_gt5", "max_truck_trip_load_kg", "max_drone_trip_load_kg")
            row.update({name: replay[name] if replay else None for name in fields})
            pi = references.get((task["instance"], case["test_date"], task["seed"]))
            row["PI_makespan_sec"] = pi
            row["gap_pct"] = 100 * (replay["makespan_sec"] - pi) / pi if replay and pi is not None else None
            cases.append(row)
    summaries = []
    for buffer in BUFFERS:
        for row in base.summarize([r for r in cases if r["buffer_pct"] == buffer]):
            row["buffer"] = buffer / 100
            summaries.append(row)
    case_keys = {(r["instance"], r["test_date"], r["seed"], r["policy"], r["buffer_pct"]) for r in cases}
    complete = (not report["missing"] and not report["conflicting_retries"] and not report["invalid"] and not inherited
                and len(cases) == len(case_keys) == 14400 and all(r["observed_cases"] == 800 for r in summaries))
    report.update({"complete": complete, "expected_optimizations": 10400, "completed_optimizations": len(records),
                   "scheduled_replays": 14400, "actual_replays": sum(r["plan_returned"] for r in cases),
                   "replays_after_18h_using_last_speed": sum(r["finish_hour"] is not None and r["finish_hour"] > 18 for r in cases),
                   "PI_references": len(references), "protocol_id": manifest["protocol_id"]})
    base.dump(args.output / "coverage.json", report)
    base.csv_file(args.output / "all_planning_runs.csv", plans)
    base.csv_file(args.output / "all_replay_cases.csv", cases)
    base.csv_file(args.output / "heldout_summary.csv", summaries)
    base.csv_file(args.output / "buffer_comparison.csv", compare_buffers(cases, summaries))
    print(json.dumps({k: v for k, v in report.items() if not isinstance(v, list)}, indent=2))
    if not complete:
        raise SystemExit("Incomplete experiment: diagnostic CSVs preserved; no LaTeX table generated")
    latex_table(args.output, summaries, 0)
    latex_table(args.output, summaries, .1)


def fetch_batches(args):
    # gh handles authenticated GitHub redirects without exposing the token to blob hosts.
    manifest = load_capsule(args.capsule)
    prefix = f"hanoi-full-{manifest['experiment_id']}-batch-"
    args.output.mkdir(parents=True, exist_ok=True)
    artifacts = []

    def pages(endpoint, field):
        page = 1
        while True:
            separator = "&" if "?" in endpoint else "?"
            values = json.loads(subprocess.check_output(["gh", "api", endpoint + f"{separator}per_page=100&page={page}"]))[field]
            yield from values
            if len(values) < 100:
                break
            page += 1

    for policy in ("p0", "monday", "saturday", "sunday", "pi"):
        endpoint = f"repos/{args.repo}/actions/workflows/hanoi-full-{policy}.yml/runs?created=%3E%3D{manifest['created_at'][:10]}"
        for run in pages(endpoint, "workflow_runs"):
            for artifact in pages(f"repos/{args.repo}/actions/runs/{run['id']}/artifacts", "artifacts"):
                if artifact["name"].startswith(prefix) and not artifact["expired"]:
                    artifacts.append(artifact)
    for artifact in artifacts:
        target = args.output / str(artifact["id"])
        target.mkdir()
        archive = target / "artifact.zip"
        with archive.open("wb") as stream:
            subprocess.run(["gh", "api", f"repos/{args.repo}/actions/artifacts/{artifact['id']}/zip"], stdout=stream, check=True)
        with zipfile.ZipFile(archive) as zipped:
            for name in zipped.namelist():
                if Path(name).is_absolute() or ".." in Path(name).parts:
                    raise ValueError("Unsafe artifact path")
            zipped.extractall(target)
        archive.unlink()
    base.dump(args.output / "download_manifest.json", {"experiment_id": manifest["experiment_id"], "artifacts": artifacts})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("freeze")
    prepare.add_argument("--bundle", type=Path, default=base.BUNDLE)
    prepare.add_argument("--binary", type=Path, required=True)
    prepare.add_argument("--output", type=Path, required=True)
    prepare.add_argument("--experiment-id", required=True)
    prepare.add_argument("--commit", required=True)
    for name in ("check", "matrix", "run-task", "pack-batch", "aggregate", "fetch-batches"):
        sub = commands.add_parser(name)
        sub.add_argument("--capsule", type=Path, required=True)
        if name in ("matrix", "run-task", "pack-batch"):
            sub.add_argument("--batch", type=int, required=True)
            sub.add_argument("--policy", choices=GROUPS, required=True, help="Workflow owner group, not necessarily the optimized policy")
        if name == "run-task":
            sub.add_argument("--task-key", required=True)
        if name in ("pack-batch", "aggregate"):
            sub.add_argument("--input", type=Path, required=True)
        if name == "fetch-batches":
            sub.add_argument("--repo", required=True)
        if name not in ("matrix", "check"):
            sub.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    for key in ("capsule", "binary", "bundle", "input", "output"):
        if hasattr(args, key):
            setattr(args, key, getattr(args, key).resolve())
    if args.command == "matrix":
        manifest = load_capsule(args.capsule)
        print(json.dumps({"include": [{"key": t["key"]} for t in batch_tasks(manifest["tasks"], args.batch, args.policy)]}))
    elif args.command == "check":
        manifest = load_capsule(args.capsule)
        print(manifest["protocol_id"])
    else:
        {"freeze": freeze, "run-task": run_task, "pack-batch": pack_batch,
         "aggregate": aggregate, "fetch-batches": fetch_batches}[args.command](args)


if __name__ == "__main__":
    main()
