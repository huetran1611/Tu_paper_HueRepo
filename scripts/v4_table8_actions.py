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
SCENARIOS = {"A": "A", "BCD": "BCD", "DE": "DE"}
MAX_SOLVER_JOBS = 248  # The prepare and pack jobs also count towards 250.
POLL_SECONDS = 60
SOLVER = "solver.exe" if os.name == "nt" else "solver"
N200_TIME_LIMIT = 2 * 60 * 60


def build(args):
    inputs = ROOT / ".github/workload-tiebreak"
    config = read_json(ROOT / ".github/v4-table8-irace.json")
    n200 = args.dataset == "n200"
    all_instances = read_json(inputs / "inputs.json")["instances"]
    bounds = {"n200": (200, 200, 16), "n6-n100": (6, 100, 80), "de": (6, 200, 96)}
    low, high, expected = bounds[args.dataset]
    instances = [i for i in all_instances if low <= i["customers"] <= high]
    if len(instances) != expected or len({item["instance"] for item in instances}) != expected:
        raise ValueError(f"Expected {expected} distinct instances in {args.dataset}")
    if not 1 <= args.parallel_jobs <= 500:
        raise ValueError("parallel_jobs must be between 1 and 500")
    args.bundle.mkdir()
    if args.dataset == "de":
        baseline = read_json(ROOT / ".github/v4-table8-de-baseline.json")
        refs = baseline["instances"]
        if set(refs) != {i["instance"] for i in instances}:
            raise ValueError("Frozen A baseline must cover all 96 instances")
        for inst in instances:
            row = refs[inst["instance"]]
            if (row["customers"] != inst["customers"] or row["seeds"] != 10
                    or any(not math.isfinite(row[key]) or row[key] <= 0
                           for key in ("Ti_seconds", "best_makespan", "mean_makespan"))
                    or row["Ti_seconds"] > N200_TIME_LIMIT):
                raise ValueError(f"Invalid frozen A baseline: {inst['instance']}")
        write_json(args.bundle / "baseline.json", baseline)
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
        "dataset": args.dataset, "parallel_jobs": args.parallel_jobs,
        "max_solve_seconds": N200_TIME_LIMIT if n200 else None,
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
    n200 = manifest.get("dataset") == "n200"
    de = phase == "DE"
    max_instances = (MAX_SOLVER_JOBS - int(n200 or de)) // per_instance
    # Keep the larger input ZIP small enough to transfer to every solver job.
    if n200:
        count = 4
    elif de:
        count = 10
    else:
        count = math.ceil(len(instances) / max_instances)
    capacity = math.ceil(len(instances) / count)
    if capacity > max_instances:
        raise ValueError("Batch plan would exceed the solver-job limit")
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
             "Ti_seconds": budgets[inst["instance"]] if budgets is not None else None,
             **({"corrected_d": True} if de and scenario == "D" else {}),
             **({"calibration_limit_seconds": manifest["max_solve_seconds"]}
                if phase == "A" and manifest.get("max_solve_seconds") is not None else {})}
            for inst in bucket for seed in seeds for scenario in scenarios
        ],
    } for bucket in batches]


def load_batches(bundle, phase, calibration):
    manifest = read_json(bundle / "manifest.json")
    if phase == "DE":
        if manifest["dataset"] != "de":
            raise ValueError("Phase DE requires a frozen DE bundle")
        budgets = {name: row["Ti_seconds"] for name, row in
                   read_json(bundle / "baseline.json")["instances"].items()}
    else:
        budgets = read_json(calibration / "timing.json") if phase == "BCD" else None
    if budgets is not None and (set(budgets) != {i["instance"] for i in manifest["instances"]}
                                or any(not math.isfinite(t) or t <= 0 for t in budgets.values())
                                or (manifest.get("max_solve_seconds") is not None and
                                    max(budgets.values()) > manifest["max_solve_seconds"])):
        raise ValueError("Timed phases require verified positive budgets for every instance")
    return manifest, batching(manifest, phase, budgets)


def prepare(args):
    manifest, batches = load_batches(args.bundle, args.phase, args.calibration)
    if manifest["source_sha"] != args.source_sha:
        raise ValueError("Worker workflow ref changed since the controller started")
    if sha256(args.bundle / SOLVER) != manifest["solver_sha256"]:
        raise ValueError("Frozen solver checksum mismatch")
    if not 0 <= args.batch < len(batches):
        raise ValueError("Batch outside published plan")
    if not 1 <= args.parallel <= MAX_SOLVER_JOBS:
        raise ValueError("Invalid solver concurrency for batch")
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
        print(f"parallel={args.parallel}", file=stream)


def validate_result(result, task):
    if result["scenario"] != task["scenario"] or result["n"] != task["n"] or result["seed"] != task["seed"]:
        raise ValueError(f"Result identity differs from task {task}")
    flags = result["enabled_components"]
    if (flags["simulated_annealing"] or flags["destroy_repair"] or not flags["adaptive_selection"]
            or flags["mode_switching"] != (task["scenario"] in "BD")
            or flags["workload_tie_break"] != (task["scenario"] == "E")):
        raise ValueError(f"Unexpected ablation flags for {task}")
    if task.get("corrected_d") and (result["makespan_fleet_neighborhoods"] != [7, 8]
                                    or result["workload_fleet_neighborhoods"] != [7, 8]):
        raise ValueError(f"Corrected D must use fleet scope only on N7/N8 in both modes: {task}")
    if result["completed_iterations"] != result["makespan_iterations"] + result["workload_iterations"]:
        raise ValueError(f"Invalid iteration counters for {task}")
    if not math.isfinite(result["elapsed_seconds"]) or result["elapsed_seconds"] <= 0:
        raise ValueError(f"Invalid solver elapsed time for {task}")
    if task["scenario"] == "A":
        limit = task.get("calibration_limit_seconds")
        complete = result["stop_reason"] == "iteration_budget" and result["completed_iterations"] == task["iterations"]
        capped = (limit is not None and result["stop_reason"] == "process_time_limit"
                  and result["completed_iterations"] < task["iterations"])
        if (not (complete or capped) or result["iteration_budget"] != task["iterations"]
                or result["process_time_limit_seconds"] != (limit or 0)
                or (limit is None and not result["feasible"])):
            raise ValueError(f"A must finish the iteration budget or reach the configured time cap: {task}")
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
        if (limit := task.get("calibration_limit_seconds")) is not None:
            command.append(f"--process-time-limit={limit}")
            timeout = limit + 60
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
        artifact = self.packed_artifact(run, name)
        if artifact is None:
            raise ValueError(f"Expected packed artifact {name} in run {run}")
        return self.read_packed(artifact)

    def packed_artifact(self, run, name):
        query = urllib.parse.urlencode({"name": name})
        artifacts = self.json(f"/runs/{run}/artifacts?{query}")["artifacts"]
        available = [a for a in artifacts if a["name"] == name and not a["expired"]]
        if len(available) > 1:
            raise ValueError(f"Multiple packed artifacts {name} in run {run}")
        return available[0]["id"] if available else None

    def read_packed(self, artifact):
        with ZipFile(io.BytesIO(self.request(f"/artifacts/{artifact}/zip"))) as archive:
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
    time_limited = defaultdict(int)
    limit = manifest.get("max_solve_seconds")
    for batch in batches:
        end = offset + len(batch["tasks"])
        verify_batch(batch, records[offset:end], manifest)
        for record in records[offset:end]:
            name = record["task"]["instance"]
            result = record["result"]
            capped = result["stop_reason"] == "process_time_limit"
            elapsed = result["elapsed_seconds"]
            if limit is not None:
                elapsed = limit if capped else min(elapsed, limit)
            by_instance[name].append(elapsed)
            time_limited[name] += capped
        offset = end
    budgets = {name: mean(samples) for name, samples in by_instance.items()}
    write_json(args.output / "timing.json", budgets)
    write_csv(args.output / "Ti.csv", [
        {"instance": name, "Ti_seconds": budget, "seeds": len(by_instance[name]),
         "min_seconds": min(by_instance[name]), "max_seconds": max(by_instance[name]),
         "A_time_limited_runs": time_limited[name]}
        for name, budget in budgets.items()
    ])


class EventCampaign:
    """Advance short-lived controller runs when worker packs finish."""

    def __init__(self, campaign, bundle, dataset):
        self.api = GitHub()
        self.campaign = campaign
        self.manifest = read_json(bundle / "manifest.json")
        self.dataset = dataset
        if (self.manifest["dataset"] != dataset
                or (dataset == "n200" and self.manifest["max_solve_seconds"] != N200_TIME_LIMIT)):
            raise ValueError(f"Expected the frozen {dataset} campaign bundle")
        origin = self.api.json(f"/runs/{campaign}")
        if (origin["head_sha"] != self.manifest["source_sha"]
                or origin["display_title"] != f"V4 Table 8 {dataset} {campaign}"
                or origin["path"].split("@")[0] != f".github/workflows/v4-table8-{dataset}.yml"):
            raise ValueError("Campaign ID does not match the frozen controller")
        self.worker = f"/workflows/{WORKER}"
        self.filters = urllib.parse.urlencode(
            {"created": f">={origin['created_at']}", "event": "workflow_dispatch", "per_page": 100}
        )
        self.runs = {}
        prefix = f"V4T8 {campaign} "
        for page in range(1, 11):
            runs = self.api.json(f"{self.worker}/runs?{self.filters}&page={page}")["workflow_runs"]
            for run in runs:
                title = run["display_title"]
                if not title.startswith(prefix):
                    continue
                phase, batch = title.removeprefix(prefix).split()
                key = phase, int(batch)
                if key in self.runs:
                    raise ValueError(f"Duplicate worker runs for {title}")
                self.runs[key] = run
            if len(runs) < 100:
                break
        else:
            raise ValueError("Too many workflow runs to inventory this campaign")

    def launch_missing(self, phase, batches, calibration_run=None):
        parallel = math.ceil(self.manifest["parallel_jobs"] / len(batches))
        for i in range(len(batches)):
            if (phase, i) in self.runs:
                continue
            inputs = {"controller": str(self.campaign), "phase": phase, "batch": str(i),
                      "parallel": str(parallel), "source_sha": self.manifest["source_sha"],
                      f"notify_{self.dataset}": "yes"}
            if calibration_run is not None:
                inputs["calibration_run"] = str(calibration_run)
            dispatched = self.api.json(f"{self.worker}/dispatches", {
                "ref": os.environ["GITHUB_REF_NAME"], "inputs": inputs,
            })
            print(f"Dispatched {phase} batch {i}: {dispatched['html_url']}", flush=True)

    def read_phase(self, phase, batches, output):
        work = []
        for i, batch in enumerate(batches):
            run = self.runs.get((phase, i))
            if not run:
                return False
            artifact = self.api.packed_artifact(run["id"], f"v4-table8-packed-{run['run_attempt']}")
            if artifact is None:
                if run["status"] == "completed" and run["conclusion"] != "success":
                    raise RuntimeError(f"Re-run failed worker jobs and resume the campaign: {run['html_url']}")
                return False
            work.append((i, batch, run, artifact))
        output.mkdir(parents=True)
        records, provenance = [], []
        for i, batch, run, artifact in work:
            packed = self.api.read_packed(artifact)
            verify_batch(batch, packed, self.manifest)
            records.extend(packed)
            provenance.append({"batch": i, "run_id": run["id"], "attempt": run["run_attempt"]})
        write_json(output / "records.json", records)
        write_json(output / "batch_runs.json", provenance)
        return True


def advance_n200(args):
    campaign = EventCampaign(args.campaign, args.bundle, "n200")
    _, a_batches = load_batches(args.bundle, "A", None)
    campaign.launch_missing("A", a_batches)
    calibration = args.output / "calibration"
    if not campaign.read_phase("A", a_batches, calibration):
        print("Waiting for the remaining A batches to publish their results.", flush=True)
        return
    calibrate(argparse.Namespace(bundle=args.bundle, output=calibration))
    _, timed_batches = load_batches(args.bundle, "BCD", calibration)
    timed = args.output / "timed"
    completed = campaign.read_phase("BCD", timed_batches, timed)
    if completed:
        report(argparse.Namespace(calibration=calibration, timed=timed, output=args.output / "report"))
    with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as stream:
        print("calibrated=true", file=stream)
        print(f"reported={'true' if completed else 'false'}", file=stream)


def launch_n200_timed(args):
    campaign = EventCampaign(args.campaign, args.bundle, "n200")
    _, batches = load_batches(args.bundle, "BCD", args.calibration)
    campaign.launch_missing("BCD", batches, calibration_run=os.environ["GITHUB_RUN_ID"])


def advance_de(args):
    campaign = EventCampaign(args.campaign, args.bundle, "de")
    _, batches = load_batches(args.bundle, "DE", None)
    campaign.launch_missing("DE", batches)
    if not campaign.read_phase("DE", batches, args.output):
        print("Waiting for the remaining D/E batches to publish their results.", flush=True)
        return

    baseline = read_json(args.bundle / "baseline.json")
    write_json(args.output / "baseline.json", baseline)
    groups = defaultdict(list)
    for record in read_json(args.output / "records.json"):
        task = record["task"]
        name = task["instance"]
        reference = baseline["instances"][name]
        row = run_row(record["result"], {"instance": name, "customers": task["n"],
                                        "Ti_seconds": reference["Ti_seconds"]}, "timed", "")
        row.pop("result_json")
        groups[name].append(row | {"cpu_model": record["cpu_model"], "image_version": record["image_version"]})

    runs, summaries = [], []
    for name in sorted(groups, key=lambda value: tuple(map(int, value.split(".")))):
        group = sorted(groups[name], key=lambda row: (row["scenario"], row["seed"]))
        runs.extend(group)
        reference = baseline["instances"][name]
        for row in summarize(group, "DE"):
            if row["best_makespan"] is not None:
                row["best_gap_vs_a_pct"] = 100 * (row["best_makespan"] / reference["best_makespan"] - 1)
            if row["feasible_runs"] == row["runs"]:
                row["mean_gap_vs_a_pct"] = 100 * (row["mean_makespan"] / reference["mean_makespan"] - 1)
            summaries.append(row)
    write_csv(args.output / "runs.csv", runs)
    write_csv(args.output / "by_instance.csv", summaries)
    scopes = {"all": summaries,
              "6-100": [row for row in summaries if row["customers"] <= 100],
              "N200": [row for row in summaries if row["customers"] == 200]}
    write_csv(args.output / "table8_summary.csv", [
        {"scope": scope, **row}
        for scope, rows in scopes.items() for row in summarize_table(rows, "DE")
    ])
    with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as stream:
        print("reported=true", file=stream)


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
        (build, ["bundle", "source_sha", "dataset", "parallel_jobs"]),
        (prepare, ["bundle", "phase", "calibration", "batch", "source_sha", "parallel", "output"]),
        (solve, ["runtime", "slot", "output"]),
        (pack, ["runtime", "records", "output"]),
        (orchestrate, ["bundle", "phase", "calibration", "parallel_jobs", "output"]),
        (calibrate, ["bundle", "output"]),
        (report, ["calibration", "timed", "output"]),
        (advance_n200, ["bundle", "campaign", "output"]),
        (launch_n200_timed, ["bundle", "campaign", "calibration"]),
        (advance_de, ["bundle", "campaign", "output"]),
    ]:
        sub = commands.add_parser(action.__name__)
        sub.set_defaults(action=action)
        for option in options:
            if option == "phase":
                sub.add_argument("--phase", choices=SCENARIOS, required=True)
            elif option == "dataset":
                sub.add_argument("--dataset", choices=("n6-n100", "n200", "de"), default="n6-n100")
            elif option == "parallel_jobs" and action == build:
                sub.add_argument("--parallel-jobs", type=int, default=20)
            else:
                types = {"source_sha": str, "batch": int, "slot": int, "parallel": int,
                         "parallel_jobs": int, "campaign": int}
                sub.add_argument("--" + option.replace("_", "-"), type=types.get(option, Path),
                                 required=option != "calibration" or action in (report, launch_n200_timed))
    args = parser.parse_args()
    if getattr(args, "phase", None) == "BCD" and args.calibration is None:
        parser.error("--calibration is required for phase BCD")
    args.action(args)


if __name__ == "__main__":
    main()
