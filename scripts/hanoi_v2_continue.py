"""Gate all remaining V2 batches on a technically valid pilot, without local daemons."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import importlib
import json
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib.parse import urlencode


def api(repo, endpoint):
    process = subprocess.run(["gh", "api", f"repos/{repo}/{endpoint}"],
                             capture_output=True, text=True, check=True, timeout=60)
    return json.loads(process.stdout)


def pages(repo, endpoint, field):
    for page in range(1, 1001):
        separator = "&" if "?" in endpoint else "?"
        values = api(repo, endpoint+f"{separator}per_page=100&page={page}")[field]
        yield from values
        if len(values) < 100:
            return
    raise RuntimeError("Unexpected pagination size")


def run(args):
    args.capsule = args.capsule.resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    state = {"setup_run_id":args.setup_run_id, "pilot_run_id":args.pilot_run_id,
        "branch":args.ref, "status":"waiting_for_pilot", "submitted":[], "existing":[],
        "gating_uses_test_outcomes":False, "started_utc":datetime.now(timezone.utc).isoformat()}
    def checkpoint():
        path=args.output/"launch_state.json"
        temporary=path.with_suffix(".tmp.json")
        temporary.write_text(json.dumps(state,indent=2)+"\n")
        temporary.replace(path)
    checkpoint()
    try:
        sys.path.insert(0,str(args.capsule/"runtime/scripts"))
        v2=importlib.import_module("hanoi_v2_experiment")
        full,manifest=v2.load(args.capsule)
        assert manifest["experiment_id"]==args.setup_run_id
        counts=Counter(t["owner"] for t in manifest["tasks"])
        plan=[(group,batch) for group in ("PI","P0","monday","saturday","sunday")
              for batch in range(1,(counts[group]+249)//250+1)]
        assert len(plan)==77 and len(manifest["tasks"])==18400
        deadline=time.monotonic()+args.wait_minutes*60
        while True:
            pilot=api(args.repo,f"actions/runs/{args.pilot_run_id}")
            assert pilot["head_branch"]==args.ref and pilot["head_sha"]==manifest["commit"]
            if pilot["status"]=="completed":
                if pilot["conclusion"]!="success":
                    raise RuntimeError("Pilot did not succeed; no remaining batches may launch")
                break
            if time.monotonic()>deadline:
                raise TimeoutError("Pilot still incomplete; no remaining batches launched")
            print("Waiting for pilot",args.pilot_run_id,pilot["status"],flush=True)
            time.sleep(30)
        name=(f"hanoi-v2-{args.setup_run_id}-batch-monday-1-run-{args.pilot_run_id}"
              f"-a{pilot['run_attempt']}")
        available=[a for a in pages(args.repo,f"actions/runs/{args.pilot_run_id}/artifacts","artifacts")
                   if a["name"]==name and not a["expired"]]
        assert len(available)==1,"Missing or ambiguous packed pilot artifact"
        packed=args.output/"pilot_batch"
        subprocess.run(["gh","run","download",args.pilot_run_id,"--repo",args.repo,
                        "--name",name,"--dir",str(packed)],check=True,timeout=300)
        manifest["_capsule"]=args.capsule
        expected={t["key"]:t for t in full.batch_tasks(manifest["tasks"],1,"monday")}
        records,report=v2.collect(full,manifest,expected,packed)
        assert len(expected)==len(records)==250
        assert not any(report[k] for k in ("missing","conflicting_retries","invalid"))
        coverage=json.loads((packed/"batch_coverage.json").read_text())
        assert not any(coverage[k] for k in ("missing","conflicting_retries","invalid"))
        state.update(status="pilot_validated",pilot_valid_records=250,protocol_id=manifest["protocol_id"])
        checkpoint()
        print("Pilot validated: 250 complete records, exact protocol, 9000 iterations and embedded routes/events",flush=True)
        # Find existing batch runs by their fixed, explicit run title. Never choose by outcomes.
        def existing_runs():
            query=urlencode({"event":"workflow_dispatch","branch":args.ref,
                             "created":">="+manifest["created_at"][:10]})
            pattern=re.compile(r"^Hanoi V2 - batch - (PI|P0|monday|saturday|sunday) - batch (\d+) - setup "+re.escape(args.setup_run_id)+r"$")
            result={}
            endpoint="actions/workflows/hanoi-v2.yml/runs?"+query
            for item in pages(args.repo,endpoint,"workflow_runs"):
                match=pattern.fullmatch(item["display_title"])
                if match:
                    key=(match[1],int(match[2]))
                    if key in result and result[key]["id"]!=item["id"]:
                        raise RuntimeError(f"Conflicting existing runs for {key}; human review required")
                    result[key]=item
            return result
        existing=existing_runs()
        assert existing.get(("monday",1),{}).get("id")==int(args.pilot_run_id)
        for group,batch in plan:
            if (group,batch) in existing:
                item=existing[group,batch]
                state["existing"].append({"group":group,"batch":batch,"run_id":item["id"],"url":item["html_url"]})
                checkpoint()
                continue
            state["status"]="dispatching_remaining_batches"
            checkpoint()
            process=subprocess.run(["gh","workflow","run","hanoi-v2.yml","--repo",args.repo,"--ref",args.ref,
                "-f","mode=batch","-f",f"setup_run_id={args.setup_run_id}","-f",f"group={group}",
                "-f",f"batch={batch}","-f",f"max_parallel={args.max_parallel}"],
                text=True,capture_output=True,timeout=90)
            if process.returncode:
                raise RuntimeError(f"Dispatch {group}/{batch} failed or uncertain: {process.stderr}; check remote runs before resuming")
            match=re.search(r"actions/runs/(\d+)",process.stdout+process.stderr)
            state["submitted"].append({"group":group,"batch":batch,
                "run_id":int(match[1]) if match else None,"dispatch_response":process.stdout.strip()})
            checkpoint()
            print("DISPATCHED",group,batch,process.stdout.strip(),flush=True)
            time.sleep(1)
        for attempt in range(12):
            observed=existing_runs()
            if set(plan).issubset(observed):
                break
            time.sleep(5)
        else:
            raise RuntimeError("Some accepted dispatches are not yet visible; review remote runs before resuming")
        state.update(status="all_77_batches_dispatched",expected_batches=77,
            newly_submitted=len(state["submitted"]),
            runs=[{"group":g,"batch":b,"run_id":v["id"],"url":v["html_url"]}
                  for (g,b),v in sorted(observed.items())],
            finished_utc=datetime.now(timezone.utc).isoformat(),
            note="Dispatch completion is not experiment completion; aggregate only after all batches finish")
        checkpoint()
        print("DONE",state["status"],"newly submitted",state["newly_submitted"],flush=True)
    except Exception as error:
        state.update(status="stopped_needs_attention",error=str(error))
        checkpoint()
        raise


def main():
    parser=argparse.ArgumentParser(__doc__)
    parser.add_argument("--capsule",type=Path,required=True)
    parser.add_argument("--setup-run-id",required=True)
    parser.add_argument("--pilot-run-id",required=True)
    parser.add_argument("--ref",required=True)
    parser.add_argument("--repo",required=True)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--wait-minutes",type=int,default=120)
    parser.add_argument("--max-parallel",type=int,default=20)
    run(parser.parse_args())


if __name__=="__main__":main()
