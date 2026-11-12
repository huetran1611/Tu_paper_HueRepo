"""Plan/resume/collect the 39 independent GitHub Actions batches.

Only called by explicitly dispatched workflows; uses GH_TOKEN from their environment.
"""
import argparse
import base64
import io
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
import zlib
from zipfile import ZipFile
import paper_tables_678 as batch


def gh_json(endpoint, data=None):
    command=["gh","api",endpoint]
    if data is not None: command += ["--method","POST","--input","-"]
    # Retry reads only: retrying an ambiguous POST could dispatch a duplicate run.
    for attempt in range(6):
        result=subprocess.run(command,input=json.dumps(data) if data is not None else None,
                              capture_output=True,text=True,timeout=120)
        if result.returncode == 0: break
        if data is not None or attempt == 5: result.check_returncode()
        print('GitHub read failed; backing off', flush=True)
        time.sleep(min(60 * 2**attempt, 900))
    return json.loads(result.stdout) if result.stdout.strip() else None


def pages(endpoint,key):
    page=1
    while True:
        separator="&" if "?" in endpoint else "?"
        result=gh_json(f"{endpoint}{separator}per_page=100&page={page}")[key]
        yield from result
        if len(result)<100: break
        page+=1


def job_finished(job):
    # GitHub can return stale status='in_progress' after finalizing a job.
    # A terminal conclusion together with completed_at is independent evidence.
    return job['status']=='completed' or bool(job.get('completed_at') and job.get('conclusion'))


def inventory(repo,setup_id,with_jobs=False,cache_path=None):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+",repo): raise ValueError("Invalid repository")
    if not re.fullmatch(r"[1-9][0-9]*",str(setup_id)): raise ValueError("Invalid setup run ID")
    setup=gh_json(f"repos/{repo}/actions/runs/{setup_id}")
    completed={};active=set();matched=[];runs=[];active_jobs=[];reserved=set();attempts={}
    memo=json.loads(cache_path.read_text()) if cache_path and cache_path.exists() else {}
    updated_memo={}
    # Setup-ID in run-name isolates campaigns, including their later resume runs.
    for run in pages(f"repos/{repo}/actions/runs?event=workflow_dispatch&created=%3E%3D{setup['created_at']}","workflow_runs"):
        match=re.search(r"paper-tables-678-batch-(\d\d)\.yml(?:@.*)?$",run.get("path",""))
        title=re.fullmatch(r'T678 batch (\d\d) setup-([1-9][0-9]*)(?: slots-([0-9,]+))?',run.get('display_title',''))
        if not match or not title or title[2]!=str(setup_id): continue
        number=int(match[1]);matched.append(run["id"])
        if int(title[1])!=number: raise ValueError('Run title and workflow batch differ')
        runs.append(dict(id=run["id"],batch=number,attempts=run.get("run_attempt",1),status=run["status"]))
        if run["status"]!="completed": active.add(number)
        stamp=[run.get('run_attempt',1),run['status'],run.get('updated_at')]
        cached=memo.get(str(run['id']),{})
        reuse=(run['status']=='completed' and cached.get('stamp')==stamp
               and time.time()-cached.get('fetched_unix',0)<21600)
        artifacts=cached['artifacts'] if reuse else list(pages(f"repos/{repo}/actions/runs/{run['id']}/artifacts","artifacts"))
        for artifact in artifacts:
            key=re.fullmatch(r"paper-t678-b(\d\d)-t(\d{3})-ok",artifact["name"])
            if not key or artifact.get("expired"): continue
            if artifact.get('expires_at') and artifact['expires_at'] <= time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()): continue
            pair=tuple(map(int,key.groups()))
            if pair[0]!=number: raise ValueError("Artifact batch does not match workflow")
            artifact={**artifact,"source_run_id":run["id"],"source_status":run["status"]}
            if pair not in completed or artifact["id"]>completed[pair]["id"]: completed[pair]=artifact
        jobs=[]
        if with_jobs:
            jobs=cached['jobs'] if reuse and 'jobs' in cached else list(pages(f"repos/{repo}/actions/runs/{run['id']}/jobs","jobs"))
            declared={(number,int(s)) for s in title[3].split(',')} if title[3] else set()
            if any(s<0 or s>=(100 if number==39 else 250) for b,s in declared): raise ValueError('Invalid reserved slot')
            listed=set()
            for job in jobs:
                key=re.search(r'batch-(\d+)-task-(\d+)\b',job['name'])
                if not key: raise ValueError('Unknown solver job; refusing to guess available capacity')
                pair=tuple(map(int,key.groups()))
                if pair[0]!=number: raise ValueError('Job batch mismatch')
                listed.add(pair)
                attempts[pair]=attempts.get(pair,0)+job.get('run_attempt',run.get('run_attempt',1))
                if not job_finished(job):
                    active_jobs.append(dict(pair=pair,id=job['id'],run_id=run['id']))
                elif run['status']!='completed' and job.get('conclusion')=='success' and pair not in completed:
                    reserved.add(pair) # Successful job whose artifact publication is not visible yet.
            if not jobs and not declared:
                # Legacy queued runs do not expose their input slots. Reserve the
                # entire batch until its matrix is visible rather than overfill.
                declared={(number,s) for s in range(100 if number==39 else 250)}
            for pair in declared-listed:
                attempts[pair]=attempts.get(pair,0)+run.get('run_attempt',1)
                if run['status']!='completed' and pair not in completed: reserved.add(pair)
        updated_memo[str(run['id'])]=dict(stamp=stamp,artifacts=artifacts,
                fetched_unix=cached['fetched_unix'] if reuse else time.time(),**({'jobs':jobs} if with_jobs else {}))
    if cache_path:
        cache_path.parent.mkdir(parents=True,exist_ok=True)
        batch.save(cache_path,updated_memo)
    return dict(setup=setup,completed=completed,active=active,run_ids=matched,runs=runs,
                active_jobs=active_jobs,reserved=reserved,attempts=attempts)


def plans(manifest,completed,active):
    if manifest.get("version")!=2: raise ValueError("Create a new version-2 setup; old shard bundles cannot be resumed here")
    expected={(t["batch"],t["slot"]) for t in manifest["tasks"]}
    if set(completed)-expected: raise ValueError("Unexpected batch/slot in artifact inventory")
    return [dict(batch=number,workflow=f"paper-tables-678-batch-{number:02d}.yml",
                 slots=[t["slot"] for t in manifest["tasks"] if t["batch"]==number and (number,t["slot"]) not in completed],
                 active=number in active)
            for number in range(1,manifest["batch_count"]+1)]


def plan(args):
    manifest=batch.load(args.bundle.resolve());found=inventory(args.repo,args.setup_run_id)
    pending=plans(manifest,found["completed"],found["active"])
    report=dict(setup_run_id=args.setup_run_id,protocol_id=manifest["protocol_id"],
                completed=len(found["completed"]),total=len(manifest["tasks"]),batches=pending,dispatched=[])
    args.output.mkdir(parents=True,exist_ok=True)
    batch.save(args.output/"launch_plan.json",report)
    if args.dispatch:
        # Setup asks the SAME serialized coordinator to allocate capacity; it
        # never bypasses reservations by directly sending a whole batch.
        gh_json(f"repos/{args.repo}/actions/workflows/paper-tables-678-coordinate.yml/dispatches",
                dict(ref=args.ref,inputs=dict(setup_run_id=str(args.setup_run_id))))
        report['coordinator_requested']=True
        batch.save(args.output/'launch_plan.json',report)
    lines=["# Tables 6–8: independent workflow plan", "",f"Setup run: {args.setup_run_id}",
           f"Completed artifacts: {report['completed']}/{report['total']}",f"Dispatched batches: {report['dispatched']}",
           f"Batches already active (not dispatched again): {sorted(found['active'])}","",
           "| Batch | Missing tasks | Workflow |", "|---|---:|---|"]
    lines += [f"| {p['batch']:02d} | {len(p['slots'])} | {p['workflow']} |" for p in pending]
    (args.output/"PLAN.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
    print(f"Completed {report['completed']}/{report['total']}; dispatched {len(report['dispatched'])} batches")


LEASE_SECONDS=600
MAX_OUTSTANDING=20


def reservation_pairs(repo,setup_id,exclude_artifact_id=None):
    """Leases are uploaded BEFORE dispatch, surviving crashes and API visibility lag."""
    result=gh_json(f'repos/{repo}/actions/artifacts?name=paper-t678-reservations-setup-{setup_id}&per_page=100')
    reserved=set()
    for artifact in result['artifacts']:
        if str(artifact['id'])==str(exclude_artifact_id): continue
        # ISO UTC ordering is stable. Only very recent preparation records matter.
        cutoff=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime(time.time()-LEASE_SECONDS))
        if artifact.get('expired') or artifact['created_at']<cutoff: continue
        raw=subprocess.run(['gh','api',f"repos/{repo}/actions/artifacts/{artifact['id']}/zip"],
                           capture_output=True,check=True,timeout=120).stdout
        with ZipFile(io.BytesIO(raw)) as archive:
            if archive.namelist()!=['scheduler-plan.json']: raise ValueError('Unexpected reservation archive')
            lease=json.loads(archive.read('scheduler-plan.json'))
        if str(lease['setup_run_id'])!=str(setup_id): raise ValueError('Foreign reservation')
        if time.time()-lease['created_unix']<LEASE_SECONDS:
            for pair in lease['pairs']:
                b,s=pair
                if not (1<=b<=39 and 0<=s<(100 if b==39 else 250)): raise ValueError('Invalid reservation')
                reserved.add((b,s))
    return reserved


def capacity_plan(found,leases,limit=MAX_OUTSTANDING):
    completed=set(found['completed'])
    active_jobs=found.get('active_jobs',[])
    active_pairs={tuple(j['pair']) for j in active_jobs}
    reserved=(set(found.get('reserved',set()))|set(leases))-completed
    # Count duplicate real jobs separately, but don't double-count their leases.
    occupied=len(active_jobs)+len(reserved-active_pairs)
    free=max(0,limit-occupied)
    pairs=[];paused=[]
    for i in range(9600):
        pair=(i//250+1,i%250)
        if pair in completed or pair in active_pairs or pair in reserved: continue
        if found.get('attempts',{}).get(pair,0)>=5:
            paused.append(pair);continue
        if len(pairs)<free: pairs.append(pair)
    return dict(occupied=occupied,free=free,pairs=pairs,paused=paused)


def coordinate(args):
    """Prepare a bounded wave, or apply an already uploaded reservation record."""
    own_run=os.environ.get('GITHUB_RUN_ID')
    if args.apply_plan:
        if not args.lease_artifact_id: raise ValueError('Upload the reservation artifact before dispatching')
        lease=gh_json(f'repos/{args.repo}/actions/artifacts/{args.lease_artifact_id}')
        if (str(lease.get('workflow_run',{}).get('id'))!=str(own_run)
                or lease['name']!=f'paper-t678-reservations-setup-{args.setup_run_id}' or lease.get('expired')):
            raise ValueError('Reservation artifact does not belong to this controller')
    found=inventory(args.repo,args.setup_run_id,with_jobs=True,cache_path=args.cache_file)
    leases=reservation_pairs(args.repo,args.setup_run_id,
                             exclude_artifact_id=args.lease_artifact_id if args.apply_plan else None)
    allocation=capacity_plan(found,leases)
    message=f"Completed {len(found['completed'])}/9600; outstanding {allocation['occupied']}/20. "
    if args.apply_plan:
        plan=json.loads(args.apply_plan.read_text())
        if str(plan['setup_run_id'])!=str(args.setup_run_id) or plan['ref']!=args.ref or plan['repo']!=args.repo:
            raise ValueError('Dispatch plan does not match campaign')
        if str(plan['controller_run_id'])!=str(own_run): raise ValueError('Plan belongs to another controller run')
        if time.time()-plan['created_unix']>=LEASE_SECONDS:
            message+='Reservation expired before dispatch; next controller will replan.'
        else:
            # Account for manual launches that appeared AFTER preparation.
            allowed=set(allocation['pairs'])
            selected=[tuple(p) for p in plan['pairs'] if tuple(p) in allowed][:allocation['free']]
            groups={}
            for b,s in selected: groups.setdefault(b,[]).append(s)
            for b,slots in groups.items():
                gh_json(f"repos/{args.repo}/actions/workflows/paper-tables-678-batch-{b:02d}.yml/dispatches",
                        dict(ref=args.ref,inputs=dict(setup_run_id=str(args.setup_run_id),
                             slots=json.dumps(slots,separators=(',',':')),reservation=','.join(map(str,slots)))))
            message+=f"Dispatched {len(selected)} slots across batches {list(groups)}."
    else:
        plan=dict(version=1,setup_run_id=str(args.setup_run_id),repo=args.repo,ref=args.ref,
                  controller_run_id=own_run,created_unix=time.time(),pairs=allocation['pairs'])
        batch.save(args.output,plan)
        message+=f"Prepared {len(plan['pairs'])} slots; {len(allocation['paused'])} slots paused after five attempts."
        if os.environ.get('GITHUB_OUTPUT'):
            with open(os.environ['GITHUB_OUTPUT'],'a') as stream: stream.write(f"count={len(plan['pairs'])}\n")
    print(message)
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'],'a',encoding='utf-8') as stream: stream.write(message+'\n')


def collect(args):
    manifest=batch.load(args.bundle.resolve());found=inventory(args.repo,args.setup_run_id)
    plans(manifest,found["completed"],found["active"]) # validate inventory before downloading
    args.output.mkdir(parents=True,exist_ok=True)
    batch.save(args.output/"inventory.json",dict(setup_run_id=args.setup_run_id,run_ids=found["run_ids"],
                active_batches=sorted(found["active"]),completed_artifacts=len(found["completed"])))
    def publish(pair,record):
        task=batch.task_at(manifest,*pair)
        if record["task"]!=task or record["protocol_id"]!=manifest["protocol_id"]: raise ValueError("Foreign completion record")
        batch.save(args.output/task["key"]/"completed.json",record)
    selected={a["id"]:(pair,a) for pair,a in found["completed"].items()}
    # One log archive per workflow attempt avoids 9600 REST archive requests.
    # Markers are emitted ONLY after the individual completion artifact uploads.
    def from_logs(run):
        if run["status"]!="completed": return set()
        received=set()
        for attempt in range(1,run["attempts"]+1):
            with tempfile.TemporaryDirectory() as raw:
                archive=Path(raw)/"logs.zip"
                try:
                    with archive.open("wb") as stream:
                        subprocess.run(["gh","api",f"repos/{args.repo}/actions/runs/{run['id']}/attempts/{attempt}/logs"],stdout=stream,check=True,timeout=180)
                except subprocess.CalledProcessError:
                    continue
                with ZipFile(archive) as zipped:
                    for name in zipped.namelist():
                        if not name.endswith(".txt"): continue
                        for packed in re.findall(rb"PAPER_T678_COMPLETION:([A-Za-z0-9+/=]+)",zipped.read(name)):
                            payload=json.loads(zlib.decompress(base64.b64decode(packed)))
                            identifier=payload["artifact_id"]
                            if identifier not in selected: continue
                            pair,artifact=selected[identifier]
                            if artifact["source_run_id"]!=run["id"]: raise ValueError("Completion log belongs to another workflow run")
                            if identifier not in received:
                                publish(pair,payload["record"]);received.add(identifier)
        return received
    with ThreadPoolExecutor(max_workers=4) as pool:
        received=set().union(*pool.map(from_logs,found.get("runs",[])))
    missing=[(pair,a) for pair,a in found["completed"].items() if a["id"] not in received and a.get("source_status","completed")=="completed"]
    if len(missing)>100:
        raise RuntimeError("Completion logs missing for more than 100 tasks; inspect workflow log retention/export instead of issuing thousands of REST downloads")
    # Rare fallback for a successful artifact whose final log export was interrupted.
    def download(item):
        pair,artifact=item;task=batch.task_at(manifest,*pair)
        with tempfile.TemporaryDirectory() as raw:
            archive=Path(raw)/"artifact.zip"
            with archive.open("wb") as stream:
                subprocess.run(["gh","api",f"repos/{args.repo}/actions/artifacts/{artifact['id']}/zip"],stdout=stream,check=True,timeout=180)
            with ZipFile(archive) as zipped:
                names=[n for n in zipped.namelist() if n.endswith("/completed.json") or n=="completed.json"]
                if len(names)!=1: raise ValueError("Successful task artifact must contain exactly one completion record")
                record=json.loads(zipped.read(names[0]))
            publish(pair,record)
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(download,missing))


def main():
    p=argparse.ArgumentParser(description=__doc__);s=p.add_subparsers(dest="command",required=True)
    for command in ("plan","collect"):
        q=s.add_parser(command);q.add_argument("--bundle",type=Path,required=True);q.add_argument("--setup-run-id",required=True)
        q.add_argument("--repo",default=os.environ.get("GITHUB_REPOSITORY"));q.add_argument("--output",type=Path,required=True)
        if command=="plan": q.add_argument("--dispatch",action="store_true");q.add_argument("--ref",default=os.environ.get("GITHUB_REF_NAME"))
    q=s.add_parser('coordinate');q.add_argument('--setup-run-id',required=True)
    q.add_argument('--repo',default=os.environ.get('GITHUB_REPOSITORY'))
    q.add_argument('--ref',default=os.environ.get('GITHUB_REF_NAME'))
    q.add_argument('--cache-file',type=Path,default=Path('.scheduler-cache/inventory.json'))
    q.add_argument('--output',type=Path,default=Path('scheduler-plan.json'))
    q.add_argument('--apply-plan',type=Path)
    q.add_argument('--lease-artifact-id',type=int)
    args=p.parse_args()
    if args.command=='coordinate':
        if not args.ref: raise ValueError('A ref is required')
        coordinate(args)
    elif args.command=="plan":
        if args.dispatch and not args.ref: raise ValueError("A ref is required to dispatch batches")
        plan(args)
    else: collect(args)


if __name__=="__main__": main()
