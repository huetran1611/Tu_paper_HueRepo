import copy
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import hanoi_v2_continue as c


class ContinuationTests(unittest.TestCase):
    def scenario(self, pilot_conclusion="success", valid=250, all_existing=False):
        root=Path(__file__).resolve().parents[1]
        manifest=json.loads((root/"results/hanoi_v2_github_20261020/capsule/experiment.json").read_text())
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        output=Path(temp.name)/"status"
        args=SimpleNamespace(capsule=Path(temp.name)/"capsule",output=output,
            setup_run_id="39416770489",pilot_run_id="39416901265",ref="codex/hanoi-v2-20261020",
            repo="owner/repo",wait_minutes=1,max_parallel=20)
        tasks=[t for t in manifest["tasks"] if t["owner"]=="monday"][:250]
        fake_full=SimpleNamespace(batch_tasks=lambda *a:tasks)
        fake_v2=SimpleNamespace(load=lambda p:(fake_full,copy.deepcopy(manifest)),
            collect=lambda *a:({str(i):{} for i in range(valid)},
                {"missing":[],"invalid":[],"conflicting_retries":[]}))
        counts={"PI":10,"P0":13,"monday":18,"saturday":18,"sunday":18}
        def item(group,batch,run):
            return {"id":run,"display_title":f"Hanoi V2 - batch - {group} - batch {batch} - setup 39416770489",
                    "html_url":f"https://github.com/owner/repo/actions/runs/{run}"}
        existing={('monday',1):item('monday',1,39416901265)}
        if all_existing:
            for group,n in counts.items():
                for b in range(1,n+1):existing.setdefault((group,b),item(group,b,100000+len(existing)))
        calls=[]
        def api(repo,endpoint):
            if endpoint.startswith("actions/workflows/"):
                return {"workflow_runs":list(existing.values())}
            if endpoint.endswith("/artifacts?per_page=100&page=1"):
                return {"artifacts":[{"name":"hanoi-v2-39416770489-batch-monday-1-run-39416901265-a1","expired":False}]}
            return {"head_branch":args.ref,"head_sha":manifest["commit"],"status":"completed",
                    "conclusion":pilot_conclusion,"run_attempt":1}
        def process(command,**kw):
            if command[1:3]==["run","download"]:
                path=Path(command[command.index("--dir")+1]);path.mkdir(parents=True)
                (path/"batch_coverage.json").write_text(json.dumps({"missing":[],"invalid":[],"conflicting_retries":[]}))
                return subprocess.CompletedProcess(command,0,stdout="",stderr="")
            fields={s.split("=",1)[0]:s.split("=",1)[1] for s in command if "=" in s}
            pair=fields['group'],int(fields['batch'])
            self.assertNotIn(pair,existing)
            calls.append(pair)
            run=100000+len(existing)
            existing[pair]=item(*pair,run)
            return subprocess.CompletedProcess(command,0,stdout=existing[pair]['html_url'],stderr="")
        with patch.object(c,'api',side_effect=api),patch.object(c.importlib,'import_module',return_value=fake_v2), \
             patch.object(c.subprocess,'run',side_effect=process),patch.object(c.time,'sleep'):
            error=None
            try:c.run(args)
            except Exception as e:error=e
        state=json.loads((output/'launch_state.json').read_text())
        return calls,state,error

    def test_failed_pilot_never_dispatches(self):
        calls,state,error=self.scenario(pilot_conclusion='failure')
        self.assertIsNotNone(error);self.assertFalse(calls)
        self.assertEqual(state['status'],'stopped_needs_attention')

    def test_incomplete_pilot_never_dispatches(self):
        calls,state,error=self.scenario(valid=249)
        self.assertIsNotNone(error);self.assertFalse(calls)

    def test_success_dispatches_exactly_76_without_pilot_duplicate(self):
        calls,state,error=self.scenario()
        self.assertIsNone(error)
        self.assertEqual(len(calls),76)
        self.assertEqual(len(set(calls)),76)
        self.assertNotIn(('monday',1),calls)
        self.assertEqual(len(state['runs']),77)
        self.assertFalse(state['gating_uses_test_outcomes'])

    def test_resume_skips_all_existing_runs(self):
        calls,state,error=self.scenario(all_existing=True)
        self.assertIsNone(error);self.assertFalse(calls)
        self.assertEqual(state['newly_submitted'],0)
        self.assertEqual(len(state['existing']),77)


if __name__=='__main__':unittest.main()
