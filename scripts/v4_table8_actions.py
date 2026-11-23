"""Build, shard, execute and collect the V4 Table 8 GitHub Actions campaign."""

import argparse
from collections import defaultdict
import hashlib
import io
import json
import math
import os
from pathlib import Path
import platform
import subprocess
from statistics import mean
import time
import urllib.error
import urllib.parse
import urllib.request
from zipfile import ZIP_DEFLATED, ZipFile

from run_v4_table8 import read_json, run_row, sha256, summarize, summarize_table, validate_timed, write_csv, write_json

ROOT = Path(__file__).resolve().parents[1]
WORKER = "v4-table8-batch.yml"
SCENARIOS = {"A": "A", "BCD": "BCD"}
MAX_SOLVER_JOBS = 248  # The prepare and pack jobs also count towards 250.
POLL_SECONDS = 60
SOLVER = "solver.exe" if os.name == "nt" else "solver"


def build(args):
    inputs = ROOT / ".github/workload-tiebreak"
    config = read_json(ROOT / ".github/v4-table8-irace.json")
    instances = [item for item in read_json(inputs / "inputs.json")["instances"]
                 if 6 <= item["customers"] <= 100]
    if len(instances) != 80 or len({item["instance"] for item in instances}) != 80:
        raise ValueError("Expected 80 distinct instances from 6 to 100 customers")
    args.bundle.mkdir()
    subprocess.run(["g++", "-std=c++17", "-O2", str(ROOT / "src_v4/v4_table8.cpp"),
                    "-o", str(args.bundle / SOLVER)], check=True)
    with ZipFile(inputs / "inputs.zip") as source, ZipFile(args.bundle / "inputs.zip", "w", ZIP_DEFLATED) as subset:
        for inst in instances:
            for member in inst["files"]:
                data = source.read(member["path"])
                if hashlib.sha256(data).hexdigest() != member["sha256"]:
                    raise ValueError(f'Input checksum mismatch: {member["path"]}')
                subset.writestr(member["path"], data)
    write_json(args.bundle / "manifest.json", {
        **config, "source_sha": args.source_sha, "seeds": list(range(42, 52)), "instances": instances,
        "compiler": subprocess.check_output(["g++", "--version"], text=True).splitlines()[0],
        "solver_sha256": sha256(args.bundle / SOLVER),
    })


def iteration_budget(n):
    return 9 * n * (1 + math.isqrt(n - 1))


def batching(manifest, phase, budgets):
    scenarios = SCENARIOS[phase]
    instances = manifest["instances"]
    seeds = manifest["seeds"]
    per_instance = len(seeds) * len(scenarios)
    max_instances = MAX_SOLVER_JOBS // per_instance
    count = math.ceil(len(instances) / max_instances)
    capacity = math.ceil(len(instances) / count)
    batches = [[] for _ in range(count)]
    weights = [0.] * count

    def estimate(inst):
        return budgets[inst["instance"]] if budgets is not None else inst["customers"] ** 3

    for inst in sorted(instances, key=estimate, reverse=True):
        index = min((i for i in range(count) if len(batches[i]) < capacity), key=weights.__getitem__)
        batches[index].append(inst)
        weights[index] += estimate(inst)

    return [{
        "instances": bucket,
        "tasks": [
            {"instance": inst["instance"], "n": inst["customers"], "seed": seed,
             "scenario": scenario, "iterations": iteration_budget(inst["customers"]),
             "Ti_seconds": budgets[inst["instance"]] if budgets is not None else None}
            for inst in bucket for seed in seeds for scenario in scenarios
        ],
    } for bucket in batches]


def load_batches(bundle, phase, calibration):
    manifest = read_json(bundle / "manifest.json")
    budgets = read_json(calibration / "timing.json") if phase == "BCD" else None
    if budgets is not None and (set(budgets) != {i["instance"] for i in manifest["instances"]}
                                or any(not math.isfinite(t) or t <= 0 for t in budgets.values())):
        raise ValueError("BCD requires verified positive budgets for every instance")
    return manifest, batching(manifest, phase, budgets)


def prepare(args):
    manifest, batches = load_batches(args.bundle, args.phase, args.calibration)
    if manifest["source_sha"] != args.source_sha:
        raise ValueError("Worker workflow ref changed since the controller started")
    if sha256(args.bundle / SOLVER) != manifest["solver_sha256"]:
        raise ValueError("Frozen solver checksum mismatch")
    if not 0 <= args.batch < len(batches):
        raise ValueError("Batch outside published plan")
    batch = batches[args.batch]
    args.output.mkdir()
    (args.output / SOLVER).write_bytes((args.bundle / SOLVER).read_bytes())
    with ZipFile(args.bundle / "inputs.zip") as source, ZipFile(args.output / "inputs.zip", "w", ZIP_DEFLATED) as subset:
        for inst in batch["instances"]:
            for member in inst["files"]:
                subset.writestr(Path(member["path"]).name, source.read(member["path"]))
    write_json(args.output / "batch.json", {
        "source_sha": manifest["source_sha"], "solver_sha256": manifest["solver_sha256"],
        "compiler": manifest["compiler"],
        "parameters": manifest["parameters"] | manifest["vehicle_parameters"], "tasks": batch["tasks"],
    })
    with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as stream:
        print(f'matrix={json.dumps({"slot": list(range(len(batch["tasks"])))}, separators=(",", ":"))}', file=stream)


def validate_result(result, task):
    if result["scenario"] != task["scenario"] or result["n"] != task["n"] or result["seed"] != task["seed"]:
        raise ValueError(f"Result identity differs from task {task}")
    flags = result["enabled_components"]
    if (flags["simulated_annealing"] or flags["destroy_repair"] or not flags["adaptive_selection"]
            or flags["mode_switching"] != (task["scenario"] in "BD")):
        raise ValueError(f"Unexpected ablation flags for {task}")
    if result["completed_iterations"] != result["makespan_iterations"] + result["workload_iterations"]:
        raise ValueError(f"Invalid iteration counters for {task}")
    if not math.isfinite(result["elapsed_seconds"]) or result["elapsed_seconds"] <= 0:
        raise ValueError(f"Invalid solver elapsed time for {task}")
    if task["scenario"] == "A":
        if (result["stop_reason"] != "iteration_budget" or not result["feasible"]
                or result["completed_iterations"] != task["iterations"]
                or result["process_time_limit_seconds"] != 0):
            raise ValueError(f"A must finish its iteration budget with a feasible solution: {task}")
    else:
        validate_timed(result, task["scenario"], task["seed"],
                       {"customers": task["n"], "Ti_seconds": task["Ti_seconds"]}, task)


def solve(args):
    runtime = args.runtime.resolve()
    batch = read_json(runtime / "batch.json")
    task = batch["tasks"][args.slot]
    output = args.output.resolve()
    output.mkdir()
    with ZipFile(runtime / "inputs.zip") as inputs:
        for suffix in (".txt", ".vmax_ij.txt", ".theta_ijl.txt"):
            name = task["instance"] + suffix
            (output / name).write_bytes(inputs.read(name))
    params = batch["parameters"]
    segment = max(1, math.ceil(params["cseg"] * task["n"] - 1e-12))
    result_path = output / "result.json"
    command = [
        str(runtime / SOLVER), task["instance"] + ".txt", f"--scenario={task['scenario']}",
        f"--seed={task['seed']}", "--attempts=1", f"--segment-iters={segment}",
        f"--result-json={result_path}", f"--truck-vmax-file={task['instance']}.vmax_ij.txt",
        f"--truck-theta-file={task['instance']}.theta_ijl.txt",
        *(f"--{key.replace('_', '-')}={value}" for key, value in params.items()),
    ]
    timeout = None
    if task["scenario"] == "A":
        command.append(f"--iters={task['iterations']}")
    else:
        command.append(f"--process-time-limit={task['Ti_seconds']}")
        timeout = task["Ti_seconds"] + 60
    # Keep verbose console output off the artifact and out of the solver's measured file I/O.
    with (output / "stderr.log").open("wb") as stderr:
        completed = subprocess.run(command, cwd=output, stdout=subprocess.DEVNULL, stderr=stderr, timeout=timeout)
    if completed.returncode not in (0, 2):
        completed.check_returncode()
    result = read_json(result_path)
    validate_result(result, task)
    if bool(result["feasible"]) != (completed.returncode == 0):
        raise ValueError(f"Feasibility does not match exit status: {task}")
    cpu_model = platform.machine()
    cpuinfo = Path("/proc/cpuinfo")
    if cpuinfo.is_file():
        cpu_model = next((line.split(":", 1)[1].strip() for line in cpuinfo.read_text().splitlines()
                          if line.startswith("model name")), cpu_model)
    write_json(output / f"slot-{args.slot}.json", {
        "slot": args.slot, "task": task, "result": result, "source_sha": batch["source_sha"],
        "solver_sha256": batch["solver_sha256"],
        "compiler": batch["compiler"], "cpu_model": cpu_model, "image_version": os.environ.get("ImageVersion"),
    })


def pack(args):
    batch = read_json(args.runtime / "batch.json")
    records = [read_json(args.records / f"slot-{slot}.json") for slot in range(len(batch["tasks"]))]
    verify_batch(batch, records, batch)
    write_json(args.output, records)


def verify_batch(batch, records, provenance):
    if len(records) != len(batch["tasks"]):
        raise ValueError("Missing solver records in packed batch")
    for slot, (expected, record) in enumerate(zip(batch["tasks"], records)):
        if (record["slot"] != slot or record["task"] != expected
                or any(record[k] != provenance[k] for k in ("source_sha", "solver_sha256"))):
            raise ValueError(f"Packed record differs from expected task {expected}")
        validate_result(record["result"], expected)


class GitHub:
    def __init__(self):
        from download_paper_bundle import SafeRedirect
        self.opener = urllib.request.build_opener(SafeRedirect())
        self.base = f'{os.environ["GITHUB_API_URL"]}/repos/{os.environ["GITHUB_REPOSITORY"]}/actions'
        self.headers = {"Authorization": f'Bearer {os.environ["GH_TOKEN"]}',
                        "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2026-03-10"}

    def request(self, path, body=None):
        delay = 0
        for attempt in range(4):
            if delay:
                print(f"GitHub rate limit or retryable error. Waiting {delay:.0f}s.", flush=True)
                time.sleep(delay)
            req = urllib.request.Request(
                self.base + path, data=json.dumps(body).encode() if body is not None else None,
                headers=self.headers | ({"Content-Type": "application/json"} if body is not None else {}),
            )
            try:
                with self.opener.open(req, timeout=60) as response:
                    return response.read()
            except urllib.error.HTTPError as error:
                retry = error.headers.get("Retry-After")
                limited = error.code == 429 or (error.code == 403 and
                          (retry or error.headers.get("X-RateLimit-Remaining") == "0"
                           or b"secondary rate limit" in error.read().lower()))
                if limited:
                    reset = int(error.headers.get("X-RateLimit-Reset", "0"))
                    delay = int(retry) if retry else max(60 * 2**attempt, reset - time.time() + 1
                            if error.headers.get("X-RateLimit-Remaining") == "0" else 0)
                elif body is None and error.code in (500, 502, 503, 504):
                    delay = 2**attempt
                else:
                    raise
                if attempt == 3:
                    raise
            except (urllib.error.URLError, TimeoutError):
                # A timed-out dispatch can already have created a run. A controller rerun rediscovers it.
                if body is not None or attempt == 3:
                    raise
                delay = 2**attempt

    def json(self, path, body=None):
        return json.loads(self.request(path, body))

    def collect(self, run, name):
        query = urllib.parse.urlencode({"name": name})
        artifacts = self.json(f"/runs/{run}/artifacts?{query}")["artifacts"]
        available = [a for a in artifacts if a["name"] == name and not a["expired"]]
        if len(available) != 1:
            raise ValueError(f"Expected one packed artifact {name} in run {run}")
        with ZipFile(io.BytesIO(self.request(f"/artifacts/{available[0]['id']}/zip"))) as archive:
            return json.loads(archive.read("packed.json"))


def orchestrate(args):
    manifest, batches = load_batches(args.bundle, args.phase, args.calibration)
    api = GitHub()
    campaign = int(os.environ["GITHUB_RUN_ID"])
    ref = os.environ["GITHUB_REF_NAME"]
    sha = manifest["source_sha"]
    if sha != os.environ["GITHUB_SHA"]:
        raise ValueError("Controller commit differs from frozen bundle")
    if not 1 <= args.parallel_jobs <= 500:
        raise ValueError("parallel_jobs must be between 1 and 500")
    cap = math.ceil(args.parallel_jobs / len(batches))
    started = api.json(f"/runs/{campaign}")["created_at"]
    title = {f"V4T8 {campaign} {args.phase} {i}": i for i in range(len(batches))}
    worker = f"/workflows/{WORKER}"
    filters = urllib.parse.urlencode(
        {"event": "workflow_dispatch", "created": f">={started}", "per_page": 100}
    )

    def status():
        found = {}
        for page in range(1, 11):
            page_rows = api.json(f"{worker}/runs?{filters}&page={page}")["workflow_runs"]
            for run in page_rows:
                index = title.get(run["display_title"])
                if index is not None:
                    if index in found:
                        raise ValueError(f"Duplicate runs for {run['display_title']}")
                    if run["head_sha"] != sha:
                        raise ValueError(f"Worker was dispatched on a different commit: {run['html_url']}")
                    found[index] = run
            if len(page_rows) < 100:
                return found
        raise RuntimeError("Too many matching workflow runs to inventory safely")

    existing = status()
    run_ids = {i: r["id"] for i, r in existing.items()}
    for i in range(len(batches)):
        if i in run_ids:
            continue
        dispatched = api.json(f"{worker}/dispatches", {"ref": ref, "inputs": {
            "controller": str(campaign), "phase": args.phase, "batch": str(i), "parallel": str(cap),
        }})
        run_ids[i] = dispatched["workflow_run_id"]
        print(f"Dispatched {args.phase} batch {i}: {dispatched['html_url']}", flush=True)
        time.sleep(1)

    deadline = time.monotonic() + 5 * 3600
    while time.monotonic() < deadline:
        found = status()
        if set(found) == set(run_ids) and all(run["status"] == "completed" for run in found.values()):
            break
        time.sleep(POLL_SECONDS)
    else:
        raise TimeoutError("Batches still running. Re-run the controller job to resume monitoring.")
    if any(found[i]["id"] != run for i, run in run_ids.items()):
        raise ValueError("Dispatched run IDs differ from listed batch IDs")
    failed = [r for r in found.values() if r["conclusion"] != "success"]
    if failed:
        raise RuntimeError("Re-run failed worker jobs, then re-run this controller job: " +
                           ", ".join(r["html_url"] for r in failed))

    records = []
    provenance = []
    for i, batch in enumerate(batches):
        run = run_ids[i]
        packed = api.collect(run, f"v4-table8-packed-{found[i]['run_attempt']}")
        verify_batch(batch, packed, manifest)
        records.extend(packed)
        provenance.append({"batch": i, "run_id": run, "attempt": found[i]["run_attempt"]})
    args.output.mkdir()
    write_json(args.output / "batch_runs.json", provenance)
    write_json(args.output / "records.json", records)


def calibrate(args):
    manifest, batches = load_batches(args.bundle, "A", None)
    records = read_json(args.output / "records.json")
    if len(records) != sum(len(batch["tasks"]) for batch in batches):
        raise ValueError("Incomplete A phase")
    offset = 0
    by_instance = defaultdict(list)
    for batch in batches:
        end = offset + len(batch["tasks"])
        verify_batch(batch, records[offset:end], manifest)
        for record in records[offset:end]:
            by_instance[record["task"]["instance"]].append(record["result"]["elapsed_seconds"])
        offset = end
    budgets = {name: mean(samples) for name, samples in by_instance.items()}
    write_json(args.output / "timing.json", budgets)
    write_csv(args.output / "Ti.csv", [
        {"instance": name, "Ti_seconds": budget, "seeds": len(by_instance[name]),
         "min_seconds": min(by_instance[name]), "max_seconds": max(by_instance[name])}
        for name, budget in budgets.items()
    ])


def report(args):
    budgets = read_json(args.calibration / "timing.json")
    groups = defaultdict(list)
    for source in (args.calibration, args.timed):
        path = source / "records.json"
        for record in read_json(path):
            task = record["task"]
            name = task["instance"]
            row = run_row(record["result"], {"instance": name, "customers": task["n"], "Ti_seconds": budgets[name]},
                          "iteration_calibration" if task["scenario"] == "A" else "timed", path.name)
            row.pop("result_json")
            groups[name].append(row | {key: record[key] for key in ("cpu_model", "image_version", "compiler")})
    if set(groups) != set(budgets):
        raise ValueError("Report instance coverage does not match calibration")
    rows, summaries = [], []
    for name, group in sorted(groups.items()):
        keys = {(row["scenario"], row["seed"]) for row in group}
        if len(group) != 40 or keys != {(scenario, seed) for scenario in "ABCD" for seed in range(42, 52)}:
            raise ValueError(f"Incomplete or duplicate results for {name}")
        rows.extend(sorted(group, key=lambda row: (row["scenario"], row["seed"])))
        summaries.extend(summarize(group, "ABCD"))
    args.output.mkdir()
    write_csv(args.output / "runs.csv", rows)
    write_csv(args.output / "by_instance.csv", summaries)
    write_csv(args.output / "table8_summary.csv", list(summarize_table(summaries, "ABCD")))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for action, options in [
        (build, ["bundle", "source_sha"]),
        (prepare, ["bundle", "phase", "calibration", "batch", "source_sha", "output"]),
        (solve, ["runtime", "slot", "output"]),
        (pack, ["runtime", "records", "output"]),
        (orchestrate, ["bundle", "phase", "calibration", "parallel_jobs", "output"]),
        (calibrate, ["bundle", "output"]),
        (report, ["calibration", "timed", "output"]),
    ]:
        sub = commands.add_parser(action.__name__)
        sub.set_defaults(action=action)
        for option in options:
            if option == "phase":
                sub.add_argument("--phase", choices=SCENARIOS, required=True)
            else:
                types = {"source_sha": str, "batch": int, "slot": int, "parallel_jobs": int}
                sub.add_argument("--" + option.replace("_", "-"), type=types.get(option, Path),
                                 required=option != "calibration" or action == report)
    args = parser.parse_args()
    if getattr(args, "phase", None) == "BCD" and args.calibration is None:
        parser.error("--calibration is required for phase BCD")
    args.action(args)


if __name__ == "__main__":
    main()
