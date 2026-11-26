"""Durable Windows runner for Tables 6--8; one solver per task, bounded parallelism."""
import argparse
import ctypes
from ctypes import wintypes
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid
from zipfile import ZipFile

import paper_tables_678 as design

ROOT=next(p for p in Path(__file__).resolve().parents if (p/'experiments/irace_v3_log/launch_irace.py').exists())
sys.path.insert(0,str(ROOT/'experiments/irace_v3_log'))
from launch_irace import create_process_job,pid_alive,publish_status

V4_IRACE = "irace-v4"
V4_POLICIES = {"paper-v4", V4_IRACE}
V4_WINNER = ROOT / "experiments/irace_v4/runs/ordinal3000_n100_20261121/best_configuration.json"


def cpu_seconds(process):
    """User+kernel CPU of this process, independent of other concurrent solvers."""
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.GetProcessTimes.argtypes=[wintypes.HANDLE]+[ctypes.POINTER(wintypes.FILETIME)]*4
    kernel.GetProcessTimes.restype=wintypes.BOOL
    created,exited,kernel_time,user_time=(wintypes.FILETIME() for _ in range(4))
    if not kernel.GetProcessTimes(int(process._handle),ctypes.byref(created),ctypes.byref(exited),ctypes.byref(kernel_time),ctypes.byref(user_time)):
        raise ctypes.WinError(ctypes.get_last_error())
    value=lambda t:(t.dwHighDateTime<<32)|t.dwLowDateTime
    return (value(kernel_time)+value(user_time))/10_000_000


def prepare(args):
    run=args.run_dir.resolve();bundle=run/'bundle'
    if (bundle/'manifest.json').exists():
        manifest = load(bundle)
        if manifest["algorithm_policy"] != args.algorithm_policy:
            raise ValueError("The run directory contains a different algorithm policy")
        return manifest
    bundle.mkdir(parents=True,exist_ok=True)
    paper_v4 = args.algorithm_policy in V4_POLICIES
    parameters=design.read(args.parameters)['parameters']
    if args.algorithm_policy == V4_IRACE:
        if parameters["h_mode"] + parameters.pop("h_gap") != parameters["h_div"]:
            raise ValueError("h_div does not equal h_mode + h_gap")
    expected_parameters = set(design.PARAMETERS) | ({"rho"} if paper_v4 else set())
    if set(parameters) != expected_parameters:
        raise ValueError("Parameter file does not match the algorithm policy")
    source=args.source_dir.resolve()
    revision = "v4" if paper_v4 else "v3"
    cpp_path = source / f"tabubu_TimeDependent_{revision}.cpp"
    hpp_path = source / "paper_neighborhoods.hpp"
    cpp,hpp=design.adapt(cpp_path.read_text(encoding='utf-8'),
                         hpp_path.read_text(encoding='utf-8'), paper_v4=paper_v4)
    adapted=bundle/'source';adapted.mkdir()
    (adapted/'solver.cpp').write_text(cpp,encoding='utf-8')
    (adapted/'paper_neighborhoods.hpp').write_text(hpp,encoding='utf-8')
    binary=bundle/'solver.exe'
    subprocess.run([args.compiler,'-std=c++17','-O2','-Wall','-Wextra',str(adapted/'solver.cpp'),'-o',str(binary)],check=True)
    shutil.copy2(ROOT/'.github/workload-tiebreak/inputs.zip',bundle/'inputs.zip')
    shutil.copy2(ROOT/'scripts/paper_tables_678.py',bundle/'paper_tables_678.py')
    shutil.copy2(__file__,bundle/'local_runner.py')
    shutil.copy2(args.parameters,bundle/'selected_configuration.json')
    instances=design.read(ROOT/'.github/workload-tiebreak/inputs.json')['instances']
    if len(instances)!=96: raise ValueError('Expected 96 held-out test instances')
    with ZipFile(bundle/'inputs.zip') as archive:
        for inst in instances:
            for f in inst['files']:
                if hashlib.sha256(archive.read(f['path'])).hexdigest()!=f['sha256']: raise ValueError('Input hash mismatch')
    tasks=[]
    for inst in instances:
        for name,v in design.VARIANTS.items():
            for seed in range(42,52):
                tasks.append(dict(key=f"{inst['instance']}-{name}-s{seed}",instance=inst['instance'],variant=name,seed=seed,
                                  n=inst['customers'],iterations=9*inst['customers']*math.ceil(math.sqrt(inst['customers']))*v['multiplier']))
    for index,task in enumerate(tasks): task.update(batch=index//250+1,slot=index%250)
    m=dict(version=2,algorithm_policy=args.algorithm_policy,parameters=parameters,instances=instances,seeds=list(range(42,52)),
           compiler_directory=str(Path(args.compiler).resolve().parent),
           vehicle_parameters=dict(truck_capacity=70,drone_capacity=2.27),
           variants=design.VARIANTS,tables=design.TABLES,tasks=tasks,batch_size=250,batch_count=39,
           binary='solver.exe',binary_sha256=design.sha(binary),inputs_zip_sha256=design.sha(bundle/'inputs.zip'),
           runner_sha256=design.sha(bundle/'paper_tables_678.py'),
           local_artifacts_sha256={p.relative_to(bundle).as_posix():design.sha(p) for p in bundle.rglob('*') if p.is_file()},
           original_source_sha256={p.name:design.sha(p) for p in (cpp_path, hpp_path)},
           process_caps=({'200':7200} if args.algorithm_policy == V4_IRACE
                         else {} if paper_v4 else {'default':7200,'200':7200}),
           time_metric='Windows GetProcessTimes: solver user+kernel CPU',
           created_unix=time.time())
    if args.algorithm_policy == V4_IRACE:
        m["doubled_process_caps"] = {"200": 14400}
    m['protocol_id']=hashlib.sha256(json.dumps(m,sort_keys=True).encode()).hexdigest()
    design.save(bundle/'manifest.json',m)
    return m


def load(bundle):
    m=design.load(bundle)
    for name,digest in m['local_artifacts_sha256'].items():
        if design.sha(bundle/name)!=digest: raise ValueError('Frozen artifact changed: '+name)
    return m


def process_cap(manifest, task):
    size = str(task["n"])
    caps = manifest["process_caps"]
    if manifest["variants"][task["variant"]]["multiplier"] == 2:
        doubled = manifest.get("doubled_process_caps", {}).get(size)
        if doubled is not None:
            return doubled
    return caps.get(size, caps.get("default"))


def launch(run,m,task):
    directory=run/'results'/task['key'];directory.mkdir(parents=True,exist_ok=True)
    attempt=directory/('a-'+uuid.uuid4().hex[:10]);attempt.mkdir()
    result_path=attempt/'incumbent.json'
    data=run/'inputs'
    instance=data/(task['instance']+'.txt')
    cap=process_cap(m, task)
    segment=max(1,math.ceil(m['parameters']['cseg']*task['n']-1e-12))
    command=[str(run/'bundle'/m['binary']),str(instance),'--attempts=1',f"--seed={task['seed']}",
             f"--iters={task['iterations']}",f'--segment-iters={segment}',
             f"--truck-capacity={m['vehicle_parameters']['truck_capacity']}",
             f"--drone-capacity={m['vehicle_parameters']['drone_capacity']}",
             '--truck-vmax-file='+str(instance.with_name(instance.stem+'.vmax_ij.txt')),
             '--truck-theta-file='+str(instance.with_name(instance.stem+'.theta_ijl.txt')),'--result-json='+str(result_path)]
    if cap is not None:
        command.append(f'--process-time-limit={cap-2}')
    paper_v4 = m["algorithm_policy"] in V4_POLICIES
    for name,value in m['parameters'].items():
        if name!='cseg' or paper_v4: command.append('--'+name.replace('_','-')+'='+str(value))
    env=os.environ.copy();env.update(PAPER_VARIANT=task['variant'],OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1')
    env['PATH']=m['compiler_directory']+os.pathsep+env.get('PATH','')
    design.save(attempt/'request.json',dict(protocol_id=m['protocol_id'],task=task,command=command,started_unix=time.time()))
    started = time.monotonic()
    with (attempt/'stdout.log').open('w') as out,(attempt/'stderr.log').open('w') as err:
        process=subprocess.Popen(command,cwd=attempt,env=env,stdout=out,stderr=err,creationflags=subprocess.CREATE_NO_WINDOW)
    try: job=create_process_job(process)
    except Exception:
        process.kill();process.wait();raise
    design.save(attempt/'process.json',dict(pid=process.pid,supervisor_pid=os.getpid(),started_unix=time.time()))
    return dict(task=task,attempt=attempt,process=process,job=job,started=started,cap=cap,segment=segment)


def finish(run,m,item,timed_out=False):
    process=item['process'];task=item['task'];attempt=item['attempt']
    try:
        cpu=cpu_seconds(process);wall=time.monotonic()-item['started']
        result=design.read(attempt/'incumbent.json') if (attempt/'incumbent.json').exists() else None
        full_budget = (not timed_out and process.returncode == 0 and result
                       and result['stop_reason'] == 'iteration_budget'
                       and result['completed_iterations'] == task['iterations'])
        hit_cap = bool(timed_out or (result and result['stop_reason'] == 'process_time_limit'))
        usable_at_cap = (m["algorithm_policy"] == V4_IRACE and item["cap"] is not None and hit_cap
                         and (timed_out or process.returncode == 0))
        valid = bool(result and result['feasible'] and (full_budget or usable_at_cap))
        if valid:
            if result['seed']!=task['seed'] or result['n']!=task['n'] or result['segment_iters']!=item['segment']:
                raise ValueError('Solver ignored task seed/size/segment')
            if sorted(c for route in result['truck_routes']+result['drone_routes'] for c in route if c)!=list(range(1,task['n']+1)):
                raise ValueError('Incorrect customer coverage')
            for name,value in m['parameters'].items():
                if (name!='cseg' or m["algorithm_policy"] in V4_POLICIES) and abs(result['parameters'][name]-value)>1e-10:
                    raise ValueError('Parameter mismatch: '+name)
            for name,value in m['vehicle_parameters'].items():
                if abs(result['parameters'][name]-value)>1e-10: raise ValueError('Vehicle parameter mismatch: '+name)
            v=m['variants'][task['variant']];flags=result['enabled_components']
            expected=dict(adaptive_selection=v['selection']=='adaptive',mode_switching=v['mode'],simulated_annealing=v['sa'],destroy_repair=v['dr'])
            if any(flags[k]!=value for k,value in expected.items()) or flags['neighborhoods']!=9: raise ValueError('Ablation flag mismatch')
            record=dict(protocol_id=m['protocol_id'],task=task,makespan=result['makespan'],cpu_seconds=cpu,
                        wall_seconds=wall,solver_result=result,completed_unix=time.time(),attempt=str(attempt.relative_to(run)))
            if m["algorithm_policy"] == V4_IRACE:
                record["termination"] = "iteration_budget" if full_budget else "time_limit"
            design.save(run/'results'/task['key']/'completed.json',record)
            print("Completed" if full_budget else "Capped",task['key'],flush=True)
            return 'completed' if full_budget else 'capped'
        reason='time_limit' if hit_cap else 'infeasible_or_incomplete'
        design.save(attempt/'partial.json',dict(protocol_id=m['protocol_id'],task=task,reason=reason,exit_code=process.returncode,
                     cpu_seconds=cpu,wall_seconds=wall,solver_result=result))
        design.save(run/'results'/task['key']/'outcome.json',dict(protocol_id=m['protocol_id'],task=task,state='partial',reason=reason,attempt=str(attempt.relative_to(run))))
        print('Partial',task['key'],reason,flush=True)
        return 'partial'
    except Exception as error:
        design.save(attempt/'error.json',dict(error=str(error),task=task))
        design.save(run/'results'/task['key']/'outcome.json',dict(protocol_id=m['protocol_id'],task=task,state='failed',error=str(error)))
        print('Failed',task['key'],str(error),flush=True)
        return 'failed'
    finally:
        if item['job']: item['job'][1].CloseHandle(item['job'][0])


def extract_inputs(run,m):
    data=run/'inputs';data.mkdir(exist_ok=True)
    with ZipFile(run/'bundle/inputs.zip') as archive:
        for inst in m['instances']:
            for f in inst['files']:
                path=data/Path(f['path']).name
                if path.exists() and design.sha(path)==f['sha256']: continue
                raw=archive.read(f['path'])
                if hashlib.sha256(raw).hexdigest()!=f['sha256']: raise ValueError('Input changed')
                path.write_bytes(raw)


def report_campaign(run):
    partial=None
    try: design.aggregate(argparse.Namespace(bundle=run/'bundle',results=run/'results',output=run/'report'))
    except SystemExit as error: partial=error
    path=run/'report/REPORT.md'
    if path.exists() and load(run/'bundle')["algorithm_policy"] == "tuned-v3":
        text=path.read_text(encoding='utf-8').replace('Table-5 parameters','fixed irace configuration #1987')
        text+='\nThis campaign uses the frozen tuned-v3 algorithm, including its L2 workload score and per-iteration SA cooling. It is a component-design reproduction with #1987, not the changed H_tau/terminal-temperature algorithm in paper revision (4).\n'
        text+='Windows CPU accounting may round very short processes to zero; undefined CPU-time percentage gaps are reported as NA. Time-limited/failed runs remain partial and do not enter full-budget paired comparisons.\n'
        path.write_text(text,encoding='utf-8')
    if partial: print(str(partial),flush=True)


def wait_for_solver_change(active):
    from ctypes import wintypes as w

    handles = (w.HANDLE * len(active))(*(int(item["process"]._handle) for item in active))
    remaining = [item["started"] + item["cap"] - time.monotonic()
                 for item in active if item["cap"] is not None]
    wait_ms = max(0, math.ceil(min([1.0, *remaining]) * 1000))
    wait = ctypes.WinDLL("kernel32", use_last_error=True).WaitForMultipleObjects
    wait.argtypes = [w.DWORD, ctypes.POINTER(w.HANDLE), w.BOOL, w.DWORD]
    wait.restype = w.DWORD
    if wait(len(handles), handles, False, wait_ms) == 0xFFFFFFFF:
        raise ctypes.WinError(ctypes.get_last_error())


def run_campaign(args):
    if os.name!='nt': raise RuntimeError('This runner uses Windows process CPU accounting')
    run=args.run_dir.resolve();run.mkdir(parents=True,exist_ok=True)
    lock=run/'supervisor.lock'
    if lock.exists():
        old=design.read(lock)
        if pid_alive(old['pid']): raise RuntimeError('Campaign already running')
        lock.rename(run/f'stale-lock-{time.time_ns()}.json')
    with lock.open('x') as stream: json.dump(dict(pid=os.getpid()),stream)
    active=[]
    try:
        m=load(run/'bundle');extract_inputs(run,m)
        counts=dict(completed=0,capped=0,partial=0,failed=0)
        pending=[]
        for task in m['tasks']:
            directory=run/'results'/task['key']
            path=directory/'completed.json'
            outcome=directory/'outcome.json'
            if path.exists():
                record=design.read(path)
                if record['task']!=task or not design.accepts_result_protocol(m, record): raise ValueError('Foreign cached result')
                counts['capped' if record.get('termination') == 'time_limit' else 'completed']+=1
            elif outcome.exists() and not args.retry_partial:
                record=design.read(outcome)
                if record['task']!=task or not design.accepts_result_protocol(m, record): raise ValueError('Foreign outcome')
                counts[record['state']]+=1
            else: pending.append(task)
        index=0
        while active or index<len(pending):
            for item in active[:]:
                timed_out=False
                if item['cap'] is not None and item['process'].poll() is None and time.monotonic()-item['started']>=item['cap']:
                    item['process'].kill();item['process'].wait();timed_out=True
                if item['process'].poll() is not None:
                    counts[finish(run,m,item,timed_out)]+=1;active.remove(item)
            while index<len(pending) and len(active)<args.parallel:
                active.append(launch(run,m,pending[index]));index+=1
            publish_status(run/'status.json',dict(state='running',supervisor_pid=os.getpid(),parallel=args.parallel,total=len(m['tasks']),
                           **counts,pending=len(pending)-index,active=[dict(key=i['task']['key'],pid=i['process'].pid,
                           elapsed_seconds=time.monotonic()-i['started']) for i in active],updated_unix=time.time()))
            if active: wait_for_solver_change(active)
        state='finished' if not counts['partial'] and not counts['failed'] else 'finished_with_partial_results'
        report_campaign(run)
        publish_status(run/'status.json',dict(state=state,supervisor_pid=os.getpid(),parallel=args.parallel,total=len(m['tasks']),
                       **counts,pending=0,active=[],updated_unix=time.time()))
    finally:
        for item in active:
            if item['process'].poll() is None: item['process'].kill();item['process'].wait()
            if item['job']: item['job'][1].CloseHandle(item['job'][0])
        lock.unlink(missing_ok=True)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('command',choices=['prepare','run','report'])
    p.add_argument('--run-dir',type=Path,default=ROOT/'experiments/tables678_1987/run')
    p.add_argument('--parameters',type=Path)
    p.add_argument('--source-dir',type=Path)
    p.add_argument('--algorithm-policy',choices=['tuned-v3', 'paper-v4', V4_IRACE],default='tuned-v3')
    p.add_argument('--compiler',default='D:/mingw64/bin/g++.exe')
    p.add_argument('--parallel',type=int,default=20)
    p.add_argument('--retry-partial',action='store_true')
    args=p.parse_args()
    if not 1<=args.parallel<=20: p.error('parallel must be between 1 and 20')
    if args.algorithm_policy in V4_POLICIES:
        args.source_dir = args.source_dir or ROOT / "src_v4"
        args.parameters = args.parameters or (V4_WINNER if args.algorithm_policy == V4_IRACE else ROOT / "src_v4/paper4_defaults.json")
    else:
        v3 = ROOT / "experiments/irace_v3_log/runs/log15000_884_20261112"
        args.source_dir = args.source_dir or v3 / "artifacts"
        args.parameters = args.parameters or v3 / "best_configuration.json"
    if args.command=='prepare': prepare(args)
    elif args.command=='run': run_campaign(args)
    else: report_campaign(args.run_dir.resolve())


if __name__=='__main__': main()
