"""Run the PDF's scenarios under per-instance V4 time budgets."""

import argparse
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
from statistics import mean

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "experiments/tables678_irace_v4/full_20261123"
SCENARIOS = "ABCDE"


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def write_csv(path, rows):
    temporary = path.with_suffix(".csv.tmp")
    with temporary.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def load_inputs(baseline, chosen_instances, chosen_seeds):
    manifest = read_json(baseline / "bundle/manifest.json")
    if manifest["algorithm_policy"] != "irace-v4":
        raise ValueError("Expected the V4 irace calibration manifest")
    allowed = {item["instance"]: item for item in manifest["instances"]
               if 6 <= item["customers"] <= 100}
    instances = list(dict.fromkeys(chosen_instances or allowed))
    seeds = list(dict.fromkeys(chosen_seeds or manifest["seeds"]))
    if set(instances) - allowed.keys() or set(seeds) - set(manifest["seeds"]):
        raise ValueError("Select instances and seeds from the V4 calibration manifest")
    budget_path = baseline / "progress_report/Ti_adaptive_mk_n6_n100.csv"
    with budget_path.open(newline="", encoding="utf-8-sig") as stream:
        budgets = {row["instance"]: row for row in csv.DictReader(stream)}
    for name in instances:
        budget = budgets[name]
        if (int(budget["customers"]) != allowed[name]["customers"]
                or int(budget["completed_runs"]) != len(manifest["seeds"])
                or not math.isfinite(float(budget["Ti_seconds"]))
                or float(budget["Ti_seconds"]) <= 0):
            raise ValueError(f"Invalid calibrated time for {name}")
        for source in allowed[name]["files"]:
            path = baseline / "inputs" / Path(source["path"]).name
            if sha256(path) != source["sha256"]:
                raise ValueError(f"Calibration input hash mismatch: {path}")
    return manifest, instances, seeds, budgets, budget_path


def cached_a(baseline, manifest, instance, seed, budget):
    key = f"{instance}-adaptive_mk-s{seed}"
    path = baseline / "results" / key / "completed.json"
    record = read_json(path)
    result = record["solver_result"]
    flags = result["enabled_components"]
    if (record["protocol_id"] not in {manifest["protocol_id"], manifest.get("previous_protocol_id")}
            or record["task"]["key"] != key or record["termination"] != "iteration_budget"
            or result["stop_reason"] != "iteration_budget" or not result["feasible"]
            or result["seed"] != seed or result["n"] != int(budget["customers"])
            or result["completed_iterations"] != int(budget["iterations_per_run"])
            or not flags["adaptive_selection"]
            or any(flags[name] for name in ("mode_switching", "simulated_annealing", "destroy_repair"))):
        raise ValueError(f"Invalid cached calibration result: {path}")
    return {
        **result,
        "scenario": "A",
        "cpu_seconds": record["cpu_seconds"],
        "makespan_iterations": result["completed_iterations"],
        "workload_iterations": 0,
        "f_mk": result["makespan"],
        "source": "cached_calibration",
        "source_record": str(path),
    }


def timed_run(binary, env, run_dir, baseline, manifest, instance, scenario, seed, budget):
    run_dir.mkdir(parents=True, exist_ok=True)
    inputs = baseline / "inputs"
    result_path = run_dir / "result.json"
    if result_path.exists():
        saved = read_json(result_path)
        if saved["stop_reason"] == "process_time_limit":
            validate_timed(saved, scenario, seed, budget, result_path)
            return saved
    segment = max(1, math.ceil(manifest["parameters"]["cseg"] * int(budget["customers"]) - 1e-12))
    parameters = manifest["parameters"] | manifest["vehicle_parameters"]
    command = [
        str(binary), str(inputs / f"{instance}.txt"), f"--scenario={scenario}",
        f"--seed={seed}", "--attempts=1", f"--segment-iters={segment}",
        f"--process-time-limit={budget['Ti_seconds']}", f"--result-json={result_path}",
        f"--truck-vmax-file={inputs / (instance + '.vmax_ij.txt')}",
        f"--truck-theta-file={inputs / (instance + '.theta_ijl.txt')}",
        *(f"--{name.replace('_', '-')}={value}" for name, value in parameters.items()),
    ]
    write_json(run_dir / "request.json", {"command": command, "Ti_seconds": float(budget["Ti_seconds"])})
    with (run_dir / "stdout.log").open("wb") as stdout, (run_dir / "stderr.log").open("wb") as stderr:
        completed = subprocess.run(command, cwd=run_dir, env=env, stdout=stdout, stderr=stderr)
    if completed.returncode not in (0, 2) or not result_path.exists():
        raise RuntimeError(f"Solver failed with exit {completed.returncode}: {run_dir}")
    result = read_json(result_path)
    validate_timed(result, scenario, seed, budget, result_path)
    if bool(result["feasible"]) != (completed.returncode == 0):
        raise RuntimeError(f"Exit code and feasibility disagree: {result_path}")
    return result


def validate_timed(result, scenario, seed, budget, result_path):
    if (result["stop_reason"] != "process_time_limit" or result["scenario"] != scenario
            or result["n"] != int(budget["customers"]) or result["seed"] != seed
            or result["process_time_limit_seconds"] != float(budget["Ti_seconds"])
            or result["completed_iterations"] != result["makespan_iterations"] + result["workload_iterations"]):
        raise RuntimeError(f"Invalid timed result: {result_path}")


def run_row(result, budget, source, path):
    return {
        "instance": budget["instance"], "customers": int(budget["customers"]),
        "Ti_seconds": float(budget["Ti_seconds"]), "source": source,
        **{field: result.get(field) for field in (
            "scenario", "seed", "elapsed_seconds", "cpu_seconds", "stop_reason", "feasible",
            "makespan", "f_mk", "f_wl", "reporting_tau", "iteration_budget",
            "completed_iterations", "makespan_iterations", "workload_iterations",
        )},
        **{f"N{op}_calls": calls for op, calls in enumerate(result["telemetry"]["operator_calls"])},
        "result_json": str(path),
    }


def summarize(rows, scenarios=SCENARIOS):
    best_a = mean_a = None
    for scenario in scenarios:
        runs = [row for row in rows if row["scenario"] == scenario]
        feasible = [run["makespan"] for run in runs if run["feasible"]]
        best = min(feasible) if feasible else None
        average = mean(feasible) if feasible else None
        if scenario == "A":
            best_a = best
            mean_a = average if len(feasible) == len(runs) else None
        yield {
            **{field: runs[0][field] for field in ("instance", "customers", "Ti_seconds", "scenario", "source")},
            "runs": len(runs), "feasible_runs": len(feasible),
            "best_makespan": best, "mean_makespan": average,
            "best_gap_vs_a_pct": 100 * (best / best_a - 1) if best is not None and best_a else None,
            "mean_gap_vs_a_pct": (100 * (average / mean_a - 1)
                                  if mean_a and len(feasible) == len(runs) else None),
            "mean_elapsed_seconds": mean(run["elapsed_seconds"] for run in runs),
            "mean_cpu_seconds": mean(run["cpu_seconds"] for run in runs) if all(
                run["cpu_seconds"] is not None for run in runs) else None,
            "mean_completed_iterations": mean(run["completed_iterations"] for run in runs),
            "mean_makespan_iterations": mean(run["makespan_iterations"] for run in runs),
            "mean_workload_iterations": mean(run["workload_iterations"] for run in runs),
        }


def summarize_table(instance_rows, scenarios=SCENARIOS):
    for scenario in scenarios:
        instances = [row for row in instance_rows if row["scenario"] == scenario]
        best_gaps = [row["best_gap_vs_a_pct"] for row in instances if row["best_gap_vs_a_pct"] is not None]
        mean_gaps = [row["mean_gap_vs_a_pct"] for row in instances if row["mean_gap_vs_a_pct"] is not None]
        yield {
            "scenario": scenario, "source": instances[0]["source"],
            "instances": len(instances),
            "runs": sum(row["runs"] for row in instances),
            "feasible_runs": sum(row["feasible_runs"] for row in instances),
            "best_gap_pairs": len(best_gaps), "mean_gap_pairs": len(mean_gaps),
            "mean_best_gap_vs_a_pct": mean(best_gaps) if best_gaps else None,
            "mean_mean_gap_vs_a_pct": mean(mean_gaps) if mean_gaps else None,
            "mean_completed_iterations": mean(row["mean_completed_iterations"] for row in instances),
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, default=BASELINE)
    parser.add_argument("--compiler", help="Defaults to the compiler directory in the calibration manifest")
    parser.add_argument("--instance", action="append", help="Repeat to select several instances")
    parser.add_argument("--seed", action="append", type=int, help="Repeat to select several seeds")
    parser.add_argument("--rerun-a", action="store_true", help="Apply the same time limit to A instead of reusing calibration results")
    parser.add_argument("--resume", action="store_true", help="Resume an output directory with the same settings")
    args = parser.parse_args()
    baseline, output = args.baseline.resolve(), args.output.resolve()
    manifest, instances, seeds, budgets, budget_path = load_inputs(
        baseline, args.instance, args.seed
    )
    windows = os.name == "nt"
    compiler_dir = Path(manifest["compiler_directory"])
    default_compiler = compiler_dir / ("g++.exe" if windows else "g++")
    compiler = args.compiler or (str(default_compiler) if default_compiler.is_file() else "g++")
    binary = output / ("v4_table8.exe" if windows else "v4_table8")
    env = os.environ.copy()
    env.update(OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1")
    if compiler_dir.is_dir():
        env["PATH"] = str(compiler_dir) + os.pathsep + env["PATH"]
    metadata_path = output / "experiment.json"
    metadata = {
        "baseline": str(baseline), "calibration_protocol": manifest["protocol_id"],
        "budget_csv": str(budget_path), "budget_csv_sha256": sha256(budget_path),
        "seeds": seeds, "instances": instances,
        "parameters": manifest["parameters"], "vehicle_parameters": manifest["vehicle_parameters"],
        "rerun_a": args.rerun_a,
    }
    if args.resume:
        saved = read_json(metadata_path)
        if saved != {**metadata, "solver_sha256": sha256(binary)}:
            raise ValueError("Resume requires the original experiment settings and executable")
    else:
        output.mkdir(parents=True)
        subprocess.run([compiler, "-std=c++17", "-O2", str(ROOT / "src_v4/v4_table8.cpp"), "-o", str(binary)],
                       env=env, check=True)
        write_json(metadata_path, {**metadata, "solver_sha256": sha256(binary)})
    all_rows, summaries = [], []
    for name in instances:
        rows = []
        for seed in seeds:
            for scenario in SCENARIOS:
                budget = budgets[name]
                run_dir = output / name / f"{scenario}-s{seed}"
                result_path = run_dir / "result.json"
                if scenario == "A" and not args.rerun_a:
                    result = cached_a(baseline, manifest, name, seed, budget)
                    run_dir.mkdir(parents=True, exist_ok=True)
                    write_json(result_path, result)
                else:
                    result = timed_run(binary, env, run_dir, baseline, manifest, name, scenario, seed, budget)
                row = run_row(result, budget, result.get("source", "timed"), result_path)
                rows.append(row)
        all_rows.extend(rows)
        summaries.extend(summarize(rows))
        write_csv(output / "runs.csv", all_rows)
        write_csv(output / "by_instance.csv", summaries)
        write_csv(output / "table8_summary.csv", list(summarize_table(summaries)))
        print(f"Completed {name}: {len(rows)} scenario/seed results", flush=True)


if __name__ == "__main__":
    main()
