"""960 independent runs: two workload-tie ablations, 96 TD instances, five seeds."""
import argparse,csv,gzip,hashlib,json,math,re,shutil,statistics,subprocess,sys,time
from pathlib import Path
from zipfile import ZipFile,ZIP_DEFLATED

ROOT=Path(__file__).resolve().parents[1]
SEEDS=(42,43,44,45,46)
VARIANTS={'normal':('tabubu_TimeDependent_v2_makespan_workload_tiebreak',1),
          'double':('tabubu_TimeDependent_v2_makespan_workload_tiebreak_double_budget',2)}
CAPACITIES=(250,250,250,210)
BASE_TIME_LIMIT_SEC=7200
WATCHDOG_GRACE_SEC=300
PROTOCOL_VERSION=2


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def dump(p,a):
    p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(a,indent=2)+'\n')
def save_csv(p,rows):
    p.parent.mkdir(parents=True,exist_ok=True)
    fields=list(dict.fromkeys(k for r in rows for k in r))
    with p.open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)
def budget(n):return n*9*math.ceil(math.sqrt(n))


def validate_termination(task, iterations, reason):
    expected=task['expected_iterations']
    if reason=='iteration_limit':
        assert iterations==expected,(iterations,expected)
    elif reason=='time_limit':
        assert 0<=iterations<expected,(iterations,expected)
        assert task['expected_time_limit_sec']>0
    else:
        raise AssertionError('Unknown termination reason: '+str(reason))


def bundle_inputs(args):
    target=ROOT/'.github/workload-tiebreak';target.mkdir(parents=True,exist_ok=True)
    records=[]
    archive=target/'inputs.zip'
    with ZipFile(archive,'w',ZIP_DEFLATED,compresslevel=6) as z:
        for group,directory in [('6_100','instance_time_dependent/6_100_customers'),('200','instance_time_dependent/big_instance/normal')]:
            found=[]
            for p in (ROOT/directory).glob('*.txt'):
                if re.fullmatch(r'\d+\.\d+\.\d+\.txt',p.name) and (group!='200' or p.name.startswith('200.')):found.append(p)
            assert len(found)==(80 if group=='6_100' else 16)
            for p in sorted(found,key=lambda p:tuple(map(int,p.stem.split('.')))):
                headers=p.read_text().splitlines()[:3];n=int(headers[2].split()[1]);assert int(p.stem.split('.')[0])==n
                record={'dataset':group,'instance':p.stem,'customers':n,'trucks':int(headers[0].split()[1]),
                        'drones':int(headers[1].split()[1]),'source_directory':directory,'files':[]}
                for q in [p,p.with_name(p.stem+'.vmax_ij.txt'),p.with_name(p.stem+'.theta_ijl.txt')]:
                    assert q.is_file();name=f'data/{group}/{q.name}';z.write(q,name)
                    record['files'].append({'path':name,'sha256':sha(q),'bytes':q.stat().st_size})
                records.append(record)
    assert len(records)==96
    with ZipFile(archive) as z:assert z.testzip() is None
    dump(target/'inputs.json',{'instances':records,'zip_sha256':sha(archive),'seeds':SEEDS,'K':9,
         'truck_capacity_kg':40,'drone_capacity_kg':2.27,
         'stop_rule':'stop at N=n*K*ceil(sqrt(n)) or 7200 process seconds; double uses 2N or 14400 seconds'})
    print(json.dumps({'instances':len(records),'tasks':960,'batches':CAPACITIES,'zip_bytes':archive.stat().st_size},indent=2))


def prepare(args):
    base_time_limit=getattr(args,'base_time_limit_sec',BASE_TIME_LIMIT_SEC)
    if base_time_limit<=0:raise ValueError('A positive base time limit is required')
    out=args.bundle;out.mkdir(parents=True,exist_ok=True)
    for name in ['inputs.zip','inputs.json']:shutil.copy2(ROOT/'.github/workload-tiebreak'/name,out/name)
    meta=json.loads((out/'inputs.json').read_text());assert sha(out/'inputs.zip')==meta['zip_sha256']
    with ZipFile(out/'inputs.zip') as z:
        for inst in meta['instances']:
            for item in inst['files']:assert hashlib.sha256(z.read(item['path'])).hexdigest()==item['sha256']
    tasks=[]
    for seed in SEEDS:
        for inst in meta['instances']:
            for variant,(binary,multiplier) in VARIANTS.items():
                tasks.append({'key':f"{inst['dataset']}-{inst['instance']}-{variant}-s{seed}",
                    'instance':inst['instance'],'dataset':inst['dataset'],'customers':inst['customers'],
                    'trucks':inst['trucks'],'drones':inst['drones'],'seed':seed,'variant':variant,
                    'binary':binary,'budget_multiplier':multiplier,'base_iterations':budget(inst['customers']),
                    'expected_iterations':budget(inst['customers'])*multiplier,
                    'expected_segment_iterations':inst['customers']*3,
                    'base_time_limit_sec':base_time_limit,'expected_time_limit_sec':base_time_limit*multiplier,
                    'truck_capacity_kg':40,'drone_capacity_kg':2.27})
    cursor=0;counts=[0]*4
    for task in tasks:
        while counts[cursor]>=CAPACITIES[cursor]:cursor=(cursor+1)%4
        task['batch']=cursor+1;counts[cursor]+=1;cursor=(cursor+1)%4
    assert len(tasks)==len({t['key'] for t in tasks})==960 and tuple(counts)==CAPACITIES
    snapshots=out/'source_snapshot';snapshots.mkdir(exist_ok=True)
    source_paths=['src_v2/tabubu_TimeDependent_v2.cpp','src_v2/tabubu_TimeDependent_workload_tiebreak_impl.hpp',
        'src_v2/tabubu_TimeDependent_v2_makespan_workload_tiebreak.cpp',
        'src_v2/tabubu_TimeDependent_v2_makespan_workload_tiebreak_double_budget.cpp',
        'src_v2/WORKLOAD_TIEBREAK_ABLATION.md','scripts/test_workload_tiebreak.cpp']
    hashes={}
    for name in source_paths:
        p=snapshots/name;p.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(ROOT/name,p);hashes[name]=sha(p)
    for binary,_ in VARIANTS.values():assert (out/'bin'/binary).is_file()
    manifest={'commit':args.commit,'tasks':tasks,'seeds':SEEDS,'instances':meta['instances'],
        'inputs_zip_sha256':meta['zip_sha256'],'source_sha256':hashes,
        'binary_sha256':{b:sha(out/'bin'/b) for b,_ in VARIANTS.values()},
        'batch_sizes':CAPACITIES,'optimization_processes':960,'SA':False,'destroy':False,'repair':False,
        'workload_tie_break':True,'attempts_per_task':1,'base_time_limit_sec':base_time_limit,
        'protocol_version':PROTOCOL_VERSION,'watchdog_grace_sec':WATCHDOG_GRACE_SEC,
        'time_limit_scope':'whole solver process, shared across attempts including initialization',
        'stop_rule':'iteration_limit_or_time_limit','early_stagnation_stop':False}
    manifest['protocol_id']=hashlib.sha256(json.dumps(manifest,sort_keys=True).encode()).hexdigest()
    dump(out/'task_manifest.json',manifest)
    shutil.copy2(Path(__file__),out/'workload_tiebreak_experiment.py')
    print(json.dumps({'tasks':960,'batches':counts,'protocol_id':manifest['protocol_id']},indent=2))


def load(bundle):
    m=json.loads((bundle/'task_manifest.json').read_text());raw=dict(m);protocol=raw.pop('protocol_id')
    assert hashlib.sha256(json.dumps(raw,sort_keys=True).encode()).hexdigest()==protocol
    if m.get('protocol_version')!=PROTOCOL_VERSION:
        raise ValueError('Old unlimited-time bundle; run the updated setup workflow before starting batches')
    return m


def run_task(args):
    m=load(args.bundle);task=next(t for t in m['tasks'] if t['key']==args.task_key)
    target=args.output.resolve();target.mkdir(parents=True,exist_ok=False)
    try:
        binary=(args.bundle/'bin'/task['binary']).resolve();assert sha(binary)==m['binary_sha256'][task['binary']]
        data=next(x for x in m['instances'] if x['instance']==task['instance'] and x['dataset']==task['dataset'])
        staging=target/'inputs';staging.mkdir()
        with ZipFile(args.bundle/'inputs.zip') as z:
            for item in data['files']:
                raw=z.read(item['path']);assert hashlib.sha256(raw).hexdigest()==item['sha256']
                (staging/Path(item['path']).name).write_bytes(raw)
        p=staging/(task['instance']+'.txt')
        command=[str(binary),str(p),'--attempts=1','--auto-tune',
            '--time-limit='+str(task['base_time_limit_sec']),'--workload-tie-break=1',
            '--seed='+str(task['seed']),'--truck-capacity=40','--drone-capacity=2.27',
            '--truck-vmax-file='+str(p.with_name(p.stem+'.vmax_ij.txt')),
            '--truck-theta-file='+str(p.with_name(p.stem+'.theta_ijl.txt'))]
        dump(target/'command.json',{'task':task,'command':command,'protocol_id':m['protocol_id']})
        started=time.monotonic()
        with (target/'stdout.log').open('w') as stdout,(target/'stderr.log').open('w') as stderr:
            result=subprocess.run(command,cwd=target,stdout=stdout,stderr=stderr,
                                  timeout=task['expected_time_limit_sec']+m['watchdog_grace_sec'])
        if result.returncode:raise RuntimeError(f'Solver exit code {result.returncode}')
        seconds=time.monotonic()-started
        with (target/'output_mode_0.txt').open() as stream:
            history=csv.DictReader(stream);count=0
            for row in history:
                count+=1;assert int(row['iter'])==count
        stdout=(target/'stdout.log').read_text()
        config=re.search(r'Search config: total_iters=(\d+) \(segments=(\d+), segment_iters=(\d+)',stdout)
        assert config and int(config[1])==task['expected_iterations'] and int(config[3])==task['expected_segment_iterations']
        cap=re.search(r'time_limit_sec=([\d.eE+-]+)',stdout)
        assert cap and abs(float(cap[1])-task['expected_time_limit_sec'])<=1e-6
        termination=re.search(r'Search termination: reason=(\w+), completed_iterations=(\d+), max_iterations=(\d+)',stdout)
        assert termination and int(termination[2])==count and int(termination[3])==task['expected_iterations']
        stop_reason=termination[1]
        validate_termination(task,count,stop_reason)
        solution=(target/'output_solution_best.txt').read_text()
        served=[]
        for line in solution.splitlines():
            if re.match(r'(Truck|Drone) \d+:',line):served += [int(s) for s in line.split(':',1)[1].split('|')[0].split() if int(s)!=0]
        assert sorted(served)==list(range(1,task['customers']+1))
        metrics={name:float(re.search(pattern,solution)[1]) for name,pattern in {
            'makespan_sec':r'Improved solution cost: ([\d.eE+-]+)',
            'workload_l2_sec':r'Workload L2: ([\d.eE+-]+)',
            'deadline_violation':r'Total validation:.*Deadline violation=([\d.eE+-]+)',
            'energy_violation':r'Total validation:.*Energy violation=([\d.eE+-]+)',
            'capacity_violation':r'Total validation:.*Capacity violation=([\d.eE+-]+)'}.items()}
        feasible='Final solution feasibility: FEASIBLE' in solution
        assert all(math.isfinite(v) and v>=0 for v in metrics.values())
        if feasible:assert all(metrics[k]<=1e-6 for k in ['deadline_violation','energy_violation','capacity_violation'])
        output={'protocol_id':m['protocol_id'],'task':task,'iterations':count,'elapsed_wall_sec':seconds,
            'stop_reason':stop_reason,'time_limit_sec':task['expected_time_limit_sec'],
            'feasible':feasible,**metrics,'workload_only_accepted_moves':int(re.search(r'Accepted workload-only improvements: (\d+)',stdout)[1]),
            'solution_sha256':sha(target/'output_solution_best.txt'),'iteration_csv_sha256':sha(target/'output_mode_0.txt')}
        dump(target/'result.json',output)
        for name in ['stdout.log','stderr.log']:
            with (target/name).open('rb') as src,gzip.open(target/(name+'.gz'),'wb') as dest:shutil.copyfileobj(src,dest)
            (target/name).unlink()
        shutil.rmtree(staging)
        print(json.dumps(output,indent=2))
    except Exception as error:
        dump(target/'error.json',{'task':task,'protocol_id':m['protocol_id'],'technical_failure':True,'error':str(error)})
        raise


def smoke(args):
    import tempfile
    m=load(args.bundle)
    base=next(t for t in m['tasks'] if t['customers']==6 and t['variant']=='normal')
    double=next(t for t in m['tasks'] if t['instance']==base['instance'] and t['seed']==base['seed'] and t['variant']=='double')
    with tempfile.TemporaryDirectory(prefix='workload-smoke-') as d:
        results=[];hist=[]
        for task in [base,double]:
            out=Path(d)/task['variant'];run_task(argparse.Namespace(bundle=args.bundle,task_key=task['key'],output=out))
            results.append(json.loads((out/'result.json').read_text()))
            hist.append(list(csv.DictReader((out/'output_mode_0.txt').open())))
        assert hist[0]==hist[1][:len(hist[0])]
        assert results[1]['makespan_sec']<=results[0]['makespan_sec']+1e-6
    dump(args.bundle/'smoke_validation.json',{'all_passed':True,'actual_TD_files_used':True,
        'base_iterations':162,'double_iterations':324,'same_seed_trajectory_prefix':True,
        'input_instances':96,'experiment_processes':960,'smoke_processes_excluded_from_experiment':2})


def aggregate(args):
    m=load(args.bundle);expected={t['key']:t for t in m['tasks'] if not args.batch or t['batch']==args.batch}
    found={};invalid=[]
    for p in args.input.glob('**/result.json'):
        try:
            r=json.loads(p.read_text());key=r['task']['key'];assert key in expected and r['protocol_id']==m['protocol_id']
            assert r['task']==expected[key] and r['time_limit_sec']==expected[key]['expected_time_limit_sec']
            validate_termination(expected[key],r['iterations'],r['stop_reason'])
            assert key not in found;found[key]=r
        except Exception as e:invalid.append({'path':str(p),'error':str(e)})
    args.output.mkdir(parents=True,exist_ok=True)
    coverage={'expected':len(expected),'completed':len(found),'missing':sorted(set(expected)-found.keys()),'invalid':invalid,
              'iteration_limited':sum(r['stop_reason']=='iteration_limit' for r in found.values()),
              'time_limited':sum(r['stop_reason']=='time_limit' for r in found.values()),
              'batch':args.batch,'protocol_id':m['protocol_id']}
    dump(args.output/'coverage.json',coverage)
    rows=[{**r['task'],**{k:v for k,v in r.items() if k not in ['task']}} for r in found.values()]
    save_csv(args.output/'all_runs.csv',rows)
    for key,r in found.items():dump(args.output/'records'/key/'result.json',r)
    groups={}
    for row in rows:groups.setdefault((row['dataset'],row['instance'],row['variant']),[]).append(row)
    summary=[]
    for (dataset,instance,variant),group in sorted(groups.items()):
        feasible=[r for r in group if r['feasible']]
        summary.append({'dataset':dataset,'instance':instance,'variant':variant,'completed_repetitions':len(group),
            'feasible_repetitions':len(feasible),'mean_makespan_sec':statistics.mean(r['makespan_sec'] for r in feasible) if feasible else None,
            'best_makespan_sec':min((r['makespan_sec'] for r in feasible),default=None),
            'mean_workload_l2_sec':statistics.mean(r['workload_l2_sec'] for r in feasible) if feasible else None})
    save_csv(args.output/'summary.csv',summary)
    if coverage['missing'] or invalid:raise RuntimeError('Incomplete batch; inspect coverage.json')
    print(json.dumps(coverage,indent=2))


def main():
    p=argparse.ArgumentParser(__doc__);p.add_argument('action',choices=['bundle-inputs','prepare','matrix','run','smoke','aggregate'])
    p.add_argument('--bundle',type=Path);p.add_argument('--batch',type=int,default=0);p.add_argument('--commit')
    p.add_argument('--task-key');p.add_argument('--output',type=Path);p.add_argument('--input',type=Path)
    p.add_argument('--base-time-limit-sec',type=float,default=BASE_TIME_LIMIT_SEC)
    args=p.parse_args()
    if args.bundle:args.bundle=args.bundle.resolve()
    if args.action=='bundle-inputs':bundle_inputs(args)
    elif args.action=='prepare':prepare(args)
    elif args.action=='matrix':
        tasks=[t for t in load(args.bundle)['tasks'] if t['batch']==args.batch];assert len(tasks)==CAPACITIES[args.batch-1]
        print(json.dumps({'include':[{'key':t['key']} for t in tasks]},separators=(',',':')))
    elif args.action=='run':run_task(args)
    elif args.action=='smoke':smoke(args)
    elif args.action=='aggregate':aggregate(args)

if __name__=='__main__':main()
