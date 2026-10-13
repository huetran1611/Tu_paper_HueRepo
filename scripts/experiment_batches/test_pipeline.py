import ast
import copy
import csv
import json
import os
import runpy
import tempfile
import unittest
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from unittest.mock import patch

import yaml

import collect
import plan


class PipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tasks = plan.all_tasks()
        digest = plan.plan_hash(cls.tasks)
        cls.manifests = [{
            "task": task, "batch": (int(task["global_id"]) - 1) // 250 + 1,
            "plan_hash": digest, "commit": "test-commit",
            "time_limit_sec": "7200", "return_code": 0,
        } for task in cls.tasks]

    def test_partition_and_five_runs_per_configuration(self):
        self.assertEqual(Counter(task["experiment"] for task in self.tasks),
                         {"big": 320, "trip": 960, "vehicle": 320})
        batches = [plan.batch_tasks(self.tasks, number) for number in range(1, 8)]
        self.assertEqual([len(batch) for batch in batches], [250] * 6 + [100])
        flattened = [task["global_id"] for batch in batches for task in batch]
        self.assertEqual(len(set(flattened)), 1600)
        groups = defaultdict(list)
        for task in self.tasks:
            key = (task["experiment"], task["base"], task.get("scenario"),
                   task.get("mode"), task.get("drone_capacity"), task.get("variant"))
            groups[key].append(task["run"])
        self.assertEqual(len(groups), 320)
        self.assertTrue(all(sorted(runs) == [1, 2, 3, 4, 5] for runs in groups.values()))

    def test_manifest_rejects_missing_duplicate_and_mixed_runs(self):
        collect.validate_manifests(self.manifests, self.tasks)
        invalid = [self.manifests[:-1], self.manifests + [self.manifests[0]]]
        for field, value in (("commit", "other-commit"), ("time_limit_sec", "3600"),
                             ("return_code", 1), ("batch", 7)):
            manifests = copy.deepcopy(self.manifests)
            manifests[0][field] = value
            invalid.append(manifests)
        for manifests in invalid:
            with self.assertRaises(ValueError):
                collect.validate_manifests(manifests, self.tasks)

    def test_workflow_and_python_syntax(self):
        root = plan.SCRIPT_DIR.parents[1]
        for path in plan.SCRIPT_DIR.glob("*.py"):
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        callers = []
        for path in (root / ".github" / "workflows").glob("*.yml"):
            document = yaml.safe_load(path.read_text(encoding="utf-8"))
            for job in document.get("jobs", {}).values():
                if job.get("uses") == "./.github/workflows/experiment-batch-reusable.yml":
                    callers.append(job["with"]["batch_number"])
            if path.name == "experiment-batch-reusable.yml":
                self.assertIn("task_json", json.dumps(document["jobs"]["solve"]))
                self.assertEqual(document["jobs"]["solve"]["timeout-minutes"], 360)
                self.assertNotIn("max-parallel", document["jobs"]["solve"]["strategy"])
        self.assertEqual(sorted(callers), list(range(1, 8)))

    def test_complete_summary_includes_runs_four_and_five(self):
        previous = Path.cwd()
        with tempfile.TemporaryDirectory() as directory:
            try:
                os.chdir(directory)
                for task in self.tasks:
                    folder = Path(plan.COLLECT_DIRS[task["experiment"]]) / task["global_id"]
                    folder.mkdir(parents=True)
                    run = task["run"]
                    common = (f"Instance: {task['base']}\nRun: {run}\n"
                              f"Improved solution cost: {100 - run}\n"
                              f"Mean elapsed time: {run * 10} seconds\n"
                              "Final solution feasibility: FEASIBLE\n"
                              "Truck Routes:\nTruck 1: 0 1 0\n"
                              "Drone Routes:\nDrone 1: 0 2 0\nTotal validation:\n")
                    if task["experiment"] == "big":
                        name = f"{task['scenario']}.{task['base']}.Dd227.run{run}.txt"
                        content = f"Scenario: {task['scenario']}\n" + common
                        (folder / name).write_text(content, encoding="utf-8")
                    elif task["experiment"] == "vehicle":
                        name = f"{task['base']}.{task['variant']}.run{run}.txt"
                        content = (f"Customer count: {task['customer_count']}\n"
                                   f"Variant: {task['variant']}\nTrucks: {task['trucks']}\n"
                                   f"Drones: {task['drones']}\n") + common
                        (folder / name).write_text(content, encoding="utf-8")
                    else:
                        row = dict(instance=task["base"], mode=task["mode"],
                                   drone_capacity=task["drone_capacity"], gridsize=task["gridsize"],
                                   number_of_customer=task["number_of_customer"], run=run,
                                   truck_route="0 1 0", drone_route="0 2 0", drone_ratio="0.01",
                                   best_objective=str(100 - run), solution_status="FEASIBLE",
                                   process_status="success", return_code=0, elapsed_seconds=str(run * 10))
                        (folder / "result.json").write_text(json.dumps(row), encoding="utf-8")
                with patch.dict(os.environ, EXPECTED_TASK_COUNT="320"):
                    for kind in plan.COUNTS:
                        runpy.run_path(str(plan.SCRIPT_DIR / f"{kind}_summary.py"))
                collect.summarize_trip()
                for file in ("big_instance_dd227_best_summary.csv",
                             "instance_vehicle_sweep_best_summary.csv",
                             "time_dependent_trip_capacity_best_summary.csv"):
                    with open(file, newline="", encoding="utf-8") as handle:
                        rows = list(csv.DictReader(handle))
                    self.assertTrue(all(int(row["best_run"]) == 5 for row in rows))
                    self.assertTrue(all(float(row["mean_obj_5_runs"]) == 97 for row in rows))
                    self.assertEqual(len(rows), 192 if file.startswith("time_dependent") else 64)
                for file in ("big_instance_dd227_summary.xlsx", "instance_vehicle_sweep_summary.xlsx"):
                    with zipfile.ZipFile(file) as workbook:
                        self.assertIsNone(workbook.testzip())
            finally:
                os.chdir(previous)


if __name__ == "__main__":
    unittest.main()
