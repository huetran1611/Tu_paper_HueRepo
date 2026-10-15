"""Run a reproducible set_03 Monday fold-1 P2 pilot with selected tuning."""
import csv
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import sys

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "instance_hanoi/datasets/hanoi_traffic/Data_9.2026/instances/set_03"
OUT = ROOT / "instance_hanoi/datasets/hanoi_traffic/Data_9.2026/pilot_selected/set_03_monday_fold1_seed42"
FLAGS = [
    "--attempts=1", "--iters=9000", "--no-improve=9001", "--seed=42",
    "--trucks=1", "--drones=1", "--truck-capacity=40", "--drone-capacity=2.27",
    "--c-seg=2", "--c-tau=2", "--h-mode=1", "--h-div=4",
    "--gamma1=0.70", "--gamma2=0.30", "--gamma3=0.10", "--gamma4=0.30",
    "--sa-t0=0.005", "--sa-alpha=0.9982", "--penalty-kappa=1",
    "--target-violation=0.005", "--destroy-rate=0.20", "--cross-cluster-penalty=1",
]


def write_profile(path, speed, hours):
    with path.open("w") as stream:
        for k, hour in enumerate(hours):
            for i in range(speed.shape[0]):
                for j in range(speed.shape[1]):
                    stream.write(f"{i} {j} {hour} {speed[i, j, k]:.12g}\n")


def metrics(path, start):
    text = path.read_text()
    duration = float(re.search(r"Improved solution cost: ([\d.eE+-]+)", text)[1])
    return {"makespan_sec": duration, "finish_hour": start + duration / 3600,
            "feasible": "Final solution feasibility: FEASIBLE" in text}


def main():
    binary = Path(sys.argv[1]).resolve()
    OUT.mkdir(parents=True, exist_ok=True)
    with np.load(DATA / "training_profiles_full_precision.npz") as profiles:
        policies = list(profiles["policies"].astype(str))
        write_profile(OUT / "P2_monday.txt", profiles["speed_kph"][0, policies.index("P2_monday")], profiles["hours"])
    with np.load(DATA / "daily_path_speeds_full_precision.npz") as profiles:
        dates = list(profiles["dates"].astype(str))
        write_profile(OUT / "test_2026-09-07.txt", profiles["speed_kph"][dates.index("2026-09-07")], profiles["hours"])
    source = ROOT / "src_v2/tabubu_HanoiTraffic_v2.cpp"
    manifest = {"dataset": "set_03", "policy": "P2_monday", "fold": 1, "seed": 42,
                "training_dates": ["2026-09-14", "2026-09-21", "2026-09-28"],
                "test_date": "2026-09-07", "flags": FLAGS,
                "service_time": "unchanged instance values (60 seconds)",
                "segment_formula": "ceil(c_seg * n)", "tenure_formula": "max(20, int(c_tau * sqrt(n)))",
                "temperature_units": "raw penalized objective units, as implemented by solver",
                "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(), "commands": []}
    common = [str(binary), str(DATA / "instance.txt"),
              f"--truck-distance-file={DATA / 'truck_distance_m.txt'}",
              f"--drone-distance-file={DATA / 'drone_distance_m.txt'}"]
    rows = []
    for hour in (8, 9, 10):
        work = OUT / f"start_{hour:02d}"
        work.mkdir(exist_ok=True)
        command = common + FLAGS + [f"--start-hour={hour}", f"--truck-vijl-file={OUT / 'P2_monday.txt'}"]
        manifest["commands"].append(command)
        (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        print(f"Running P2 Monday fold 1, start {hour:02d}:00", flush=True)
        with (work / "solver.log").open("w") as log:
            subprocess.run(command, cwd=work, stdout=log, stderr=subprocess.STDOUT, check=True)
        row = {"start_hour": hour, **metrics(work / "output_solution_best.txt", hour)}
        replay = work / "replay"
        replay.mkdir(exist_ok=True)
        command = common + FLAGS + [f"--start-hour={hour}", f"--truck-vijl-file={OUT / 'test_2026-09-07.txt'}",
                                    f"--evaluate-solution={work / 'output_solution_best.txt'}"]
        manifest["commands"].append(command)
        with (replay / "solver.log").open("w") as log:
            result = subprocess.run(command, cwd=replay, stdout=log, stderr=subprocess.STDOUT)
        if result.returncode not in (0, 2):
            raise RuntimeError(f"Replay failed; see {replay}")
        row.update({f"test_{key}": value for key, value in metrics(replay / "output_solution_evaluated.txt", hour).items()})
        rows.append(row)
        with (OUT / "summary.csv").open("w") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(row))
            writer.writeheader()
            writer.writerows(rows)
        print(json.dumps(row), flush=True)
    feasible = [r["finish_hour"] for r in rows if r["feasible"]]
    manifest["results"] = rows
    manifest["pilot_only_window_end_hour"] = math.ceil(max(feasible)) + 1 if feasible else None
    manifest["window_warning"] = "Pilot only: not a bound for other instances, folds, seeds, or policies. Test replay is diagnostic, not used to select training window."
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    main()
