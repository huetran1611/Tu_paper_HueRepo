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
    path=run/"campaign.json"
    if path.exists():
        manifest=json.loads(path.read_text())
        expected=(args.budget,args.parallel,args.seed,args.smoke)
        actual=(manifest["budget"],manifest["parallel"],manifest["irace_seed"],manifest["smoke"])
        if actual!=expected: raise ValueError("Resume must use the frozen budget/parallel/seed/mode")
        for name,digest in manifest["artifact_sha256"].items():
            if sha(run/"artifacts"/name)!=digest: raise ValueError(f"Frozen artifact changed: {name}")
        if sha(Path(manifest["training_dir"])/"manifest.json")!=manifest["training_manifest_sha256"]:
            raise ValueError("Training manifest changed")
        return manifest
    training=ROOT/"experiments/irace_v3/training"
    report=json.loads((training/"validation_report.json").read_text())
    if report["status"]!="PASS" or not report["byte_reproducibility_checked"]:
        raise ValueError("Validate training before launch")
    training_manifest=json.loads((training/"manifest.json").read_text())
    selection=args.training_selection.resolve()
    names=selection.read_text().splitlines()
    available={entry["name"]+".txt":entry for entry in training_manifest["instances"]}
    if len(names)!=len(set(names)) or any(name not in available for name in names):
        raise ValueError("Expected distinct, validated training instances")
    counts={n:sum(available[name]["n"]==n for name in names) for n in (50,100,200)}
    if counts not in ({50:8,100:8,200:1},{50:8,100:8,200:4}):
        raise ValueError("Training selection must be 8-8-1 or 8-8-4")
    if counts[200]==4 and {available[name]['grid_miles'] for name in names if available[name]['n']==200}!={10,20,30,40}:
        raise ValueError("Four n200 instances must cover grids 10,20,30,40")
    for instance in names:
        for filename,digest in available[instance]['files'].items():
            if sha(training/'instances'/filename)!=digest: raise ValueError(f'Training file changed: {filename}')
    artifacts=run/"artifacts";artifacts.mkdir(parents=True,exist_ok=True)
    solver_source=args.solver_artifacts_from.resolve() if args.solver_artifacts_from else ROOT/'src_v3'
    files=[solver_source/"tabubu_TimeDependent_v3.exe",solver_source/"tabubu_TimeDependent_v3.cpp",
           solver_source/"paper_neighborhoods.hpp",HERE/"parameters.txt",HERE/"target_runner.py",
           HERE/"publish_checkpoint.py",HERE/"run_irace.R",HERE/"launch_irace.py"]
    for source in files: shutil.copy2(source,artifacts/source.name)
    shutil.copy2(args.initial_configurations,artifacts/'initial_configurations.txt')
    if args.seed_reuse_schedule:
        shutil.copy2(args.seed_reuse_schedule,artifacts/'seed_reuse_schedule.json')
        import csv
        schedule=json.loads(args.seed_reuse_schedule.read_text())
        with (artifacts/'seed_reuse_schedule.csv').open('w',newline='') as stream:
            writer=csv.writer(stream);writer.writerow(['instance','occurrence','seed'])
            for instance,seeds in schedule.items():
                for occurrence,seed in enumerate(seeds,1): writer.writerow([instance,occurrence,seed])
    shutil.copy2(training/"manifest.json",artifacts/"training_manifest.json")
    shutil.copy2(selection,artifacts/"train_instances.txt")
    manifest={"schema_version":1,"budget":args.budget,"parallel":args.parallel,"irace_seed":args.seed,
              "smoke":args.smoke,"irace_version":"4.5","training_dir":str(training),
              "training_manifest_sha256":sha(training/"manifest.json"),
              "selected_instance_names":names,"selected_instance_count":len(names),
              "training_files":{name:digest for instance in names for name,digest in available[instance]["files"].items()},
              "binary_name":"tabubu_TimeDependent_v3.exe","binary_sha256":sha(artifacts/"tabubu_TimeDependent_v3.exe"),
              "artifact_sha256":{p.name:sha(p) for p in artifacts.iterdir()},
              "process_caps_seconds":{"200":7200,"500":10800},
              "all_components_enabled":True,"created_unix":time.time()}
    manifest['training_counts']=counts
    manifest['cache_source_dirs']=[str(p.resolve()) for p in args.cache_source]
    for source in args.cache_source:
        old=json.loads((source/'campaign.json').read_text())
        if old['binary_sha256']!=manifest['binary_sha256'] or old['smoke']!=args.smoke:
            raise ValueError('Cache source solver/mode mismatch')
    manifest['seed_reuse_schedule']=bool(args.seed_reuse_schedule)
    manifest["sampling_policy"]={"t0":"log [0.001,0.5]", "cooling_rate":"log [0.0001,0.005]; alpha=1-cooling_rate",
                                  "eta_levels":[0,.1,.25,.5,.75,1,2], "digits":8}
    if args.smoke:
        manifest["test_iteration_override"]=1
        manifest["process_caps_seconds"]={"50":3,"100":3,"200":3,"500":3}
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
        status_path=run/"status.json"
        if status_path.exists():
            previous_status=json.loads(status_path.read_text())
            if previous_status["state"]=="stopped_by_user" and "optimiz" in previous_status.get("stop_reason",""):
                current_binary=ROOT/"src_v3/tabubu_TimeDependent_v3.exe"
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
    parser.add_argument("--run-dir",type=Path,default=HERE/"runs/log15000_20261112")
    parser.add_argument("--budget",type=int,default=15000)
    parser.add_argument("--parallel",type=int,default=20)
    parser.add_argument("--seed",type=int,default=20261112)
    parser.add_argument("--rscript",type=Path,default=RSCRIPT)
    parser.add_argument("--smoke",action="store_true")
    parser.add_argument("--check-only",action="store_true")
    parser.add_argument('--training-selection',type=Path,default=ROOT/'experiments/irace_v3/training/train_instances_17.txt')
    parser.add_argument('--cache-source',type=Path,action='append',default=[])
    parser.add_argument('--solver-artifacts-from',type=Path)
    parser.add_argument('--initial-configurations',type=Path,default=HERE/'initial_configurations.txt')
    parser.add_argument('--seed-reuse-schedule',type=Path)
    run_campaign(parser.parse_args())
