"""Extend a frozen Hanoi experiment with buffer 20%, reusing its solver and PI."""
import argparse
from collections import Counter
import io
import json
from pathlib import Path
import subprocess
import tempfile
import time
import zipfile

import hanoi_full_experiment as full

BUFFER = 20
BATCH_SIZE = 250
WORKFLOW = "hanoi-buffer20.yml"


def tasks(parent):
    result = []
    for task in parent["tasks"]:
        if task["policy"] != "PI" and task["buffer_pct"] == 0:
            result.append({**task, "buffer_pct": BUFFER, "key": task["key"][:-2] + "20"})
    if len(result) != 4000 or len({t["key"] for t in result}) != 4000:
        raise ValueError("Expected exactly 4000 new policy optimizations")
    if Counter(t["policy"] for t in result) != {"P0": 800, "P1": 800, "P2": 2400}:
        raise ValueError("Unexpected policy coverage")
    return result


def batch_tasks(parent, batch):
    if batch not in range(1, 17):
        raise ValueError("Buffer20 batch must be 1..16")
    return tasks(parent)[(batch - 1) * BATCH_SIZE:batch * BATCH_SIZE]


def context(capsule):
    parent = full.load_capsule(capsule)
    if parent["buffers_pct"] != [0, 10] or parent["start_hour"] != 8 or parent["p0_window"] != [8, 17]:
        raise ValueError("Expected original baseline/buffer10 protocol")
    if len(parent["tasks"]) != 10400 or parent["PI_buffer_pct"] != 0:
        raise ValueError("Expected complete original task universe and unbuffered PI")
    tasks(parent)
    extension_id = full.digest({"parent_protocol_id": parent["protocol_id"], "buffer_pct": BUFFER,
        "planning_freshness_sec": 2880, "replay_freshness_sec": 3600,
        "runner_sha256": full.sha256(Path(__file__))})
    parent["_capsule"] = capsule
    return parent, extension_id


def collect(path, parent, expected, extension_id=None):
    records, report = full.collect_records(path, parent, expected)
    if extension_id is not None:
        for key, record in list(records.items()):
            if record.get("extension_id") != extension_id:
                report["invalid"].append({"key": key, "error": "Wrong extension runner/protocol"})
                del records[key]
        report["missing"] = sorted(set(expected) - records.keys())
    return records, report


def run_task(args, parent, extension_id):
    selected = batch_tasks(parent, args.batch)
    task = next((t for t in selected if t["key"] == args.task_key), None)
    if task is None:
        raise ValueError("Task does not belong to this buffer20 batch")
    args.output.mkdir(parents=True)
    base = full.base
    base.dump(args.output / "task.json", task)
    store = full.Profiles(args.capsule / "bundle")
    data = store.bundle / "instances" / task["instance"]
    daily = store.daily(task["instance"])
    hours, dates = store.data["hours"], store.data["dates"].astype(str).tolist()
    with tempfile.TemporaryDirectory(prefix="hanoi-buffer20-") as temporary:
        profile = Path(temporary) / "planning.txt"
        speed, heldout, train = base.profiles_for(daily, hours, task["fold"], task["policy"], task["day_type"])
        full.write_profile(profile, speed, hours)
        planning_input = args.output / "planning_instance.txt"
        base.buffered_instance(data / "instance.txt", planning_input, .2)
        planning = base.run_solver(args.capsule / "solver", planning_input, data, profile,
                                   args.output / "planning", task["seed"])
        record = {"protocol_id": parent["protocol_id"], "extension_id": extension_id,
                  "task": task, "planning": planning, "training_dates": [dates[k] for k in train],
                  "planning_profile_sha256": full.sha256(profile), "cases": []}
        for day in (base.DAYS if task["day_type"] == "shared" else (task["day_type"],)):
            index = heldout[base.DAYS.index(day)]
            case = {"day_type": day, "test_date": dates[index], "replay": None}
            if planning["feasible"]:
                profile = Path(temporary) / f"test_{day}.txt"
                full.write_profile(profile, daily[index], hours)
                case["profile_sha256"] = full.sha256(profile)
                # Original, unbuffered instance is essential for replay evaluation.
                case["replay"] = base.run_solver(args.capsule / "solver", data / "instance.txt", data,
                    profile, args.output / f"replay_{day}", task["seed"],
                    args.output / "planning/output_solution_best.txt")
            record["cases"].append(case)
        full.validate_record(record, task, parent)
        base.dump(args.output / "task_result.json", record)


def pack(args, parent, extension_id):
    expected = {t["key"]: t for t in batch_tasks(parent, args.batch)}
    records, report = collect(args.input, parent, expected, extension_id)
    for key, record in records.items():
        full.base.dump(args.output / key / "task_result.json", record)
    full.base.dump(args.output / "batch_coverage.json", {"batch": args.batch, "extension_id": extension_id, **report})
    if report["missing"] or report["conflicting_retries"] or report["invalid"]:
        raise SystemExit("Incomplete buffer20 batch; preserved records and exact coverage diagnostics")


def gh(endpoint):
    for attempt in range(4):
        try:
            return subprocess.check_output(["gh", "api", endpoint], timeout=180)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            if attempt == 3:
                raise
            time.sleep(2 ** attempt)


def fetch(args, parent, extension_id):
    # Keep parent records separate so old validation never encounters new tasks.
    full.fetch_batches(argparse.Namespace(capsule=args.capsule, repo=args.repo, output=args.output / "parent"))
    prefix = f"hanoi-b20-{parent['experiment_id']}-{extension_id[:12]}-batch-"
    artifacts = []
    endpoint = f"repos/{args.repo}/actions/workflows/{WORKFLOW}/runs?created=%3E%3D{parent['created_at'][:10]}"
    def pages(url, key):
        page = 1
        while True:
            separator = "&" if "?" in url else "?"
            values = json.loads(gh(url + f"{separator}per_page=100&page={page}"))[key]
            yield from values
            if len(values) < 100:
                break
            page += 1
    for run in pages(endpoint, "workflow_runs"):
        for artifact in pages(f"repos/{args.repo}/actions/runs/{run['id']}/artifacts", "artifacts"):
            if artifact["name"].startswith(prefix) and not artifact["expired"]:
                artifacts.append(artifact)
    for artifact in artifacts:
        target = args.output / "buffer20" / str(artifact["id"])
        target.mkdir(parents=True)
        with zipfile.ZipFile(io.BytesIO(gh(f"repos/{args.repo}/actions/artifacts/{artifact['id']}/zip"))) as archive:
            if any(Path(name).is_absolute() or ".." in Path(name).parts for name in archive.namelist()):
                raise ValueError("Unsafe artifact archive path")
            archive.extractall(target)
    full.base.dump(args.output / "buffer20_download_manifest.json", {"extension_id": extension_id, "artifacts": artifacts})


def compare_buffers(cases):
    rows = []
    for day in full.base.DAYS:
        for policy in ("P0", "P1", "P2"):
            selected = [r for r in cases if r["day_type"] == day and r["policy"] == policy]
            def index(buffer):
                return {(r["instance"], r["test_date"], r["seed"]): r for r in selected
                        if r["buffer_pct"] == buffer and r["plan_returned"]}
            baseline = index(0)
            for buffer in (10, 20):
                buffered = index(buffer)
                pairs = [(baseline[key], buffered[key]) for key in sorted(baseline.keys() & buffered.keys())]
                pi_pairs = [(a, b) for a, b in pairs if a["gap_pct"] is not None and b["gap_pct"] is not None]
                mean = lambda values: sum(values) / len(values) if values else None
                rows.append({"day_type": day, "policy": policy, "compared_buffer_pct": buffer,
                    "NA_buffer0": len(baseline), "NA_buffered": len(buffered),
                    "common_returned_cases": len(pairs), "common_PI_pairs": len(pi_pairs),
                    "common_RF_delta_pp": mean([100 * (b["freshness_failure"] - a["freshness_failure"]) for a, b in pairs]),
                    "common_duration_change_pct": mean([100 * (b["makespan_sec"] - a["makespan_sec"]) / a["makespan_sec"] for a, b in pairs]),
                    "common_PI_gap_change_pp": mean([b["gap_pct"] - a["gap_pct"] for a, b in pi_pairs])})
    return rows


def aggregate(args, parent, extension_id):
    baseline, baseline_coverage = collect(args.input / "parent", parent, {t["key"]: t for t in parent["tasks"]})
    extra, extra_coverage = collect(args.input / "buffer20", parent, {t["key"]: t for t in tasks(parent)}, extension_id)
    integrity = []
    for path in args.input.glob("**/batch_coverage.json"):
        report = json.loads(path.read_text())
        if report["conflicting_retries"] or report["invalid"]:
            integrity.append(str(path))
    records = {**baseline, **extra}
    references = {(r["task"]["instance"], r["test_date"], r["task"]["seed"]): r["planning"]["makespan_sec"]
                  for r in baseline.values() if r["task"]["policy"] == "PI" and r["planning"]["feasible"]}
    plans, cases = [], []
    fields = ("makespan_sec", "finish_hour", "freshness_failure", "n_violated", "max_excess_pct",
              "max_excess_min", "failure_gt5", "max_truck_trip_load_kg", "max_drone_trip_load_kg")
    for key, record in sorted(records.items()):
        task = record["task"]
        plans.append({**task, **record["planning"], "extension_id": record.get("extension_id", "baseline")})
        for case in record["cases"]:
            replay = case["replay"]
            pi = references.get((task["instance"], case["test_date"], task["seed"]))
            cases.append({**task, "day_type": case["day_type"], "test_date": case["test_date"],
                "plan_returned": record["planning"]["feasible"],
                **{name: replay[name] if replay else None for name in fields}, "PI_makespan_sec": pi,
                "gap_pct": 100 * (replay["makespan_sec"] - pi) / pi if replay and pi is not None else None})
    summaries = []
    for buffer in (0, 10, 20):
        for row in full.base.summarize([r for r in cases if r["buffer_pct"] == buffer]):
            summaries.append({**row, "buffer": buffer / 100})
    ok = lambda report: not (report["missing"] or report["invalid"] or report["conflicting_retries"])
    case_keys = {(r["instance"], r["test_date"], r["seed"], r["policy"], r["buffer_pct"]) for r in cases}
    complete = (ok(baseline_coverage) and ok(extra_coverage) and not integrity
                and len(plans) == 14400 and len(cases) == len(case_keys) == 21600
                and len(summaries) == 27 and all(row["observed_cases"] == 800 for row in summaries))
    report = {"complete": complete, "parent_protocol_id": parent["protocol_id"], "extension_id": extension_id,
        "parent": baseline_coverage, "buffer20": extra_coverage, "batch_integrity_issues": integrity,
        "planning_records": len(plans), "scheduled_replays": len(cases),
        "actual_replays": sum(r["plan_returned"] for r in cases), "PI_references": len(references),
        "replays_after_18h_using_last_speed": sum(r["finish_hour"] is not None and r["finish_hour"] > 18 for r in cases)}
    args.output.mkdir(parents=True, exist_ok=True)
    full.base.dump(args.output / "coverage.json", report)
    full.base.csv_file(args.output / "all_planning_runs.csv", plans)
    full.base.csv_file(args.output / "all_replay_cases.csv", cases)
    full.base.csv_file(args.output / "heldout_summary.csv", summaries)
    full.base.csv_file(args.output / "buffer_comparison.csv", compare_buffers(cases))
    if not complete:
        raise SystemExit("Incomplete combined experiment; diagnostics saved, no LaTeX published")
    number = lambda value: "--" if value is None else f"{value:.2f}"
    lines = [r"\begin{tabular}{llrrrrr}", r"\hline",
             r"Day & Policy & Buffer (\%) & NP (\%) & RF (\%) [min,max] & Gap (\%) & Max. excess (\%) \\", r"\hline"]
    for row in summaries:
        rf = f"{number(row['RF_pct'])} [{number(row['RF_date_min_pct'])},{number(row['RF_date_max_pct'])}]"
        lines.append(" & ".join([row["day_type"].title(), "$P_" + row["policy"][-1] + "$",
            str(round(row["buffer"] * 100)), number(row["NP_pct"]), rf, number(row["gap_pct"]),
            number(row["mean_max_excess_pct_violating"])]) + r" \\")
    lines += [r"\hline", r"\end{tabular}"]
    (args.output / "hanoi_all_buffers_table.tex").write_text("\n".join(lines) + "\n")
    print(json.dumps({"complete": True, "new_buffer20_optimizations": len(extra),
                      "all_planning_records": len(plans), "scheduled_replays": len(cases)}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("check", "matrix", "run-task", "pack-batch", "fetch", "aggregate"))
    parser.add_argument("--capsule", required=True, type=Path)
    parser.add_argument("--batch", type=int)
    parser.add_argument("--task-key")
    parser.add_argument("--input", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--repo")
    args = parser.parse_args()
    for name in ("capsule", "input", "output"):
        if getattr(args, name) is not None:
            setattr(args, name, getattr(args, name).resolve())
    parent, extension_id = context(args.capsule)
    if args.command == "check":
        print(json.dumps({"extension_id": extension_id, "short_id": extension_id[:12],
                          "optimizations": 4000, "batches": 16, "scheduled_replays": 7200}))
    elif args.command == "matrix":
        print(json.dumps({"include": [{"key": t["key"]} for t in batch_tasks(parent, args.batch)]}))
    else:
        {"run-task": run_task, "pack-batch": pack, "fetch": fetch, "aggregate": aggregate}[args.command](args, parent, extension_id)


if __name__ == "__main__":
    main()
