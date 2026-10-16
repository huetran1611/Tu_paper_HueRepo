import argparse
import contextlib
import copy
import io
from itertools import product
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import numpy as np
import yaml

import hanoi_full_experiment as full


class FullExperimentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name)
        cls.capsule = cls.root / "capsule"
        cls.capsule.mkdir()
        shutil.copytree(full.base.BUNDLE, cls.capsule / "bundle")
        (cls.capsule / "solver").write_text("test binary\n")
        cls.bundle = json.loads((cls.capsule / "bundle/manifest.json").read_text())
        cls.all_tasks = full.tasks(cls.bundle["instances"])
        cls.manifest = {"experiment_id": "test", "commit": "a" * 40,
                        "created_at": "2026-10-16T01:00:00+00:00",
                        "binary_sha256": full.sha256(cls.capsule / "solver"),
                        "bundle_manifest_sha256": full.sha256(cls.capsule / "bundle/manifest.json"),
                        "code_sha256": {name: full.sha256(full.ROOT / name) for name in full.CODE},
                        "tasks": cls.all_tasks}
        cls.manifest["protocol_id"] = full.digest(cls.manifest)
        full.base.dump(cls.capsule / "experiment.json", cls.manifest)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def record(self, task, feasible=True, pi_feasible=True):
        metrics = {"iterations": 9000, "feasible": feasible, "makespan_sec": 100., "finish_hour": 8 + 100 / 3600,
                   "freshness_failure": not feasible, "n_violated": 0 if feasible else 1,
                   "max_excess_pct": 0., "max_excess_min": 0., "failure_gt5": False,
                   "max_truck_trip_load_kg": 39., "max_drone_trip_load_kg": 2.}
        _, heldout, train = full.base.profiles_for(
            np.ones((12, 1, 1, 12)), np.arange(6, 18),
            task["fold"], task["policy"], task["day_type"])
        dates = self.bundle["dates"]
        record = {"protocol_id": self.manifest["protocol_id"], "task": task,
                  "planning": metrics, "training_dates": [dates[k] for k in train], "cases": []}
        if task["policy"] == "PI":
            record["test_date"] = dates[heldout[full.base.DAYS.index(task["day_type"])]]
            if not pi_feasible:
                metrics.update(feasible=False, freshness_failure=True, n_violated=1)
        else:
            for day in (full.base.DAYS if task["day_type"] == "shared" else (task["day_type"],)):
                replay = {**metrics, "makespan_sec": 80. if task["buffer_pct"] == 0 else 90.,
                          "feasible": False, "freshness_failure": True, "n_violated": 1,
                          "max_excess_pct": 10., "max_excess_min": 6., "failure_gt5": True}
                record["cases"].append({"day_type": day, "test_date": dates[heldout[full.base.DAYS.index(day)]],
                                        "replay": replay if feasible else None})
        return record

    def expected_manifest(self):
        return {**self.manifest, "_capsule": self.capsule}

    def test_counts_policy_batches_and_no_duplicate_tasks(self):
        self.assertEqual(len(self.all_tasks), 10400)
        self.assertEqual(len({t["key"] for t in self.all_tasks}), 10400)
        for policy, batches, tail, count in (("P0", 7, 100, 1600), ("monday", 9, 134, 2134),
                                              ("saturday", 9, 134, 2134), ("sunday", 9, 132, 2132), ("PI", 10, 150, 2400)):
            selected = [full.batch_tasks(self.all_tasks, batch, policy) for batch in range(1, batches + 1)]
            self.assertEqual(sum(map(len, selected)), count)
            self.assertEqual(len(selected[-1]), tail)
            self.assertTrue(all(len(batch) <= 250 for batch in selected))
            with self.assertRaises(ValueError):
                full.batch_tasks(self.all_tasks, batches + 1, policy)
        self.assertEqual(sum(3 if t["policy"] in ("P0", "P1") else t["policy"] == "P2" for t in self.all_tasks), 14400)
        self.assertEqual({t["buffer_pct"] for t in self.all_tasks if t["policy"] == "PI"}, {0})
        for task in self.all_tasks:
            if task["policy"] == "P2":
                self.assertEqual(task["owner"], task["day_type"])

    def test_workflow_shell_line_continuations(self):
        paths = sorted((full.ROOT / ".github/workflows").glob("hanoi-full-*.yml"))
        self.assertEqual(len(paths), 5)
        for path in paths:
            self.assertFalse(any(line.endswith("\\\\") for line in path.read_text().splitlines()), path)

    def test_capsule_source_and_binary_integrity(self):
        self.assertEqual(full.load_capsule(self.capsule)["protocol_id"], self.manifest["protocol_id"])
        with patch.object(full, "sha256", return_value="changed"):
            with self.assertRaises(ValueError):
                full.load_capsule(self.capsule)

    def test_exact_case_universe_and_yaml_batch_options(self):
        expected = set()
        for instance, fold, seed, policy in product(self.bundle["instances"], range(1, 5), full.base.SEEDS, ("P0", "P1", "P2", "PI")):
            days = ("shared",) if policy in ("P0", "P1") else full.base.DAYS
            buffers = (0,) if policy == "PI" else (0, 10)
            expected.update((instance, fold, seed, policy, day, buffer) for day, buffer in product(days, buffers))
        actual = {(t["instance"], t["fold"], t["seed"], t["policy"], t["day_type"], t["buffer_pct"]) for t in self.all_tasks}
        self.assertEqual(actual, expected)
        replay_keys = []
        for task in self.all_tasks:
            if task["policy"] == "PI":
                continue
            for day in (full.base.DAYS if task["day_type"] == "shared" else (task["day_type"],)):
                replay_keys.append((task["instance"], task["fold"], task["seed"], task["policy"], day, task["buffer_pct"]))
        expected_replays = set(product(self.bundle["instances"], range(1, 5), full.base.SEEDS, ("P0", "P1", "P2"), full.base.DAYS, (0, 10)))
        self.assertEqual(set(replay_keys), expected_replays)
        self.assertEqual(len(replay_keys), len(expected_replays))
        assigned = []
        for group in full.GROUPS:
            path = full.ROOT / f".github/workflows/hanoi-full-{group.lower()}.yml"
            workflow = yaml.load(path.read_text(), Loader=yaml.BaseLoader)
            batches = list(map(int, workflow["on"]["workflow_dispatch"]["inputs"]["batch"]["options"]))
            for batch in batches:
                assigned.extend(t["key"] for t in full.batch_tasks(self.all_tasks, batch, group))
            with self.assertRaises(ValueError):
                full.batch_tasks(self.all_tasks, batches[-1] + 1, group)
        self.assertEqual(len(assigned), len(set(assigned)))
        self.assertEqual(set(assigned), {t["key"] for t in self.all_tasks})

    def test_bootstrap_writes_a_real_output_newline(self):
        path = full.ROOT / ".github/actions/hanoi-capsule-bootstrap/action.yml"
        action = yaml.load(path.read_text(), Loader=yaml.BaseLoader)
        script = next(s["run"] for s in action["runs"]["steps"] if s.get("id") == "frozen")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            full.base.dump(root / "hanoi-capsule/experiment.json", {"experiment_id": "123", "commit": "a" * 40})
            output = root / "github_output"
            subprocess.run(["bash", "-e", "-c", script], check=True,
                           env={**os.environ, "RUNNER_TEMP": directory, "SETUP_RUN_ID": "123", "GITHUB_OUTPUT": str(output)})
            self.assertEqual(output.read_bytes(), ("commit=" + "a" * 40 + "\n").encode())

    def test_all_fold_profiles_exclude_test_dates(self):
        hours = np.arange(6, 18)
        daily = np.random.default_rng(10).uniform(10, 40, size=(12, 1, 1, 12))
        for fold, day in product(range(1, 5), full.base.DAYS):
            changed = daily.copy()
            heldout = [fold - 1 + 4 * k for k in range(3)]
            changed[heldout] = 1000
            for policy in ("P0", "P1", "P2"):
                a, _, train = full.base.profiles_for(daily, hours, fold, policy, day)
                b, _, _ = full.base.profiles_for(changed, hours, fold, policy, day)
                np.testing.assert_array_equal(a, b)
                self.assertFalse(set(train) & set(heldout))
                self.assertEqual(len(train), 3 if policy == "P2" else 9)
                if policy == "P0":
                    expected = 81 / (1 / daily[train, 0, 0, 2:11]).sum()
                    np.testing.assert_allclose(a, expected)

    def test_dates_train_and_nan_rejected(self):
        task = self.all_tasks[0]
        record = self.record(task)
        full.validate_record(record, task, self.expected_manifest())
        changed = copy.deepcopy(record)
        changed["cases"][0]["test_date"] = "2026-09-14"
        with self.assertRaises(ValueError):
            full.validate_record(changed, task, self.expected_manifest())
        record["planning"]["makespan_sec"] = float("nan")
        with self.assertRaises(ValueError):
            full.validate_record(record, task, self.expected_manifest())

    def test_identical_retry_deduplicated_but_changed_retry_conflicts(self):
        task = self.all_tasks[0]
        record = self.record(task)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("a", "b"):
                full.base.dump(root / name / "task_result.json", record)
            records, report = full.collect_records(root, self.expected_manifest(), {task["key"]: task})
            self.assertEqual(len(records), 1)
            self.assertEqual(report["identical_retries"], [task["key"]])
            record["cases"][0]["replay"]["makespan_sec"] = 81
            full.base.dump(root / "b/task_result.json", record)
            _, report = full.collect_records(root, self.expected_manifest(), {task["key"]: task})
            self.assertEqual(report["conflicting_retries"], [task["key"]])

    def test_missing_does_not_publish_latex_or_count_as_np(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = argparse.Namespace(capsule=self.capsule, input=root / "input", output=root / "output")
            args.input.mkdir()
            with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit):
                full.aggregate(args)
            self.assertFalse((args.output / "hanoi_information_table.tex").exists())
            import csv
            with (args.output / "heldout_summary.csv").open() as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(rows[0]["NP_pct"], "")

    def test_planning_np_is_a_completed_record_without_replay(self):
        task = self.all_tasks[0]
        record = self.record(task, feasible=False)
        full.validate_record(record, task, self.expected_manifest())
        self.assertEqual(len(record["cases"]), 3)
        self.assertTrue(all(case["replay"] is None for case in record["cases"]))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            full.base.dump(root / "task_result.json", record)
            records, report = full.collect_records(root, self.expected_manifest(), {task["key"]: task})
            self.assertEqual(len(records), 1)
            self.assertFalse(report["missing"])

    def test_missing_feasible_pi_keeps_rf_but_no_gap(self):
        task = self.all_tasks[0]
        pi = next(t for t in self.all_tasks if t["policy"] == "PI" and t["instance"] == task["instance"]
                  and t["fold"] == task["fold"] and t["seed"] == task["seed"] and t["day_type"] == "monday")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            full.base.dump(root / "input/plan/task_result.json", self.record(task))
            full.base.dump(root / "input/pi/task_result.json", self.record(pi, pi_feasible=False))
            args = argparse.Namespace(capsule=self.capsule, input=root / "input", output=root / "output")
            with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit):
                full.aggregate(args)
            import csv
            with (args.output / "all_replay_cases.csv").open() as stream:
                cases = list(csv.DictReader(stream))
            self.assertTrue(all(row["gap_pct"] == "" for row in cases))
            self.assertTrue(all(row["freshness_failure"] == "True" for row in cases))

    def test_fetch_reads_all_five_workflows_and_only_this_experiment(self):
        endpoints = []
        def api(command):
            endpoint = command[-1]
            endpoints.append(endpoint)
            if "/workflows/hanoi-full-p0.yml/runs" in endpoint:
                return json.dumps({"workflow_runs": [{"id": 11}]}).encode()
            if "/workflows/" in endpoint:
                return b'{"workflow_runs":[]}'
            return json.dumps({"artifacts": [
                {"id": 1, "name": "hanoi-full-test-batch-P0-1-run-11-a1", "expired": False},
                {"id": 2, "name": "hanoi-full-other-batch-P0-1", "expired": False},
                {"id": 3, "name": "hanoi-full-test-batch-P0-2", "expired": True}]}).encode()
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as zipped:
            zipped.writestr("record/task_result.json", "{}")
        def download(command, stdout, check):
            stdout.write(archive.getvalue())
        with tempfile.TemporaryDirectory() as directory, patch.object(full.subprocess, "check_output", side_effect=api), patch.object(full.subprocess, "run", side_effect=download):
            output = Path(directory)
            full.fetch_batches(argparse.Namespace(capsule=self.capsule, repo="test/repo", output=output))
            self.assertTrue((output / "1/record/task_result.json").exists())
            self.assertFalse((output / "2").exists())
            self.assertEqual(sum("/workflows/" in value for value in endpoints), 5)

    def test_runner_uses_buffered_plan_and_original_replay_limits(self):
        task = next(t for t in self.all_tasks if t["policy"] == "P0" and t["buffer_pct"] == 10)
        seen_limits = []

        def solver(binary, instance, data, profile, work, seed, solution=None):
            limits = np.loadtxt(instance, skiprows=6)[:, -1]
            seen_limits.append(set(limits))
            self.assertEqual(seed, task["seed"])
            self.assertTrue(profile.exists())
            metrics = self.record(task)["planning"].copy()
            return metrics

        with tempfile.TemporaryDirectory() as directory, patch.object(full.base, "run_solver", side_effect=solver):
            output = Path(directory) / "result"
            full.run_task(argparse.Namespace(capsule=self.capsule, batch=1, policy="P0", task_key=task["key"], output=output))
            record = json.loads((output / "task_result.json").read_text())
            self.assertEqual(seen_limits, [{3240.}, {3600.}, {3600.}, {3600.}])
            self.assertEqual(len(record["cases"]), 3)
            full.validate_record(record, task, self.expected_manifest())

    def test_full_aggregation_retains_failures_negative_gaps_and_buffer_pairing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "input"
            for task in self.all_tasks:
                full.base.dump(source / task["key"] / "task_result.json", self.record(task))
            args = argparse.Namespace(capsule=self.capsule, input=source, output=root / "output")
            with contextlib.redirect_stdout(io.StringIO()):
                full.aggregate(args)
            coverage = json.loads((args.output / "coverage.json").read_text())
            self.assertTrue(coverage["complete"])
            self.assertEqual(coverage["actual_replays"], 14400)
            self.assertEqual(coverage["PI_references"], 2400)
            text = (args.output / "hanoi_information_table.tex").read_text()
            self.assertIn("without a buffer", text)
            self.assertIn("tab:hanoi-information", text)
            buffered_text = (args.output / "hanoi_buffer10_table.tex").read_text()
            self.assertIn(r"10\%", buffered_text)
            self.assertNotIn(r"10\\%", buffered_text)
            import csv
            with (args.output / "heldout_summary.csv").open() as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(len(rows), 18)
            self.assertEqual(float(rows[0]["RF_pct"]), 100)
            self.assertEqual(float(rows[0]["gap_pct"]), -20)
            with (args.output / "buffer_comparison.csv").open() as stream:
                comparisons = list(csv.DictReader(stream))
            self.assertEqual(len(comparisons), 9)
            self.assertEqual(int(comparisons[0]["common_returned_cases"]), 800)
            self.assertEqual(float(comparisons[0]["common_duration_change_pct"]), 12.5)
            # A conflict found in a packed batch must not be lost in final aggregation.
            full.base.dump(source / "batch_coverage.json", {"conflicting_retries": ["bad"], "invalid": []})
            with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit):
                full.aggregate(argparse.Namespace(capsule=self.capsule, input=source, output=root / "invalid"))


if __name__ == "__main__":
    unittest.main()
