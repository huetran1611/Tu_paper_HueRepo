import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
import yaml

import hanoi_v2_experiment as v2


class V2WorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory(prefix="hanoi-v2-test-")
        cls.capsule=Path(cls.temporary.name)/"capsule"
        subprocess.run([sys.executable,"-B",str(Path(v2.__file__)),"freeze","--capsule",str(cls.capsule),
                        "--experiment-id","test-v2","--commit","test-v2"],check=True,capture_output=True)
        cls.manifest=json.loads((cls.capsule/"experiment.json").read_text())

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_task_coverage_buffers_and_shared_PI(self):
        tasks=self.manifest["tasks"]
        self.assertEqual(len(tasks),18400)
        self.assertEqual(len({t["key"] for t in tasks}),18400)
        self.assertEqual({t["buffer_pct"] for t in tasks},{0,5,10,15})
        pi=[t for t in tasks if t["policy"]=="PI"]
        self.assertEqual(len(pi),2400)
        self.assertTrue(all(t["buffer_pct"]==0 for t in pi))
        self.assertFalse(self.manifest["original_results_or_PI_reused"])

    def test_exact_77_batches_no_more_than_250_jobs(self):
        counts={"P0":3200,"monday":4268,"saturday":4268,"sunday":4264,"PI":2400}
        seen=set();total=0
        for group,count in counts.items():
            tasks=[t for t in self.manifest["tasks"] if t["owner"]==group]
            self.assertEqual(len(tasks),count)
            batches=[tasks[i:i+250] for i in range(0,len(tasks),250)]
            total+=len(batches)
            for batch in batches:
                self.assertLessEqual(len(batch),250)
                for task in batch:
                    self.assertNotIn(task["key"],seen);seen.add(task["key"])
        self.assertEqual((total,len(seen)),(77,18400))

    def test_frozen_original_solver_and_new_profiles(self):
        original=json.loads((self.capsule/"original_protocol.json").read_text())
        self.assertEqual(self.manifest["binary_sha256"],original["binary_sha256"])
        self.assertEqual(self.manifest["flags"],original["flags"])
        self.assertNotEqual(self.manifest["profiles_sha256"],original["profiles_sha256"])
        subprocess.run([sys.executable,"-B",v2.__file__,"check","--capsule",str(self.capsule)],
                       check=True,capture_output=True)

    def test_actual_matrix_rejects_nonexistent_PI_batch(self):
        base=[sys.executable,"-B",v2.__file__,"matrix","--capsule",str(self.capsule),"--policy","PI"]
        final=subprocess.run(base+["--batch","10"],check=True,capture_output=True,text=True)
        self.assertEqual(len(json.loads(final.stdout)["include"]),150)
        invalid=subprocess.run(base+["--batch","11"],capture_output=True)
        self.assertNotEqual(invalid.returncode,0)

    def test_S1_crosses_hour_boundary_and_holds_last_speed(self):
        hours=np.arange(6,18)
        speeds=np.full((1,12),60.)
        speeds[:,2]=30
        self.assertAlmostEqual(v2.travel_seconds(np.array([30000.]),speeds,hours,8.5)[0],2700)
        self.assertAlmostEqual(v2.travel_seconds(np.array([30000.]),speeds,hours,18)[0],1800)

    def test_missing_route_payload_not_counted_as_planning_NP(self):
        record={"planning":{"feasible":True},"cases":[]}
        fake=SimpleNamespace(collect_records=lambda *args:({"task":record},
            {"missing":[],"invalid":[],"conflicting_retries":[]}))
        records,report=v2.collect(fake,{}, {"task":{}},Path("unused"))
        self.assertFalse(records)
        self.assertEqual(report["missing"],["task"])
        self.assertEqual(len(report["invalid"]),1)

    def test_workflow_contracts_and_no_old_initialization_default(self):
        root=Path(v2.__file__).resolve().parents[1]
        files=[root/".github/workflows/hanoi-v2.yml",root/".github/workflows/hanoi-v2-batch.yml",
               root/".github/actions/hanoi-v2-bootstrap/action.yml"]
        docs=[yaml.load(p.read_text(),Loader=yaml.BaseLoader) for p in files]
        self.assertIn("workflow_dispatch",docs[0]["on"])
        self.assertIn("workflow_call",docs[1]["on"])
        self.assertEqual(docs[1]["jobs"]["experiment"]["strategy"]["fail-fast"],"false")
        self.assertEqual(docs[1]["jobs"]["experiment"]["timeout-minutes"],"60")
        self.assertNotIn("38888658850", "".join(p.read_text() for p in files))

    def test_train_test_split_of_frozen_runtime(self):
        code='''import sys,numpy as np
sys.path.insert(0,sys.argv[1])
from hanoi_buffer10_cloud import profiles_for
a=np.broadcast_to(np.arange(1,13)[:,None,None,None],(12,2,2,12)).astype(float)
for fold in range(1,5):
 held=[fold-1,fold+3,fold+7]
 for policy in ('P0','P1'):
  p,h,t=profiles_for(a,np.arange(6,18),fold,policy,'shared')
  assert h==held and len(t)==9 and not set(t)&set(held)
 for g,day in enumerate(('monday','saturday','sunday')):
  p,h,t=profiles_for(a,np.arange(6,18),fold,'P2',day)
  assert len(t)==3 and all(i//4==g and i not in held for i in t)
  p,h,t=profiles_for(a,np.arange(6,18),fold,'PI',day)
  assert t==[] and np.array_equal(p,a[held[g]])
'''
        subprocess.run([sys.executable,"-B","-c",code,str(self.capsule/"runtime/scripts")],
                       check=True,capture_output=True)

    def test_complete_report_with_synthetic_records_not_solver_results(self):
        with patch.dict(sys.modules),patch.object(sys,"path",list(sys.path)):
            for name in ("hanoi_full_experiment","hanoi_buffer10_cloud","hanoi_p2_cloud_pilot",
                         "hanoi_pilot_selected","hanoi_protocol_pilot"):
                sys.modules.pop(name,None)
            self._check_complete_fixture_report()

    def _check_complete_fixture_report(self):
        from openpyxl import load_workbook
        full,_=v2.load(self.capsule)
        records={}
        dates=json.loads((self.capsule/"bundle/manifest.json").read_text())["dates"]
        for task in self.manifest["tasks"]:
            metrics={"iterations":9000,"makespan_sec":3600+task["buffer_pct"],"finish_hour":9,
                "freshness_failure":False,"n_violated":0,"max_excess_pct":0,"max_excess_min":0,
                "failure_gt5":False,"max_truck_trip_load_kg":10,"max_drone_trip_load_kg":2,
                "feasible":True,"capacity_violation":0,"energy_violation":0}
            record={"task":task,"planning":metrics,"planning_solution_text":"SYNTHETIC UNIT TEST ONLY",
                    "planning_customer_events_csv":"SYNTHETIC UNIT TEST ONLY","cases":[]}
            held=[task["fold"]-1+4*g for g in range(3)]
            if task["policy"]=="PI":record["test_date"]=dates[held[full.base.DAYS.index(task["day_type"])]]
            else:
                days=full.base.DAYS if task["day_type"]=="shared" else (task["day_type"],)
                record["cases"]=[{"day_type":day,"test_date":dates[held[full.base.DAYS.index(day)]],
                    "replay":metrics,"customer_events_csv":"SYNTHETIC UNIT TEST ONLY"} for day in days]
            records[task["key"]]=record
        report={"missing":[],"conflicting_retries":[],"invalid":[],"identical_retries":[]}
        output=Path(self.temporary.name)/"synthetic_unit_test_report"
        inputs=Path(self.temporary.name)/"empty_fixture_input";inputs.mkdir()
        with patch.object(full,"collect_records",return_value=(records,report)):
            v2.aggregate(SimpleNamespace(capsule=self.capsule,input=inputs,output=output))
        coverage=json.loads((output/"coverage.json").read_text())
        self.assertTrue(coverage["complete"])
        self.assertEqual(coverage["replay_case_rows"],28800)
        self.assertEqual(coverage["PI_feasible_references"],2400)
        book=load_workbook(output/"Hanoi_V2_Results.xlsx",read_only=True)
        self.assertEqual(sum(1 for _ in book["Planning"]),18401)
        self.assertEqual(sum(1 for _ in book["Replay"]),28801)
        book.close()
        self.assertTrue((output/"bufferI_ratio_means.pdf").exists())
        self.assertTrue((output/"hanoi_information_table.tex").exists())


if __name__=="__main__":unittest.main()
