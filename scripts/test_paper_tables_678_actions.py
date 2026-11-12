import argparse
import base64
import json
import io
import time
from pathlib import Path
import re
import tempfile
import unittest
import zlib
from unittest.mock import patch
from zipfile import ZipFile
import generate_paper_tables_678_workflows as generator
import paper_tables_678 as runner
import paper_tables_678_actions as actions
import download_paper_bundle as downloader
import urllib.request
import urllib.error
from unittest.mock import MagicMock


def manifest():
    return dict(version=2,batch_count=39,protocol_id="test",tasks=[
        dict(key=f"task-{i}",batch=i//250+1,slot=i%250) for i in range(9600)])


class WorkflowTests(unittest.TestCase):
    def test_39_workflows_cover_exactly_9600_jobs(self):
        files=list(generator.generated());self.assertEqual(len(files),39)
        total=0
        for number,(path,text) in enumerate(files,1):
            self.assertEqual(path.read_text(encoding="utf-8"),text)
            slots=json.loads(re.search(r"default: '(.+)'",text)[1])
            expected=250 if number<39 else 100
            self.assertEqual(slots,list(range(expected)));total+=len(slots)
            self.assertEqual(re.findall(r"^  [a-zA-Z_]+:",text.split("\njobs:\n")[1],re.M),["  solve:"])
            self.assertIn(f"      batch: {number}\n",text)
            self.assertNotIn("\nconcurrency:",text)
            self.assertIn("inputs.reservation",text)
            self.assertNotRegex(text,r"(?m)^\s*max-parallel:")
        self.assertEqual(total,9600)
        worker=(generator.ROOT/".github/workflows/paper-tables-678-worker.yml").read_text()
        self.assertRegex(worker,r"(?m)^\s*max-parallel: 20$")
        self.assertIn(' task --bundle bundle --batch "$BATCH" --slot "$SLOT"',worker)
        self.assertEqual(re.findall(r"^  [a-zA-Z_]+:",worker.split("\njobs:\n")[1],re.M),["  solve:"])

    def test_exact_mapping_and_resume_subset(self):
        m=manifest();plan=actions.plans(m,{},set())
        self.assertEqual([len(p["slots"]) for p in plan],[250]*38+[100])
        done={(1,slot):{} for slot in range(250)};done[(2,0)]={};done[(39,99)]={}
        resumed=actions.plans(m,done,{3})
        self.assertEqual(resumed[0]["slots"],[])
        self.assertEqual(resumed[1]["slots"],list(range(1,250)))
        self.assertTrue(resumed[2]["active"])
        self.assertEqual(resumed[38]["slots"],list(range(99)))
        self.assertEqual(runner.task_at(m,39,99)["key"],"task-9599")
        with self.assertRaises(ValueError): runner.task_at(m,39,100)
        with self.assertRaises(ValueError): actions.plans(m,{(40,0):{}},set())
        with self.assertRaises(ValueError): actions.plans(dict(m,version=1),{},set())

    def test_single_job_launches_only_one_solver(self):
        m=manifest()
        with tempfile.TemporaryDirectory() as raw:
            args=argparse.Namespace(bundle=Path(raw),output=Path(raw)/"results",batch=2,slot=13)
            with patch.object(runner.os,"name","posix"),patch.object(runner,"load",return_value=m),patch.object(runner,"run_one") as run:
                runner.task_job(args)
            run.assert_called_once()
            self.assertEqual(run.call_args.args[2]["key"],"task-263")

    def test_inventory_ignores_partial_expired_and_foreign_campaign(self):
        runs=[dict(id=1,path=".github/workflows/paper-tables-678-batch-01.yml",display_title="T678 batch 01 setup-123",status="completed"),
              dict(id=2,path=".github/workflows/paper-tables-678-batch-02.yml",display_title="T678 batch 02 setup-123",status="in_progress"),
              dict(id=3,path=".github/workflows/paper-tables-678-batch-03.yml",display_title="T678 batch 03 setup-999",status="completed")]
        artifacts=[dict(id=10,name="paper-t678-b01-t000-ok",expired=False),dict(id=11,name="paper-t678-b01-t001-partial",expired=False),
                   dict(id=12,name="paper-t678-b01-t002-ok",expired=True),dict(id=13,name="paper-t678-b01-t000-ok",expired=False)]
        def pages(endpoint,key):
            if key=="workflow_runs": return iter(runs)
            if "/runs/1/" in endpoint: return iter(artifacts)
            return iter([])
        with patch.object(actions,"gh_json",return_value=dict(created_at="2026-11-12T00:00:00Z")),patch.object(actions,"pages",side_effect=pages):
            found=actions.inventory("owner/repo","123")
        self.assertEqual(set(found["completed"]),{(1,0)})
        self.assertEqual(found["completed"][(1,0)]["id"],13)
        self.assertEqual(found["active"],{2})
        self.assertEqual(found["run_ids"],[1,2])

    def test_setup_dispatches_coordinator_instead_of_bypassing_capacity(self):
        m=manifest();found=dict(completed={},active={2},run_ids=[])
        with tempfile.TemporaryDirectory() as raw:
            args=argparse.Namespace(bundle=Path(raw),repo='owner/repo',setup_run_id='123',output=Path(raw)/'plan',dispatch=True,ref='main')
            with patch.object(runner,'load',return_value=m),patch.object(actions,'inventory',return_value=found),patch.object(actions,'gh_json') as gh:
                actions.plan(args)
            gh.assert_called_once()
            self.assertIn('coordinate.yml/dispatches',gh.call_args.args[0])

    def test_tail_is_filled_with_17_jobs_without_duplicating_active_slots(self):
        found=dict(completed={},active_jobs=[dict(pair=(1,s)) for s in (0,1,2)],reserved=set(),attempts={})
        result=actions.capacity_plan(found,set())
        self.assertEqual(result['occupied'],3)
        self.assertEqual(result['pairs'],[(1,s) for s in range(3,20)])
        self.assertEqual(result['occupied']+len(result['pairs']),20)
        # Pending jobs count as occupied just like running jobs.
        found['reserved']={(1,s) for s in range(3,20)}
        self.assertEqual(actions.capacity_plan(found,set())['pairs'],[])

    def test_terminal_conclusion_and_completed_timestamp_override_stale_status(self):
        job=dict(id=99,name='solve / batch-8-task-247',status='in_progress',conclusion='success',
                 completed_at='2026-11-11T21:01:24Z')
        self.assertTrue(actions.job_finished(job))
        self.assertFalse(actions.job_finished(dict(job,completed_at=None)))
        self.assertFalse(actions.job_finished(dict(job,conclusion=None)))
        run=dict(id=1,path='.github/workflows/paper-tables-678-batch-08.yml',display_title='T678 batch 08 setup-123',status='completed')
        def pages(endpoint,key):
            return iter([run] if key=='workflow_runs' else [job] if key=='jobs' else [])
        with patch.object(actions,'gh_json',return_value=dict(created_at='2026-11-12T00:00:00Z')),patch.object(actions,'pages',side_effect=pages):
            found=actions.inventory('owner/repo','123',with_jobs=True)
        self.assertEqual(found['active_jobs'],[])

    def test_fill_can_cross_batch_boundaries_and_pause_only_failing_slots(self):
        done={(1,s):{} for s in range(249)}
        found=dict(completed=done,active_jobs=[],reserved=set(),attempts={(2,0):5})
        result=actions.capacity_plan(found,set())
        self.assertEqual(result['pairs'],[(1,249)]+[(2,s) for s in range(1,20)])
        self.assertEqual(result['paused'],[(2,0)])
        found['active_jobs']=[dict(pair=(38,s)) for s in range(40)]
        self.assertEqual(actions.capacity_plan(found,set())['pairs'],[])

    def test_leases_count_once_and_duplicate_real_jobs_count_twice(self):
        found=dict(completed={(1,9):{}},active_jobs=[dict(pair=(1,0)),dict(pair=(1,0))],reserved={(1,1)},attempts={})
        result=actions.capacity_plan(found,{(1,0),(1,1),(1,2),(1,9)})
        self.assertEqual(result['occupied'],4)
        self.assertEqual(len(result['pairs']),16)
        self.assertNotIn((1,2),result['pairs'])
        self.assertNotIn((1,9),result['pairs'])

    def test_prepare_does_not_dispatch_and_apply_rechecks_manual_launches(self):
        found=dict(completed={},active_jobs=[],reserved=set(),attempts={})
        with tempfile.TemporaryDirectory() as raw:
            args=argparse.Namespace(repo='owner/repo',setup_run_id='123',ref='main',cache_file=Path(raw)/'cache.json',output=Path(raw)/'scheduler-plan.json',apply_plan=None,lease_artifact_id=None)
            with patch.dict(actions.os.environ,{'GITHUB_RUN_ID':'111'}),patch.object(actions,'inventory',return_value=found),patch.object(actions,'reservation_pairs',return_value=set()),patch.object(actions,'gh_json') as gh:
                actions.coordinate(args);gh.assert_not_called()
                plan=json.loads(args.output.read_text());self.assertEqual(len(plan['pairs']),20)
                args.apply_plan=args.output;args.lease_artifact_id=55
                found['active_jobs']=[dict(pair=(38,s)) for s in range(17)]
                gh.return_value=dict(name='paper-t678-reservations-setup-123',workflow_run=dict(id=111),expired=False)
                actions.coordinate(args)
                calls=[c for c in gh.call_args_list if c.args[0].endswith('/dispatches')]
                self.assertEqual(len(calls),1)
                self.assertEqual(json.loads(calls[0].args[1]['inputs']['slots']),[0,1,2])
                self.assertEqual(calls[0].args[1]['inputs']['reservation'],'0,1,2')
                gh.reset_mock();plan['created_unix']-=601;args.output.write_text(json.dumps(plan))
                actions.coordinate(args)
                self.assertFalse(any(c.args[0].endswith('/dispatches') for c in gh.call_args_list))

    def test_inventory_reserves_unexpanded_runs_and_invalidates_rerun_cache(self):
        run=dict(id=1,path='.github/workflows/paper-tables-678-batch-01.yml',display_title='T678 batch 01 setup-123 slots-3,4',status='queued',run_attempt=1)
        def pages(endpoint,key):
            return iter([run] if key=='workflow_runs' else [])
        with patch.object(actions,'gh_json',return_value=dict(created_at='2026-11-12T00:00:00Z')),patch.object(actions,'pages',side_effect=pages):
            found=actions.inventory('owner/repo','123',with_jobs=True)
        self.assertEqual(found['reserved'],{(1,3),(1,4)})
        run['status']='completed'
        with tempfile.TemporaryDirectory() as raw:
            cache=Path(raw)/'cache.json'
            with patch.object(actions,'gh_json',return_value=dict(created_at='2026-11-12T00:00:00Z')),patch.object(actions,'pages',side_effect=pages) as calls:
                actions.inventory('owner/repo','123',with_jobs=True,cache_path=cache)
                self.assertEqual(calls.call_count,3)
                calls.reset_mock();actions.inventory('owner/repo','123',with_jobs=True,cache_path=cache)
                self.assertEqual(calls.call_count,1) # Completed artifacts/jobs reused.
                run['run_attempt']=2
                calls.reset_mock();actions.inventory('owner/repo','123',with_jobs=True,cache_path=cache)
                self.assertEqual(calls.call_count,3)

    def test_recent_published_lease_blocks_slots_but_expired_lease_does_not(self):
        lease=dict(setup_run_id='123',created_unix=time.time(),pairs=[[1,7],[1,8]])
        raw=io.BytesIO()
        with ZipFile(raw,'w') as archive: archive.writestr('scheduler-plan.json',json.dumps(lease))
        artifacts=[dict(id=55,created_at=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),expired=False),
                   dict(id=56,created_at='2000-01-01T00:00:00Z',expired=False)]
        with patch.object(actions,'gh_json',return_value=dict(artifacts=artifacts)),patch.object(actions.subprocess,'run',return_value=MagicMock(stdout=raw.getvalue())) as download:
            self.assertEqual(actions.reservation_pairs('owner/repo','123'),{(1,7),(1,8)})
            self.assertEqual(download.call_count,1)
            download.reset_mock()
            self.assertEqual(actions.reservation_pairs('owner/repo','123',exclude_artifact_id=55),set())
            download.assert_not_called()

    def test_download_wait_honors_headers_and_redirect_strips_token(self):
        self.assertGreaterEqual(downloader.delay({'Retry-After':'120'},0,now=1000),120)
        self.assertGreaterEqual(downloader.delay({'X-RateLimit-Remaining':'0','X-RateLimit-Reset':'4600'},0,now=1000),3600)
        self.assertGreaterEqual(downloader.delay({'Retry-After':'Thu, 01 Jan 1970 00:20:00 GMT'},0,now=1000),200)
        self.assertLess(downloader.delay({},0,now=1000),81)
        request=urllib.request.Request('https://api.github.com/archive',headers={'Authorization':'Bearer secret'})
        redirected=downloader.SafeRedirect().redirect_request(request,None,302,'Found',{},'https://storage.example/archive')
        self.assertFalse(redirected.has_header('Authorization'))

    def test_download_retries_rate_limit_but_not_missing_artifact(self):
        opener=MagicMock();response=MagicMock()
        response.__enter__.return_value.read.return_value=b'archive'
        opener.open.side_effect=[urllib.error.HTTPError('url',403,'limited',{'Retry-After':'120'},None),response]
        with patch.object(downloader.urllib.request,'build_opener',return_value=opener),patch.object(downloader.time,'sleep') as sleep:
            self.assertEqual(downloader.get('https://api.github.com/test','secret'),b'archive')
            self.assertGreaterEqual(sleep.call_args.args[0],120)
            sleep.reset_mock()
            opener.open.side_effect=urllib.error.HTTPError('url',404,'missing',{},None)
            with self.assertRaises(urllib.error.HTTPError): downloader.get('https://api.github.com/test','secret')
            sleep.assert_not_called()

    def test_collect_validates_task_and_protocol(self):
        m=manifest();task=runner.task_at(m,1,0)
        record=dict(task=task,protocol_id="test",makespan=100,cpu_seconds=1,wall_seconds=2)
        found=dict(completed={(1,0):dict(id=10)},active=set(),run_ids=[111])
        def download(command,stdout,**kwargs):
            self.assertEqual(command,["gh","api","repos/owner/repo/actions/artifacts/10/zip"])
            with ZipFile(stdout,"w") as archive:
                archive.writestr("task-0/completed.json",json.dumps(record))
                archive.writestr("../../unwanted.txt","must not extract")
        with tempfile.TemporaryDirectory() as raw:
            args=argparse.Namespace(bundle=Path(raw),repo="owner/repo",setup_run_id="123",output=Path(raw)/"collected")
            with patch.object(runner,"load",return_value=m),patch.object(actions,"inventory",return_value=found),patch.object(actions.subprocess,"run",side_effect=download):
                actions.collect(args)
            self.assertEqual(runner.read(args.output/"task-0/completed.json"),record)
            self.assertFalse((Path(raw)/"unwanted.txt").exists())
            record["protocol_id"]="foreign"
            with patch.object(runner,"load",return_value=m),patch.object(actions,"inventory",return_value=found),patch.object(actions.subprocess,"run",side_effect=download):
                with self.assertRaisesRegex(ValueError,"Foreign completion"): actions.collect(args)

    def test_collect_reads_many_completions_from_one_log_archive(self):
        m=manifest();records={i:dict(task=runner.task_at(m,1,i),protocol_id="test",makespan=100+i,cpu_seconds=1,wall_seconds=2) for i in (0,1)}
        found=dict(completed={(1,i):dict(id=10+i,source_run_id=111,source_status="completed") for i in records},
                   active=set(),run_ids=[111],runs=[dict(id=111,attempts=1,status="completed")])
        def logs(command,stdout,**kwargs):
            self.assertEqual(command,["gh","api","repos/owner/repo/actions/runs/111/attempts/1/logs"])
            text=""
            for i,record in records.items():
                packed=base64.b64encode(zlib.compress(json.dumps(dict(artifact_id=10+i,record=record)).encode())).decode()
                text+="2026-11-12T00:00:00Z PAPER_T678_COMPLETION:"+packed+"\n"
            with ZipFile(stdout,"w") as archive: archive.writestr("job/export-completion.txt",text)
        with tempfile.TemporaryDirectory() as raw:
            args=argparse.Namespace(bundle=Path(raw),repo="owner/repo",setup_run_id="123",output=Path(raw)/"collected")
            with patch.object(runner,"load",return_value=m),patch.object(actions,"inventory",return_value=found),patch.object(actions.subprocess,"run",side_effect=logs) as gh:
                actions.collect(args)
            self.assertEqual(gh.call_count,1)
            for i,record in records.items(): self.assertEqual(runner.read(args.output/f"task-{i}/completed.json"),record)


if __name__=="__main__": unittest.main()
