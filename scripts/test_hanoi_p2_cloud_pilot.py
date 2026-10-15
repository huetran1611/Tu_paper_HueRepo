import unittest
import argparse
import contextlib
import io
import json
from pathlib import Path
import tempfile

from hanoi_p2_cloud_pilot import DAY_TYPES, aggregate, split_batch, tasks
from hanoi_pilot_selected import FLAGS


class CloudPilotTests(unittest.TestCase):
    def setUp(self):
        self.instances = [f"set_{k:02d}" for k in range(20)]
        self.tasks = tasks(self.instances)

    def test_all_240_unique_cases(self):
        self.assertEqual(len(self.tasks), 240)
        self.assertEqual(len({t["key"] for t in self.tasks}), 240)
        for instance in self.instances:
            for day in DAY_TYPES:
                folds = {t["fold"] for t in self.tasks if t["instance"] == instance and t["day_type"] == day}
                self.assertEqual(folds, {1, 2, 3, 4})

    def test_five_disjoint_batches(self):
        batches = [split_batch(self.tasks, k) for k in range(1, 6)]
        self.assertTrue(all(len(batch) == 48 for batch in batches))
        self.assertEqual([task for batch in batches for task in batch], self.tasks)

    def test_invalid_batch_rejected(self):
        with self.assertRaises(ValueError):
            split_batch(self.tasks, 6)

    def test_selected_capacity_and_search_settings(self):
        for flag in ("--truck-capacity=40", "--iters=9000", "--seed=42", "--c-seg=2",
                     "--c-tau=2", "--h-mode=1", "--h-div=4", "--sa-t0=0.005", "--sa-alpha=0.9982"):
            self.assertIn(flag, FLAGS)

    def check_aggregate(self, count, iterations=9000):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = root / "bundle"
            bundle.mkdir()
            (bundle / "manifest.json").write_text(json.dumps({"instances": self.instances}))
            source = root / "input"
            source.mkdir()
            for task in self.tasks[:count]:
                path = source / task["key"]
                path.mkdir()
                row = {**task, "seed": 42, "start_hour": 10, "iterations": iterations,
                       "feasible": True, "test_feasible": True, "finish_hour": 16., "test_finish_hour": 16.5}
                (path / "result.json").write_text(json.dumps(row))
            args = argparse.Namespace(bundle=bundle, input=source, output=root / "output")
            with contextlib.redirect_stdout(io.StringIO()):
                if count != 240 or iterations != 9000:
                    with self.assertRaises(SystemExit):
                        aggregate(args)
                else:
                    aggregate(args)
            report = json.loads((args.output / "report.json").read_text())
            self.assertEqual(report["all_240_feasible_and_finish_by_17h"], count == 240 and iterations == 9000)

    def test_complete_aggregate(self):
        self.check_aggregate(240)

    def test_missing_case_cannot_pass(self):
        self.check_aggregate(239)

    def test_smoke_configuration_cannot_pass(self):
        self.check_aggregate(240, iterations=2)


if __name__ == "__main__":
    unittest.main()
