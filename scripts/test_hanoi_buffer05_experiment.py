import argparse
from collections import Counter
import copy
from itertools import product
import json
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
import yaml

import hanoi_buffer05_experiment as extension

full = extension.full


class Buffer05Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.capsule = Path(cls.temporary.name) / "capsule"
        cls.capsule.mkdir()
        choices = [full.ROOT / ".github/Data_12day_9.2026/hanoi_protocol_buffer10",
                   full.ROOT / ".github/data/hanoi_protocol_buffer10"]
        bundle = next(path for path in choices if path.exists())
        shutil.copytree(bundle, cls.capsule / "bundle")
        cls.bundle = json.loads((bundle / "manifest.json").read_text())
        (cls.capsule / "solver").write_text("test solver")
        manifest = {"experiment_id": "123", "commit": "a" * 40,
            "buffers_pct": [0, 10], "PI_buffer_pct": 0, "start_hour": 8, "p0_window": [8, 17],
            "binary_sha256": full.sha256(cls.capsule / "solver"),
            "bundle_manifest_sha256": full.sha256(cls.capsule / "bundle/manifest.json"),
            "code_sha256": {name: full.sha256(full.ROOT / name) for name in full.CODE},
            "tasks": full.tasks(cls.bundle["instances"])}
        manifest["protocol_id"] = full.digest(manifest)
        full.base.dump(cls.capsule / "experiment.json", manifest)
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
        record = {"protocol_id": self.parent["protocol_id"], "extension_id": self.extension_id,
            "planning_freshness_sec": 3420, "replay_freshness_sec": 3600,
            "task": task, "planning": metrics, "training_dates": [dates[k] for k in train], "cases": []}
        for day in (full.base.DAYS if task["day_type"] == "shared" else (task["day_type"],)):
            replay = {**metrics, "makespan_sec": 80., "freshness_failure": True, "n_violated": 1,
                      "feasible": False, "max_excess_pct": 10., "max_excess_min": 6., "failure_gt5": True}
            record["cases"].append({"day_type": day, "test_date": dates[heldout[full.base.DAYS.index(day)]],
                                    "replay": replay if feasible else None})
        return record

    def test_fraction_is_five_percent_not_fifty_percent(self):
        self.assertEqual(extension.BUFFER_PERCENT, 5)
        self.assertEqual(extension.EPSILON, .05)
        self.assertEqual((1 - extension.EPSILON) * 3600, 3420)
        self.assertNotEqual(extension.PLANNING_FRESHNESS_SEC, 1800)

    def test_exact_universe_all_sixteen_yaml_batches(self):
        selected = extension.tasks(self.parent)
        expected = set()
        for policy in ("P0", "P1", "P2"):
            days = full.base.DAYS if policy == "P2" else ("shared",)
            expected.update(product(self.bundle["instances"], range(1, 5), full.base.SEEDS, (policy,), days, (5,)))
        actual = {(t["instance"], t["fold"], t["seed"], t["policy"], t["day_type"], t["buffer_pct"]) for t in selected}
        self.assertEqual(actual, expected)
        self.assertEqual(Counter(t["policy"] for t in selected), {"P0": 800, "P1": 800, "P2": 2400})
        self.assertTrue(all(t["key"].endswith("-b05") for t in selected))
        workflow = yaml.load((full.ROOT / ".github/workflows/hanoi-buffer05.yml").read_text(), Loader=yaml.BaseLoader)
        batches = list(map(int, workflow["on"]["workflow_dispatch"]["inputs"]["batch"]["options"]))
        assigned = [t for batch in batches for t in extension.batch_tasks(self.parent, batch)]
        self.assertEqual(len(assigned), 4000)
        self.assertEqual(len({t["key"] for t in assigned}), 4000)
        self.assertEqual(assigned, selected)
        self.assertEqual(sum(3 if t["day_type"] == "shared" else 1 for t in assigned), 7200)
        self.assertTrue(all(len(extension.batch_tasks(self.parent, b)) == 250 for b in batches))
        for batch in (0, 17):
            with self.assertRaises(ValueError):
                extension.batch_tasks(self.parent, batch)
        self.assertFalse(any(t["policy"] == "PI" for t in selected))
        self.assertEqual({t["buffer_pct"] for t in self.parent["tasks"]}, {0, 10})

    def test_frozen_source_verification(self):
        with patch.object(full, "sha256", return_value="changed"):
            with self.assertRaises(ValueError):
                extension.context(self.capsule)

    def test_all_policies_plan_at_3420_replay_at_3600(self):
        for policy in ("P0", "P1", "P2"):
            task = next(t for t in extension.tasks(self.parent) if t["policy"] == policy)
            calls = []
            def solver(binary, instance, data, profile, work, seed, solution=None):
                limits = {float(line.split()[-1]) for line in instance.read_text().splitlines()[6:]}
                calls.append((limits, solution, seed))
                return self.record(task)["planning"]
            store = SimpleNamespace(bundle=self.capsule / "bundle", daily=lambda name: np.ones((12, 1, 1, 12)),
                data={"hours": np.arange(6, 18), "dates": np.array(self.bundle["dates"])})
            with tempfile.TemporaryDirectory() as tmp, patch.object(full, "Profiles", return_value=store), patch.object(full.base, "run_solver", side_effect=solver):
                args = argparse.Namespace(capsule=self.capsule, batch=1, task_key=task["key"], output=Path(tmp) / "task")
                extension.run_task(args, self.parent, self.extension_id)
                saved = json.loads((args.output / "task_result.json").read_text())
                self.assertEqual(saved["planning_freshness_sec"], 3420)
                self.assertEqual(saved["replay_freshness_sec"], 3600)
            self.assertEqual(calls[0], ({3420.}, None, task["seed"]))
            self.assertEqual(len(calls), 2 if policy == "P2" else 4)
            self.assertTrue(all(limit == {3600.} and solution is not None for limit, solution, seed in calls[1:]))

    def test_collection_distinguishes_missing_and_infeasible(self):
        task = extension.tasks(self.parent)[0]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            expected = {task["key"]: task}
            self.assertEqual(extension.collect(root, self.parent, expected, self.extension_id)[1]["missing"], [task["key"]])
            record = self.record(task, feasible=False)
            full.base.dump(root / "first/task_result.json", record)
            records, report = extension.collect(root, self.parent, expected, self.extension_id)
            self.assertFalse(report["missing"])
            self.assertFalse(records[task["key"]]["planning"]["feasible"])
            self.assertTrue(all(c["replay"] is None for c in records[task["key"]]["cases"]))
            full.base.dump(root / "retry/task_result.json", record)
            self.assertEqual(extension.collect(root, self.parent, expected, self.extension_id)[1]["identical_retries"], [task["key"]])
            wrong = copy.deepcopy(record)
            wrong["planning_freshness_sec"] = 1800
            full.base.dump(root / "wrong/task_result.json", wrong)
            self.assertTrue(extension.collect(root / "wrong", self.parent, expected, self.extension_id)[1]["invalid"])
            wrong["planning_freshness_sec"] = 3420
            wrong["extension_id"] = "other runner"
            full.base.dump(root / "wrong/task_result.json", wrong)
            self.assertTrue(extension.collect(root / "wrong", self.parent, expected, self.extension_id)[1]["invalid"])
            wrong = copy.deepcopy(record)
            wrong["planning"]["makespan_sec"] += 1
            full.base.dump(root / "retry/task_result.json", wrong)
            self.assertTrue(extension.collect(root, self.parent, expected, self.extension_id)[1]["conflicting_retries"])

    def test_pack_complete_batch_and_partial_preservation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tasks = extension.batch_tasks(self.parent, 1)
            for task in tasks:
                full.base.dump(root / "input" / task["key"] / "task_result.json", self.record(task))
            args = argparse.Namespace(batch=1, input=root / "input", output=root / "packed")
            extension.pack(args, self.parent, self.extension_id)
            self.assertEqual(len(list(args.output.glob("*/task_result.json"))), 250)
            coverage = json.loads((args.output / "batch_coverage.json").read_text())
            self.assertFalse(coverage["missing"])
            self.assertEqual(coverage["buffer_pct"], 5)
            (args.input / tasks[0]["key"] / "task_result.json").unlink()
            args.output = root / "partial"
            with self.assertRaises(SystemExit):
                extension.pack(args, self.parent, self.extension_id)
            self.assertEqual(len(list(args.output.glob("*/task_result.json"))), 249)
            coverage = json.loads((args.output / "batch_coverage.json").read_text())
            self.assertEqual(coverage["missing"], [tasks[0]["key"]])

    def test_action_restored_after_frozen_checkout(self):
        action = yaml.load((full.ROOT / ".github/actions/hanoi-buffer05-bootstrap/action.yml").read_text(), Loader=yaml.BaseLoader)
        steps = action["runs"]["steps"]
        self.assertIn("cp scripts/hanoi_buffer05_experiment.py", steps[0]["run"])
        self.assertIn("hanoi-buffer05-bootstrap.yml", steps[0]["run"])
        self.assertEqual(steps[1]["uses"], "./.github/actions/hanoi-capsule-bootstrap")
        self.assertEqual(steps[2]["if"], "always()")
        self.assertIn(".github/actions/hanoi-buffer05-bootstrap/action.yml", steps[2]["run"])


if __name__ == "__main__":
    unittest.main()
