import argparse
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import workload_tiebreak_experiment as experiment


class TimeCapProtocolTests(unittest.TestCase):
    def test_completed_iteration_budget(self):
        task={'expected_iterations':10,'expected_time_limit_sec':7200}
        experiment.validate_termination(task,10,'iteration_limit')
        with self.assertRaises(AssertionError):
            experiment.validate_termination(task,9,'iteration_limit')

    def test_time_limited_result_is_not_a_technical_failure(self):
        task={'expected_iterations':10,'expected_time_limit_sec':7200}
        experiment.validate_termination(task,0,'time_limit')
        experiment.validate_termination(task,9,'time_limit')
        with self.assertRaises(AssertionError):
            experiment.validate_termination(task,10,'time_limit')
        with self.assertRaises(AssertionError):
            experiment.validate_termination(task,1,'unexpected')

    def test_legacy_bundle_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)
            manifest={'tasks':[],'time_limit_sec':0}
            manifest['protocol_id']=hashlib.sha256(json.dumps(manifest,sort_keys=True).encode()).hexdigest()
            experiment.dump(path/'task_manifest.json',manifest)
            with self.assertRaisesRegex(ValueError,'Old unlimited-time bundle'):
                experiment.load(path)

    def test_aggregate_accepts_mixed_valid_termination_reasons(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            tasks=[dict(key='normal',dataset='test',instance='6.1.1',variant='normal',batch=1,
                        expected_iterations=10,expected_time_limit_sec=7200),
                   dict(key='double',dataset='test',instance='6.1.1',variant='double',batch=1,
                        expected_iterations=20,expected_time_limit_sec=14400)]
            manifest={'tasks':tasks,'protocol_version':2}
            manifest['protocol_id']=hashlib.sha256(json.dumps(manifest,sort_keys=True).encode()).hexdigest()
            experiment.dump(root/'bundle/task_manifest.json',manifest)
            for task,count,reason in [(tasks[0],10,'iteration_limit'),(tasks[1],12,'time_limit')]:
                experiment.dump(root/'input'/task['key']/'result.json',dict(task=task,
                    protocol_id=manifest['protocol_id'],iterations=count,stop_reason=reason,
                    time_limit_sec=task['expected_time_limit_sec'],feasible=True,makespan_sec=100,workload_l2_sec=120))
            args=argparse.Namespace(bundle=root/'bundle',input=root/'input',output=root/'output',batch=1)
            experiment.aggregate(args)
            coverage=json.loads((args.output/'coverage.json').read_text())
            self.assertEqual(coverage['completed'],2)
            self.assertEqual(coverage['time_limited'],1)
            self.assertEqual(coverage['iteration_limited'],1)


if __name__=='__main__':
    unittest.main()
