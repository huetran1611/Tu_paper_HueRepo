import argparse
from collections import Counter
import contextlib
import copy
import io
from itertools import product
import json
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

import numpy as np
import yaml

import hanoi_buffer20_experiment as extension

full = extension.full


class Buffer20Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name)
        cls.capsule = cls.root / "capsule"
        cls.capsule.mkdir()
        bundles = [full.ROOT / ".github/Data_12day_9.2026/hanoi_protocol_buffer10",
                   full.ROOT / ".github/data/hanoi_protocol_buffer10"]
        bundle = next(path for path in bundles if path.exists())
        shutil.copytree(bundle, cls.capsule / "bundle")
        cls.bundle = json.loads((bundle / "manifest.json").read_text())
        (cls.capsule / "solver").write_text("test solver")
        parent = {"experiment_id": "123", "commit": "a" * 40, "created_at": "2026-10-16T00:00:00+00:00",
            "buffers_pct": [0, 10], "PI_buffer_pct": 0, "start_hour": 8, "p0_window": [8, 17],
            "binary_sha256": full.sha256(cls.capsule / "solver"),
            "bundle_manifest_sha256": full.sha256(cls.capsule / "bundle/manifest.json"),
            "code_sha256": {name: full.sha256(full.ROOT / name) for name in full.CODE},
            "tasks": full.tasks(cls.bundle["instances"])}
        parent["protocol_id"] = full.digest(parent)
        full.base.dump(cls.capsule / "experiment.json", parent)
        cls.parent, cls.extension_id = extension.context(cls.capsule)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def record(self, task, feasible=True):
        metrics = {"iterations": 9000, "feasible": feasible, "makespan_sec": 100., "finish_hour": 8 + 100 / 3600,
            "freshness_failure": not feasible, "n_violated": 0 if feasible else 1,
            "max_excess_pct": 0. if feasible else 1., "max_excess_min": 0. if feasible else .6,
            "failure_gt5": False, "max_truck_trip_load_kg": 39., "max_drone_trip_load_kg": 2.}
        _, heldout, train = full.base.profiles_for(np.ones((12, 1, 1, 12)), np.arange(6, 18),
            task["fold"], task["policy"], task["day_type"])
        dates = self.bundle["dates"]
        record = {"protocol_id": self.parent["protocol_id"], "task": task, "planning": metrics,
                  "training_dates": [dates[k] for k in train], "cases": []}
        if task["buffer_pct"] == 20:
            record["extension_id"] = self.extension_id
        if task["policy"] == "PI":
            record["test_date"] = dates[heldout[full.base.DAYS.index(task["day_type"])]]
        else:
            for day in (full.base.DAYS if task["day_type"] == "shared" else (task["day_type"],)):
                duration = {0: 80., 10: 90., 20: 95.}[task["buffer_pct"]]
                replay = {**metrics, "makespan_sec": duration, "finish_hour": 8 + duration / 3600,
                          "freshness_failure": True, "n_violated": 1, "feasible": False,
                          "max_excess_pct": 10., "max_excess_min": 6., "failure_gt5": True}
                record["cases"].append({"day_type": day, "test_date": dates[heldout[full.base.DAYS.index(day)]],
                                        "replay": replay if feasible else None})
        return record

    def test_exact_task_universe_and_workflow_batches(self):
        selected = extension.tasks(self.parent)
        actual = {(t["instance"], t["fold"], t["seed"], t["policy"], t["day_type"], t["buffer_pct"]) for t in selected}
        expected = set()
        for policy in ("P0", "P1", "P2"):
            days = full.base.DAYS if policy == "P2" else ("shared",)
            expected.update(product(self.bundle["instances"], range(1, 5), full.base.SEEDS, (policy,), days, (20,)))
        self.assertEqual(actual, expected)
        self.assertEqual(Counter(t["policy"] for t in selected), {"P0": 800, "P1": 800, "P2": 2400})
        self.assertTrue(all(t["key"].endswith("-b20") for t in selected))
        path = full.ROOT / ".github/workflows/hanoi-buffer20.yml"
        workflow = yaml.load(path.read_text(), Loader=yaml.BaseLoader)
        batches = list(map(int, workflow["on"]["workflow_dispatch"]["inputs"]["batch"]["options"]))
        assigned = [task for batch in batches for task in extension.batch_tasks(self.parent, batch)]
        self.assertEqual(len(assigned), len({t["key"] for t in assigned}))
        self.assertEqual({t["key"] for t in assigned}, {t["key"] for t in selected})
        self.assertEqual(sum(3 if t["day_type"] == "shared" else 1 for t in assigned), 7200)
        self.assertTrue(all(len(extension.batch_tasks(self.parent, b)) == 250 for b in batches))
        for batch in (0, 17):
            with self.assertRaises(ValueError):
                extension.batch_tasks(self.parent, batch)
        self.assertEqual({t["buffer_pct"] for t in self.parent["tasks"]}, {0, 10})

    def test_capsule_source_verification_is_not_bypassed(self):
        with patch.object(full, "sha256", return_value="changed"):
            with self.assertRaises(ValueError):
                extension.context(self.capsule)

    def test_planning_48_minutes_replay_original_60_all_policies(self):
        for policy in ("P0", "P1", "P2"):
            task = next(t for t in extension.tasks(self.parent) if t["policy"] == policy)
            calls = []
            def solver(binary, instance, data, profile, work, seed, solution=None):
                limits = {float(line.split()[-1]) for line in instance.read_text().splitlines()[6:]}
                calls.append((limits, solution))
                return self.record(task)["planning"]
            store = SimpleNamespace(bundle=self.capsule / "bundle", daily=lambda name: np.ones((12, 1, 1, 12)),
                data={"hours": np.arange(6, 18), "dates": np.array(self.bundle["dates"])})
            with tempfile.TemporaryDirectory() as tmp, patch.object(full, "Profiles", return_value=store), patch.object(full.base, "run_solver", side_effect=solver):
                args = argparse.Namespace(capsule=self.capsule, batch=1, task_key=task["key"], output=Path(tmp) / "task")
                extension.run_task(args, self.parent, self.extension_id)
                self.assertTrue((args.output / "task_result.json").exists())
            self.assertEqual(calls[0], ({2880.}, None))
            self.assertEqual(len(calls), 2 if policy == "P2" else 4)
            self.assertTrue(all(limit == {3600.} and solution is not None for limit, solution in calls[1:]))

    def test_missing_record_is_not_planning_non_success(self):
        task = extension.tasks(self.parent)[0]
        with tempfile.TemporaryDirectory() as tmp:
            expected = {task["key"]: task}
            records, report = extension.collect(Path(tmp), self.parent, expected, self.extension_id)
            self.assertEqual(report["missing"], [task["key"]])
            full.base.dump(Path(tmp) / "task/task_result.json", self.record(task, feasible=False))
            records, report = extension.collect(Path(tmp), self.parent, expected, self.extension_id)
            self.assertFalse(report["missing"])
            self.assertFalse(records[task["key"]]["planning"]["feasible"])
            self.assertTrue(all(c["replay"] is None for c in records[task["key"]]["cases"]))

    def test_retry_deduplication_conflicts_and_extension_identity(self):
        task = extension.tasks(self.parent)[0]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record = self.record(task)
            full.base.dump(root / "first/task_result.json", record)
            full.base.dump(root / "retry/task_result.json", record)
            expected = {task["key"]: task}
            records, report = extension.collect(root, self.parent, expected, self.extension_id)
            self.assertEqual(len(records), 1)
            self.assertEqual(report["identical_retries"], [task["key"]])
            changed = copy.deepcopy(record)
            changed["planning"]["makespan_sec"] += 1
            full.base.dump(root / "retry/task_result.json", changed)
            self.assertEqual(extension.collect(root, self.parent, expected, self.extension_id)[1]["conflicting_retries"], [task["key"]])
            records, report = extension.collect(root / "first", self.parent, expected, "wrong")
            self.assertFalse(records)
            self.assertTrue(report["invalid"])
            self.assertEqual(report["missing"], [task["key"]])

    def test_combined_aggregation_reuses_PI_and_retains_failures_negative_gaps(self):
        baseline = {t["key"]: self.record(t) for t in self.parent["tasks"]}
        extra = {t["key"]: self.record(t) for t in extension.tasks(self.parent)}
        clean = {"missing": [], "invalid": [], "conflicting_retries": [], "identical_retries": []}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            args = argparse.Namespace(input=root / "input", output=root / "output")
            with patch.object(extension, "collect", side_effect=[(baseline, clean), (extra, clean)]), contextlib.redirect_stdout(io.StringIO()):
                extension.aggregate(args, self.parent, self.extension_id)
            coverage = json.loads((args.output / "coverage.json").read_text())
            self.assertTrue(coverage["complete"])
            self.assertEqual(coverage["planning_records"], 14400)
            self.assertEqual(coverage["scheduled_replays"], 21600)
            self.assertEqual(coverage["PI_references"], 2400)
            import csv
            with (args.output / "heldout_summary.csv").open() as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(len(rows), 27)
            self.assertTrue(all(float(row["RF_pct"]) == 100 for row in rows))
            self.assertTrue(all(float(row["gap_pct"]) < 0 for row in rows))
            self.assertTrue(all(float(row["NP_pct"]) == 0 for row in rows))
            self.assertTrue((args.output / "hanoi_all_buffers_table.tex").exists())
            extra.pop(next(iter(extra)))
            incomplete = {**clean, "missing": ["a task"]}
            args.output = root / "incomplete"
            with patch.object(extension, "collect", side_effect=[(baseline, clean), (extra, incomplete)]):
                with self.assertRaises(SystemExit):
                    extension.aggregate(args, self.parent, self.extension_id)
            self.assertFalse((args.output / "hanoi_all_buffers_table.tex").exists())

    def test_bootstrap_preserves_runner_before_frozen_checkout(self):
        action = yaml.load((full.ROOT / ".github/actions/hanoi-buffer20-bootstrap/action.yml").read_text(), Loader=yaml.BaseLoader)
        steps = action["runs"]["steps"]
        self.assertIn("cp scripts/hanoi_buffer20_experiment.py", steps[0]["run"])
        self.assertIn("hanoi-buffer20-bootstrap.yml", steps[0]["run"])
        self.assertEqual(steps[1]["uses"], "./.github/actions/hanoi-capsule-bootstrap")
        self.assertEqual(steps[2]["if"], "always()")
        self.assertIn("cp", steps[2]["run"])
        self.assertIn(".github/actions/hanoi-buffer20-bootstrap/action.yml", steps[2]["run"])
        self.assertIn("$GITHUB_WORKSPACE/scripts", steps[3]["run"])

    def test_fetch_filters_parent_and_extension_and_rejects_unsafe_paths(self):
        prefix = f"hanoi-b20-123-{self.extension_id[:12]}-batch-"
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            archive.writestr("record/task_result.json", "{}")
        archive_bytes = stream.getvalue()
        calls = []
        def api(endpoint):
            calls.append(endpoint)
            if "/workflows/" in endpoint:
                return json.dumps({"workflow_runs": [{"id": 55}]}).encode()
            if endpoint.endswith("/zip"):
                return archive_bytes
            return json.dumps({"artifacts": [
                {"id": 1, "name": prefix + "1-run-55-a1", "expired": False},
                {"id": 2, "name": "hanoi-b20-other-parent-batch-1", "expired": False},
                {"id": 3, "name": prefix + "1-expired", "expired": True}]}).encode()
        with tempfile.TemporaryDirectory() as tmp:
            args = argparse.Namespace(capsule=self.capsule, repo="owner/repo", output=Path(tmp) / "collected")
            with patch.object(full, "fetch_batches") as parent_fetch, patch.object(extension, "gh", side_effect=api):
                extension.fetch(args, self.parent, self.extension_id)
                self.assertEqual(parent_fetch.call_args.args[0].output, args.output / "parent")
            self.assertTrue((args.output / "buffer20/1/record/task_result.json").exists())
            self.assertFalse((args.output / "buffer20/2").exists())
            self.assertTrue(any("hanoi-buffer20.yml" in value for value in calls))
            stream = io.BytesIO()
            with zipfile.ZipFile(stream, "w") as archive:
                archive.writestr("../escape.json", "{}")
            archive_bytes = stream.getvalue()
            args.output = Path(tmp) / "unsafe"
            with patch.object(full, "fetch_batches"), patch.object(extension, "gh", side_effect=api):
                with self.assertRaises(ValueError):
                    extension.fetch(args, self.parent, self.extension_id)


if __name__ == "__main__":
    unittest.main()
