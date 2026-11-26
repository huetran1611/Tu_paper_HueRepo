"""Start/resume the independent log-sampling campaign, 15000 runs, 20 workers."""
from __future__ import annotations
import argparse
import ctypes
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from target_runner import atomic_json,decode,sha

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
RSCRIPT=Path("C:/Program Files/R/R-4.6.1/bin/Rscript.exe")


def publish_status(path: Path, data: dict) -> None:
    # Windows readers/antivirus may briefly hold the destination open.
    # A status update must never terminate the running experiment.
    for attempt in range(10):
        try:
            atomic_json(path,data)
            return
        except PermissionError:
            if attempt<9: time.sleep(.1)
    print("Status file is temporarily locked; retrying on the next poll.",flush=True)


def pid_alive(pid: int) -> bool:
    if os.name != "nt":
        try: os.kill(pid,0); return True
        except ProcessLookupError: return False
    kernel=ctypes.WinDLL("kernel32",use_last_error=True)
    kernel.OpenProcess.restype=ctypes.c_void_p
    handle=kernel.OpenProcess(0x1000,False,pid)
    if not handle: return False
    code=ctypes.c_ulong()
    kernel.GetExitCodeProcess.argtypes=[ctypes.c_void_p,ctypes.POINTER(ctypes.c_ulong)]
    kernel.CloseHandle.argtypes=[ctypes.c_void_p]
    ok=kernel.GetExitCodeProcess(handle,ctypes.byref(code))
    kernel.CloseHandle(handle)
    return bool(ok and code.value==259)


def create_process_job(process: subprocess.Popen):
    """Supervisor exit/crash closes the job, terminating only its descendants."""
    if os.name != "nt": return None
    from ctypes import wintypes as w
    class Basic(ctypes.Structure):
        _fields_=[("process_time",ctypes.c_longlong),("job_time",ctypes.c_longlong),("flags",w.DWORD),
                  ("minimum",ctypes.c_size_t),("maximum",ctypes.c_size_t),("active",w.DWORD),
                  ("affinity",ctypes.c_size_t),("priority",w.DWORD),("scheduling",w.DWORD)]
    class IO(ctypes.Structure):
        _fields_=[(name,ctypes.c_ulonglong) for name in ("read","write","other","read_bytes","write_bytes","other_bytes")]
    class Extended(ctypes.Structure):
        _fields_=[("basic",Basic),("io",IO),("process_memory",ctypes.c_size_t),("job_memory",ctypes.c_size_t),
                  ("peak_process",ctypes.c_size_t),("peak_job",ctypes.c_size_t)]
    kernel=ctypes.WinDLL("kernel32",use_last_error=True)
    kernel.CreateJobObjectW.argtypes=[ctypes.c_void_p,w.LPCWSTR];kernel.CreateJobObjectW.restype=w.HANDLE
    kernel.SetInformationJobObject.argtypes=[w.HANDLE,ctypes.c_int,ctypes.c_void_p,w.DWORD]
    kernel.AssignProcessToJobObject.argtypes=[w.HANDLE,w.HANDLE]
    kernel.CloseHandle.argtypes=[w.HANDLE]
    handle=kernel.CreateJobObjectW(None,None)
    info=Extended();info.basic.flags=0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not handle or not kernel.SetInformationJobObject(handle,9,ctypes.byref(info),ctypes.sizeof(info)) or not kernel.AssignProcessToJobObject(handle,int(process._handle)):
        process.kill();process.wait()
        raise ctypes.WinError(ctypes.get_last_error())
    return handle,kernel


def latest_recovery(run: Path, rscript: Path, env: dict) -> Path | None:
    candidates=sorted((run/"checkpoints").glob("irace-*.Rdata"),reverse=True)
    if (run/"irace.Rdata").exists(): candidates.append(run/"irace.Rdata")
    code="a<-commandArgs(TRUE); e<-new.env(); load(a[1],e); if(!exists('iraceResults',e) || is.null(e$iraceResults$state)) quit(status=2)"
    for path in candidates:
        result=subprocess.run([str(rscript),"--vanilla","-e",code,str(path)],capture_output=True,env=env)
        if result.returncode==0: return path
    return None


def prepare(args, run: Path) -> dict:
    path=run/'campaign.json'
    if path.exists():
        manifest=json.loads(path.read_text())
        if (args.budget,args.parallel,args.seed,args.smoke)!=(manifest['budget'],manifest['parallel'],manifest['irace_seed'],manifest['smoke']):
            raise ValueError('Frozen budget/parallel/seed/mode mismatch')
        for name,digest in manifest['artifact_sha256'].items():
            if sha(run/'artifacts'/name)!=digest: raise ValueError('Frozen artifact changed: '+name)
        if sha(Path(manifest['training_dir'])/'manifest.json')!=manifest['training_manifest_sha256']:
            raise ValueError('Training manifest changed')
        return manifest
    training=HERE/'training'
    tm=json.loads((training/'manifest.json').read_text())
    names=[entry['name']+'.txt' for entry in tm['instances']]
    if len(names)!=10 or any(entry['n']!=100 for entry in tm['instances']):
        raise ValueError('Expected ten n100 training instances')
    files={name:digest for entry in tm['instances'] for name,digest in entry['files'].items()}
    for name,digest in files.items():
        if sha(training/'instances'/name)!=digest: raise ValueError('Training input changed: '+name)
    artifacts=run/'artifacts';artifacts.mkdir(parents=True,exist_ok=True)
    for source in [ROOT/'src_v4'/name for name in ('tabubu_TimeDependent_v4.cpp','tabubu_TimeDependent_v4.exe','paper_neighborhoods.hpp')]+[HERE/name for name in ('domains.json','parameters.txt','initial_configurations.txt','target_runner.py','publish_checkpoint.py','run_irace.R','launch_irace.py')]:
        shutil.copy2(source,artifacts/source.name)
    shutil.copy2(training/'manifest.json',artifacts/'training_manifest.json')
    (artifacts/'train_instances.txt').write_text('\n'.join(names)+'\n')
    manifest=dict(schema_version=1,budget=args.budget,parallel=args.parallel,irace_seed=args.seed,
        smoke=args.smoke,irace_version='4.5',training_dir=str(training),
        training_manifest_sha256=sha(training/'manifest.json'),training_files=files,
        selected_instance_names=names,selected_instance_count=len(names),training_counts={'100':10},
        binary_name='tabubu_TimeDependent_v4.exe',binary_sha256=sha(artifacts/'tabubu_TimeDependent_v4.exe'),
        artifact_sha256={p.name:sha(p) for p in artifacts.iterdir()},
        process_caps_seconds={'200':7200,'500':10800},all_components_enabled=True,
        sampling_policy=json.loads((HERE/'domains.json').read_text()),created_unix=time.time())
    if args.smoke:
        manifest['test_iteration_override']=1
        manifest['process_caps_seconds']={'100':10}
    atomic_json(path,manifest)
    return manifest


def run_campaign(args) -> None:
    run=args.run_dir.resolve();run.mkdir(parents=True,exist_ok=True)
    lock=run/"supervisor.lock"
    if lock.exists():
        previous=json.loads(lock.read_text())
        if pid_alive(previous["pid"]): raise RuntimeError(f"Campaign already supervised by PID {previous['pid']}")
        lock.rename(run/f"stale-supervisor-{time.time_ns()}.json")
    descriptor=os.open(lock,os.O_WRONLY|os.O_CREAT|os.O_EXCL)
    with os.fdopen(descriptor,"w") as f:
        json.dump({"pid":os.getpid(),"time_unix":time.time()},f);f.flush();os.fsync(f.fileno())
    process=None;job=None
    try:
        manifest=prepare(args,run)
        if args.prepare_only:
            publish_status(run/'status.json',dict(state='prepared',supervisor_pid=0,parallel=args.parallel,budget=args.budget,updated_unix=time.time()))
            return
        status_path=run/"status.json"
        if status_path.exists():
            previous_status=json.loads(status_path.read_text())
            if previous_status["state"]=="stopped_by_user" and "optimiz" in previous_status.get("stop_reason",""):
                current_binary=ROOT/"src_v4/tabubu_TimeDependent_v4.exe"
                if sha(current_binary)!=manifest["binary_sha256"]:
                    raise RuntimeError("This campaign was stopped for optimization and pins the old binary. Choose a NEW --run-dir for the optimized solver; saved logs/checkpoints remain intact.")
        if status_path.exists() and (run/"best_configuration.json").exists() and not args.check_only:
            if json.loads(status_path.read_text())["state"]=="finished":
                print("This campaign is already complete; saved results are unchanged.",flush=True)
                return
        env=os.environ.copy()
        env.update({"LC_ALL":"C","LANG":"C","OMP_NUM_THREADS":"1","OPENBLAS_NUM_THREADS":"1","MKL_NUM_THREADS":"1"})
        recovery=latest_recovery(run,args.rscript,env)
        command=[str(args.rscript),"--vanilla",str(run/"artifacts/run_irace.R"),"--run-dir",str(run),
                 "--python",sys.executable,"--training-dir",manifest["training_dir"],
                 "--budget",str(args.budget),"--parallel",str(args.parallel),"--seed",str(args.seed)]
        if recovery: command.extend(["--recovery",str(recovery)])
        if args.smoke: command.extend(["--smoke","true"])
        if args.check_only: command.extend(["--check-only","true"])
        print(f"Run directory: {run}\nBudget: {args.budget}; parallel: {args.parallel}",flush=True)
        print(f"Recovery: {recovery or 'restart same seed; reuse completed evaluation cache'}",flush=True)
        for recovery_attempt in range(2):
            log=run/f"irace-console-{time.time_ns()}.log"
            with log.open("w") as output:
                process=subprocess.Popen(command,cwd=run,stdout=output,stderr=subprocess.STDOUT,env=env)
                job=create_process_job(process)
                while True:
                    code=process.poll()
                    completed=list((run/"evaluations").glob("*/completed.json"))
                    publish_status(run/"status.json",{"state":"running" if code is None else "finished" if code==0 else "failed",
                                "supervisor_pid":os.getpid(),"irace_pid":process.pid,"parallel":args.parallel,
                                "budget":args.budget,"completed_cached_evaluations":len(completed),
                                "console_log":log.name,"recovery_file":str(recovery) if recovery else None,
                                "recovery_method":"irace_state" if recovery else "seeded_cache_replay",
                                "updated_unix":time.time(),"exit_code":code})
                    if code is not None: break
                    time.sleep(5)
            if job is not None:
                job[1].CloseHandle(job[0]);job=None
            text=log.read_text(errors="replace")
            if code and recovery and recovery_attempt==0 and ("Recovery terminated" in text or "Cannot find the following in recovery info" in text):
                print("irace checkpoint has an incomplete parallel batch; replaying the same seed with completed-result cache.",flush=True)
                at=command.index("--recovery")
                del command[at:at+2]
                recovery=None
                continue
            break
        if code: raise RuntimeError(f"irace exited {code}; inspect {log}")
        csv=run/"elite_configurations_units.csv"
        if csv.exists():
            import csv as csv_module
            with csv.open(newline="") as f: rows=list(csv_module.DictReader(f))
            if rows:
                from target_runner import RANGES,REAL_PARAMETERS
                best=decode({name:(float(rows[0][name]) if name in REAL_PARAMETERS else int(float(rows[0][name]))) for name in RANGES})
                atomic_json(run/"best_configuration.json",{"parameters":best,"source":csv.name,"training_only":True})
        print("Campaign finished. Results and recovery checkpoints are saved.",flush=True)
    finally:
        if process is not None and process.poll() is None:
            process.terminate();process.wait()
        if job is not None: job[1].CloseHandle(job[0])
        lock.unlink(missing_ok=True)


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir",type=Path,default=HERE/"runs/ordinal3000_n100_20261121")
    parser.add_argument("--budget",type=int,default=3000)
    parser.add_argument("--parallel",type=int,default=20)
    parser.add_argument("--seed",type=int,default=20261121)
    parser.add_argument("--rscript",type=Path,default=RSCRIPT)
    parser.add_argument("--smoke",action="store_true")
    parser.add_argument("--check-only",action="store_true")
    parser.add_argument("--prepare-only",action="store_true")
    parser.add_argument('--training-selection',type=Path,default=ROOT/'experiments/irace_v3/training/train_instances_17.txt')
    parser.add_argument('--cache-source',type=Path,action='append',default=[])
    parser.add_argument('--solver-artifacts-from',type=Path)
    parser.add_argument('--initial-configurations',type=Path,default=HERE/'initial_configurations.txt')
    parser.add_argument('--seed-reuse-schedule',type=Path)
    run_campaign(parser.parse_args())
