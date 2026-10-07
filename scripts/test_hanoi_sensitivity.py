import tempfile
import unittest
from collections import Counter
from pathlib import Path

import hanoi_sensitivity as sensitivity


class SensitivityTests(unittest.TestCase):
    def test_complete_task_matrix(self):
        tasks = sensitivity.tasks()
        self.assertEqual(Counter(t['policy'] for t in tasks),
                         {'P0_mean': 200, 'P0_q25': 200, 'P1': 800, 'P2': 2400})
        self.assertEqual(len({t['task_id'] for t in tasks}), 3600)
        self.assertTrue(all(sensitivity.expand(sensitivity.compact(t)) == t for t in tasks))
        matrix = sensitivity.build_matrix()['include']
        self.assertEqual(len(matrix), 250)
        self.assertEqual(sum(batch['task_count'] for batch in matrix), 3600)
        self.assertLessEqual(max(batch['task_count'] for batch in matrix), 15)

    def test_quantile_interpolates(self):
        self.assertEqual(sensitivity.percentile([30, 0, 20, 10]), 7.5)
        self.assertEqual(sensitivity.percentile([25]), 25)
        with self.assertRaises(ValueError):
            sensitivity.percentile([])

    def test_scale_changes_only_freshness(self):
        source = Path(__file__).resolve().parents[1] / sensitivity.base.DATA_ROOT / 'set_03'
        source /= 'hanoi_10x10_100_set_03_weekday.txt'
        original = source.read_text().splitlines()
        start = original.index('X Y Dronable Demand Drone_service Truck_service Lw') + 1
        with tempfile.TemporaryDirectory() as temporary:
            for scale, limit in [(0.8, 2880), (1.0, 3600), (1.2, 4320)]:
                target = Path(temporary) / f'{scale}.txt'
                sensitivity.scale_instance(source, target, scale)
                modified = target.read_text().splitlines()
                self.assertEqual(original[:start], modified[:start])
                self.assertEqual(len(original), len(modified))
                for before, after in zip(original[start:], modified[start:]):
                    self.assertEqual(before.split()[:-1], after.split()[:-1])
                    self.assertEqual(float(after.split()[-1]), limit)

    def test_regret_uses_matching_scale_and_feasible_pairs(self):
        common = dict(dataset='set_03', actual_profile='weekday', start_hour='7', seed='1')
        rows = [{**common, 'freshness_scale': '0.8', 'policy': 'P2',
                 'feasibility': 'FEASIBLE', 'realized_makespan_s': '100'},
                {**common, 'freshness_scale': '1.2', 'policy': 'P2',
                 'feasibility': 'FEASIBLE', 'realized_makespan_s': '200'},
                {**common, 'freshness_scale': '0.8', 'policy': 'P0_q25',
                 'feasibility': 'FEASIBLE', 'realized_makespan_s': '110'},
                {**common, 'freshness_scale': '0.8', 'policy': 'P0_q25', 'seed': '2',
                 'feasibility': 'INFEASIBLE', 'realized_makespan_s': '900'}]
        summaries = sensitivity.summarize(rows)
        row = next(r for r in summaries if r['policy'] == 'P0_q25' and r['actual_profile'] == 'weekday')
        self.assertEqual(row['failure_rate_pct'], 50)
        self.assertEqual(row['regret_cases'], 1)
        self.assertAlmostEqual(row['conditional_regret_raw_pct'], 10)
        self.assertIsNone(row['conditional_regret_pct'])

    def test_reporting_threshold(self):
        rows = []
        for seed in range(30):
            common = dict(dataset='set_03', actual_profile='weekday', start_hour='7',
                          seed=str(seed), freshness_scale='1.0', feasibility='FEASIBLE')
            rows.extend([{**common, 'policy': 'P2', 'realized_makespan_s': '100'},
                         {**common, 'policy': 'P1', 'realized_makespan_s': '105'}])
        row = next(r for r in sensitivity.summarize(rows)
                   if r['policy'] == 'P1' and r['actual_profile'] == 'weekday')
        self.assertEqual(row['regret_cases'], 30)
        self.assertAlmostEqual(row['conditional_regret_pct'], 5)


if __name__ == '__main__':
    unittest.main()
