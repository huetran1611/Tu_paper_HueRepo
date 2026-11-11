"""Plan/resume/collect the 39 independent GitHub Actions batches.

Only called by explicitly dispatched workflows; uses GH_TOKEN from their environment.
"""
import argparse
import base64
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


def inventory(repo,setup_id):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+",repo): raise ValueError("Invalid repository")
    if not re.fullmatch(r"[1-9][0-9]*",str(setup_id)): raise ValueError("Invalid setup run ID")
    setup=gh_json(f"repos/{repo}/actions/runs/{setup_id}")
    completed={};active=set();matched=[];runs=[]
    # Setup-ID in run-name isolates campaigns, including their later resume runs.
    for run in pages(f"repos/{repo}/actions/runs?event=workflow_dispatch&created=%3E%3D{setup['created_at']}","workflow_runs"):
        match=re.search(r"paper-tables-678-batch-(\d\d)\.yml(?:@.*)?$",run.get("path",""))
        if not match or not run.get("display_title","").endswith(f"setup-{setup_id}"): continue
        number=int(match[1]);matched.append(run["id"])
        runs.append(dict(id=run["id"],batch=number,attempts=run.get("run_attempt",1),status=run["status"]))
        if run["status"]!="completed": active.add(number)
        for artifact in pages(f"repos/{repo}/actions/runs/{run['id']}/artifacts","artifacts"):
            key=re.fullmatch(r"paper-t678-b(\d\d)-t(\d{3})-ok",artifact["name"])
            if not key or artifact.get("expired"): continue
            pair=tuple(map(int,key.groups()))
            if pair[0]!=number: raise ValueError("Artifact batch does not match workflow")
            artifact={**artifact,"source_run_id":run["id"],"source_status":run["status"]}
            if pair not in completed or artifact["id"]>completed[pair]["id"]: completed[pair]=artifact
    return dict(setup=setup,completed=completed,active=active,run_ids=matched,runs=runs)


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
    for item in pending:
        if found['active']: break # Never start another batch while any campaign batch is active.
        if args.dispatch and item["slots"] and not item["active"]:
            gh_json(f"repos/{args.repo}/actions/workflows/{item['workflow']}/dispatches",
                    dict(ref=args.ref,inputs=dict(setup_run_id=str(args.setup_run_id),slots=json.dumps(item["slots"],separators=(",",":")))))
            report["dispatched"].append(item["batch"])
            batch.save(args.output/"launch_plan.json",report)
            break # The completion event advances the chain, not this loop.
    lines=["# Tables 6–8: independent workflow plan", "",f"Setup run: {args.setup_run_id}",
           f"Completed artifacts: {report['completed']}/{report['total']}",f"Dispatched batches: {report['dispatched']}",
           f"Batches already active (not dispatched again): {sorted(found['active'])}","",
           "| Batch | Missing tasks | Workflow |", "|---|---:|---|"]
    lines += [f"| {p['batch']:02d} | {len(p['slots'])} | {p['workflow']} |" for p in pending]
    (args.output/"PLAN.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
    print(f"Completed {report['completed']}/{report['total']}; dispatched {len(report['dispatched'])} batches")


def coordinate(args):
    """Short, idempotent controller. Never waits for a solver or downloads its bundle."""
    found=inventory(args.repo,args.setup_run_id)
    manifest=dict(version=2,batch_count=39,tasks=[dict(batch=i//250+1,slot=i%250) for i in range(9600)])
    pending=plans(manifest,found['completed'],found['active'])
    message=f"Completed {len(found['completed'])}/9600. "
    if found['active']:
        message+=f"Waiting for active batches {sorted(found['active'])}; no dispatch."
    else:
        item=next((p for p in pending if p['slots']),None)
        if item is None: message+='Campaign complete.'
        elif sum(r.get('batch')==item['batch'] for r in found['runs']) >= 5:
            message+=f"Paused: batch {item['batch']} reached five runs; inspect errors before manual resume."
        else:
            gh_json(f"repos/{args.repo}/actions/workflows/{item['workflow']}/dispatches",
                    dict(ref=args.ref,inputs=dict(setup_run_id=str(args.setup_run_id),slots=json.dumps(item['slots'],separators=(',',':')))))
            message+=f"Dispatched batch {item['batch']} with {len(item['slots'])} missing slots."
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
    args=p.parse_args()
    if args.command=='coordinate':
        if not args.ref: raise ValueError('A ref is required')
        coordinate(args)
    elif args.command=="plan":
        if args.dispatch and not args.ref: raise ValueError("A ref is required to dispatch batches")
        plan(args)
    else: collect(args)


if __name__=="__main__": main()
