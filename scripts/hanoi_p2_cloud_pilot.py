"""Compact data export and four-fold P2 optimization on GitHub."""
import argparse
import csv
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

import numpy as np
from scipy.sparse import csr_matrix, load_npz

from hanoi_pilot_selected import FLAGS, metrics, write_profile


ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / ".github/data/hanoi_p2_fold_pilot"
DAY_TYPES = ("monday", "saturday", "sunday")


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n")


def write_csv(path, rows):
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def tasks(instances):
    return [{"instance": instance, "day_type": day, "fold": fold,
             "key": f"P2-{instance}-{day}-fold{fold:02d}-10h-seed42"}
            for instance in instances for day in DAY_TYPES for fold in range(1, 5)]


def split_batch(all_tasks, batch):
    if len(all_tasks) != 240 or batch not in range(1, 6):
        raise ValueError("Expected 240 tasks and batch number 1 through 5")
    return all_tasks[(batch-1)*48:batch*48]


def select_tasks(all_tasks, batch=None, task_key=None):
    if task_key is not None:
        selected = [task for task in all_tasks if task["key"] == task_key]
        if len(selected) != 1:
            raise ValueError("Unknown or duplicate task key")
        return selected
    return split_batch(all_tasks, batch)


def build_bundle(args):
    from hanoi_build_path_profiles import daily_road_speeds
    root = args.data_root
    work = root / "path_network"
    directories = sorted((root / "instances").glob("set_*"))
    if len(directories) != 20:
        raise ValueError("Expected 20 source instances")
    metadata = json.loads((root / "synthetic_manifest.json").read_text())
    roads = daily_road_speeds(argparse.Namespace(work=work, days=root), metadata)
    dates = [item[1] for item in roads]
    # Shared road-length exposure reconstructs the exact full-precision OD profiles.
    exposure = load_npz(work / "path_road_lengths.npz").tocsr()
    with np.load(work / "path_index.npz") as index:
        pairs, distances = index["pairs"], index["distance_m"]
    road_speed = np.array([item[2] for item in roads])
    pair_lookup = {tuple(pair): k for k, pair in enumerate(pairs)}
    source_ids, input_hashes = [], {}
    largest_difference = 0.0
    args.bundle.mkdir(parents=True, exist_ok=True)
    for directory in directories:
        target = args.bundle / "instances" / directory.name
        target.mkdir(parents=True, exist_ok=True)
        with np.load(directory / "daily_path_speeds_full_precision.npz") as daily:
            ids = daily["source_ids"]
            assert list(daily["dates"].astype(str)) == dates
            source_ids.append(ids)
            ii, jj = np.where(~np.eye(101, dtype=bool))
            positions = [pair_lookup[(ids[i], ids[j])] for i, j in zip(ii, jj)]
            selected = exposure[positions]
            rebuilt = np.array([distances[positions, None] / (selected @ (1 / speed)) for speed in road_speed])
            original = daily["speed_kph"][:, ii, jj, :]
            np.testing.assert_allclose(rebuilt, original, rtol=0, atol=1e-10)
            largest_difference = max(largest_difference, float(np.max(np.abs(rebuilt-original))))
        input_hashes[directory.name] = {}
        for filename in ("instance.txt", "truck_distance_m.txt", "drone_distance_m.txt"):
            shutil.copy2(directory / filename, target / filename)
            input_hashes[directory.name][filename] = sha256(target / filename)
        input_hashes[directory.name]["original_daily_npz"] = sha256(directory / "daily_path_speeds_full_precision.npz")
    np.savez_compressed(args.bundle / "profiles.npz", exposure_data=exposure.data,
                        exposure_indices=exposure.indices, exposure_indptr=exposure.indptr,
                        exposure_shape=exposure.shape, pairs=pairs, distance_m=distances,
                        road_speed_kph=road_speed, dates=np.array(dates), hours=np.arange(6, 18),
                        source_ids=np.array(source_ids), instances=np.array([p.name for p in directories]))
    dump(args.bundle / "manifest.json", {"synthetic_data": True, "instances": [p.name for p in directories],
         "dates": dates, "hours": list(range(6, 18)), "input_hashes": input_hashes,
         "profiles_sha256": sha256(args.bundle / "profiles.npz"),
         "reconstruction_max_difference_kph": largest_difference,
         "description": "Exact fixed-path harmonic OD speeds reconstructed from shared road exposure; original instance/distance files copied unchanged"})
    print(f"Compact bundle verified for all 20 instances; largest speed difference={largest_difference}")


class Profiles:
    def __init__(self, bundle):
        self.bundle = bundle
        self.manifest = json.loads((bundle / "manifest.json").read_text())
        if sha256(bundle / "profiles.npz") != self.manifest["profiles_sha256"]:
            raise ValueError("Profile bundle hash mismatch")
        with np.load(bundle / "profiles.npz") as archive:
            self.data = {key: archive[key] for key in archive.files}
        self.instances = list(self.data["instances"].astype(str))
        self.lookup = {tuple(pair): k for k, pair in enumerate(self.data["pairs"])}
        self.exposure = csr_matrix((self.data["exposure_data"], self.data["exposure_indices"],
                                   self.data["exposure_indptr"]), shape=tuple(self.data["exposure_shape"]))

    @lru_cache(maxsize=2)
    def daily(self, instance):
        source = self.bundle / "instances" / instance
        for name in ("instance.txt", "truck_distance_m.txt", "drone_distance_m.txt"):
            if sha256(source / name) != self.manifest["input_hashes"][instance][name]:
                raise ValueError(f"Input hash mismatch: {source / name}")
        ids = self.data["source_ids"][self.instances.index(instance)]
        ii, jj = np.where(~np.eye(101, dtype=bool))
        positions = [self.lookup[(ids[i], ids[j])] for i, j in zip(ii, jj)]
        exposure = self.exposure[positions]
        daily = np.ones((12, 101, 101, 12))
        for k, road_speed in enumerate(self.data["road_speed_kph"]):
            daily[k, ii, jj] = self.data["distance_m"][positions, None] / (exposure @ (1 / road_speed))
        return daily


def run_batch(args):
    profiles = Profiles(args.bundle)
    selected = select_tasks(tasks(profiles.instances), args.batch, args.task_key)
    if args.limit:
        selected = selected[:args.limit]
    args.output.mkdir(parents=True, exist_ok=True)
    args.profiles.mkdir(parents=True, exist_ok=True)
    flags = [f"--iters={args.iterations}" if flag.startswith("--iters=") else flag for flag in FLAGS]
    dump(args.output / "batch_manifest.json", {"batch": args.batch, "expected_tasks": selected,
         "iterations": args.iterations, "flags": flags, "solver_sha256": sha256(args.binary),
         "source_sha256": sha256(ROOT / "src_v2/tabubu_HanoiTraffic_v2.cpp"),
         "profiles_sha256": profiles.manifest["profiles_sha256"], "start_hour": 10,
         "seed": 42, "workers": 1, "service_time": "unchanged 60 seconds from instance"})
    rows, failures = [], []
    for task in selected:
        key = task["key"]
        instance, day, fold = task["instance"], task["day_type"], task["fold"]
        group = DAY_TYPES.index(day)
        heldout = group*4 + fold-1
        same = [k for k in range(group*4, group*4+4) if k != heldout]
        work = args.output / key
        work.mkdir(exist_ok=True)
        generated = args.profiles / key
        generated.mkdir(exist_ok=True)
        daily = profiles.daily(instance)
        p2 = 1 / np.mean(1 / daily[same], axis=0)
        write_profile(generated / "P2.txt", p2, profiles.data["hours"])
        write_profile(generated / "test.txt", daily[heldout], profiles.data["hours"])
        data = args.bundle / "instances" / instance
        common = [str(args.binary.resolve()), str(data / "instance.txt"), *flags, "--start-hour=10",
                  f"--truck-distance-file={data / 'truck_distance_m.txt'}",
                  f"--drone-distance-file={data / 'drone_distance_m.txt'}"]
        optimize = common + [f"--truck-vijl-file={generated / 'P2.txt'}"]
        replay_dir = work / "replay"
        replay_dir.mkdir(exist_ok=True)
        replay = common + [f"--truck-vijl-file={generated / 'test.txt'}",
                           f"--evaluate-solution={work / 'output_solution_best.txt'}"]
        dump(work / "task.json", {**task, "training_dates": list(profiles.data["dates"][same].astype(str)),
             "test_date": str(profiles.data["dates"][heldout]), "optimize": optimize, "replay": replay,
             "P2_profile_sha256": sha256(generated / "P2.txt"), "test_profile_sha256": sha256(generated / "test.txt")})
        print(f"START {key}", flush=True)
        try:
            with (work / "solver.log").open("w") as log:
                subprocess.run(optimize, cwd=work, stdout=log, stderr=subprocess.STDOUT, check=True)
            with (replay_dir / "solver.log").open("w") as log:
                result = subprocess.run(replay, cwd=replay_dir, stdout=log, stderr=subprocess.STDOUT)
            if result.returncode not in (0, 2):
                raise RuntimeError(f"Replay exit code {result.returncode}")
            row = {**task, "seed": 42, "start_hour": 10, "iterations": args.iterations,
                   "test_date": str(profiles.data["dates"][heldout]),
                   **metrics(work / "output_solution_best.txt", 10)}
            row.update({f"test_{k}": v for k, v in metrics(replay_dir / "output_solution_evaluated.txt", 10).items()})
            rows.append(row)
            dump(work / "result.json", row)
            write_csv(args.output / "summary.csv", rows)
            print(f"DONE {key}: plan {row['finish_hour']:.4f}h, replay {row['test_finish_hour']:.4f}h", flush=True)
        except Exception as error:
            failures.append({**task, "error": str(error)})
            dump(work / "error.json", failures[-1])
            print(f"ERROR {key}: {error}", flush=True)
        dump(args.output / "status.json", {"completed": len(rows), "total": len(selected), "failures": failures})
    if failures or len(rows) != len(selected):
        raise SystemExit("Incomplete batch; inspect uploaded artifacts")


def aggregate(args):
    manifest = json.loads((args.bundle / "manifest.json").read_text())
    expected_tasks = {t["key"]: t for t in tasks(manifest["instances"])}
    expected = set(expected_tasks)
    rows = []
    for path in sorted(args.input.glob("**/result.json")):
        rows.append(json.loads(path.read_text()))
    keys = [r["key"] for r in rows]
    missing = sorted(expected-set(keys))
    duplicate_or_extra = len(keys) != len(set(keys)) or bool(set(keys)-expected)
    configuration_valid = all(r.get("iterations") == 9000 and r.get("seed") == 42
        and r.get("start_hour") == 10 and r["key"] in expected_tasks
        and all(r.get(k) == v for k, v in expected_tasks[r["key"]].items()) for r in rows)
    valid = [r for r in rows if r["feasible"] and r["test_feasible"]]
    late = [r for r in rows if r["finish_hour"] > 17 or r["test_finish_hour"] > 17]
    report = {"expected": 240, "completed": len(rows), "missing": missing,
              "duplicate_or_extra": duplicate_or_extra, "feasible_plan_and_replay": len(valid),
              "configuration_valid": configuration_valid,
              "cases_exceeding_17h": [{"key": r["key"], "finish_hour": r["finish_hour"],
                                       "test_finish_hour": r["test_finish_hour"]} for r in late],
              "latest_predicted_finish_hour": max((r["finish_hour"] for r in valid), default=None),
              "latest_replayed_finish_hour": max((r["test_finish_hour"] for r in valid), default=None),
              "all_240_feasible_and_finish_by_17h": len(valid)==240 and configuration_valid and not missing and not duplicate_or_extra and not late,
              "scope": "one seed (42), four folds, 20 instances, three day types; not a bound for other seeds/policies"}
    args.output.mkdir(parents=True, exist_ok=True)
    dump(args.output / "report.json", report)
    if rows:
        rows.sort(key=lambda r: r["key"])
        write_csv(args.output / "all_runs.csv", rows)
    from openpyxl import Workbook
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "Runs"
    if rows:
        worksheet.append(list(rows[0]))
        for row in rows:
            worksheet.append(list(row.values()))
    summary = workbook.create_sheet("Summary")
    for key, value in report.items():
        summary.append([key, json.dumps(value) if isinstance(value, (dict, list)) else value])
    workbook.save(args.output / "P2_4fold_start10_results.xlsx")
    print(json.dumps(report, indent=2))
    if missing or duplicate_or_extra or not configuration_valid:
        raise SystemExit("Coverage or configuration validation failed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, default=BUNDLE)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("matrix")
    build = commands.add_parser("build-bundle")
    build.add_argument("--data-root", type=Path, required=True)
    batch = commands.add_parser("batch")
    selection = batch.add_mutually_exclusive_group(required=True)
    selection.add_argument("--batch", type=int)
    selection.add_argument("--task-key")
    batch.add_argument("--binary", type=Path, required=True)
    batch.add_argument("--output", type=Path, required=True)
    batch.add_argument("--profiles", type=Path, required=True)
    batch.add_argument("--iterations", type=int, default=9000)
    batch.add_argument("--limit", type=int)
    collect = commands.add_parser("aggregate")
    collect.add_argument("--input", type=Path, required=True)
    collect.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "matrix":
        manifest = json.loads((args.bundle / "manifest.json").read_text())
        matrix = {"include": [{"task_key": task["key"]} for task in tasks(manifest["instances"])]}
        if len(matrix["include"]) != 240:
            raise ValueError("Expected exactly 240 optimization jobs")
        print(json.dumps(matrix, separators=(",", ":")))
        return
    for key in ("bundle", "output", "input", "profiles", "data_root"):
        if hasattr(args, key):
            setattr(args, key, getattr(args, key).resolve())
    {"build-bundle": build_bundle, "batch": run_batch, "aggregate": aggregate}[args.command](args)


if __name__ == "__main__":
    main()
