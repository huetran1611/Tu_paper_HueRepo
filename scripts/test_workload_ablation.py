"""Integration checks for the compiled makespan/workload ablation executables."""
from pathlib import Path
import csv
import hashlib
import json
import re
import subprocess
import tempfile

ROOT=Path(__file__).resolve().parents[1]
BIN=ROOT/'build'
NAMES=['tabubu_TimeDependent_v2_makespan_workload_tiebreak',
       'tabubu_TimeDependent_v2_makespan_workload_tiebreak_double_budget']


def run(name,flags,instance='6.20.1.txt'):
    with tempfile.TemporaryDirectory(prefix='workload-ablation-check-') as work:
        args=[str(BIN/name),str(ROOT/'instance'/instance),'--attempts=1','--seed=42','--trucks=2','--drones=2',*flags]
        result=subprocess.run(args,cwd=work,text=True,capture_output=True,check=True)
        history=list(csv.DictReader((Path(work)/'output_mode_0.txt').open()))
        solution=(Path(work)/'output_solution_best.txt').read_text()
        assert 'Final solution feasibility: FEASIBLE' in solution
        served=[]
        for line in solution.splitlines():
            if re.match(r'(Truck|Drone) \d+:',line):
                served.extend(int(x) for x in line.split(':',1)[1].split('|')[0].split() if x!='0')
        n=int(re.search(r'^customers (\d+)',(ROOT/'instance'/instance).read_text(),re.M)[1])
        assert sorted(served)==list(range(1,n+1))
        makespan=float(re.search(r'Improved solution cost: ([\d.]+)',solution)[1])
        workload=float(re.search(r'Workload L2: ([\d.]+)',solution)[1])
        accepted=int(re.search(r'Accepted workload-only improvements: (\d+)',result.stdout)[1])
        return {'history':history,'makespan':makespan,'workload':workload,'accepted_ties':accepted,'stdout':result.stdout}


def main():
    subprocess.run([str(BIN/'test_workload_tiebreak')],check=True)
    pairs=[]
    for flags,expected in [(['--iters=48','--segment-iters=12'],48),
                           (['--segments=3','--segment-iters=7'],21),
                           (['--auto-tune','--iters=100000','--segment-iters=10000'],162)]:
        short=run(NAMES[0],flags);long=run(NAMES[1],flags)
        assert len(short['history'])==expected and len(long['history'])==2*expected
        assert short['history']==long['history'][:expected]
        assert long['makespan']<=short['makespan']+1e-6
        pairs.append({'flags':flags,'base_iterations':expected,'double_iterations':2*expected,
                      'same_prefix':True,'feasible':True})
    original=run(NAMES[0],['--iters=80','--segment-iters=20'],instance='10.5.3.txt')
    repeated=run(NAMES[0],['--iters=80','--segment-iters=20'],instance='10.5.3.txt')
    assert original['history']==repeated['history'] and original['makespan']==repeated['makespan'] and original['workload']==repeated['workload']
    control=run(NAMES[0],['--iters=80','--segment-iters=20','--workload-tie-break=0'],instance='10.5.3.txt')
    assert control['accepted_ties']==0
    for name in NAMES:
        for flag in ['--T0=0.5','--alpha=0.998','--h-mode=2','--h-div=4','--r-destroy=0.2','--workload-tie-break=2']:
            p=subprocess.run([str(BIN/name),str(ROOT/'instance/6.20.1.txt'),flag],text=True,capture_output=True)
            assert p.returncode==2,(name,flag,p.returncode)
    capped=[]
    for name,multiplier in zip(NAMES,[1,2]):
        item=run(name,['--attempts=2','--iters=100000000','--segment-iters=12','--time-limit=0.02'])
        assert 'reason=time_limit' in item['stdout']
        assert len(item['history'])<100000000*multiplier
        assert len(re.findall(r'Attempt setup:',item['stdout']))==1
        cap=float(re.search(r'time_limit_sec=([\d.eE+-]+)',item['stdout'])[1])
        assert abs(cap-.02*multiplier)<1e-9
        capped.append({'variant':name,'time_limit_seconds':cap,'iterations':len(item['history']),
                       'best_solution_saved':True,'one_shared_process_deadline':True})
    exercise=run(NAMES[0],['--iters=120','--segment-iters=30','--trucks=3','--drones=3'],instance='100.20.2.txt')
    multi=[]
    for name in NAMES:
        item=run(name,['--attempts=2','--iters=24','--segment-iters=12'])
        starts=re.findall(r'Attempt setup: index=(\d+), seed=(\d+), initial_makespan=([\d.]+), initial_workload=([\d.]+)',item['stdout'])
        assert len(starts)==2
        multi.append(starts)
    assert multi[0]==multi[1]
    source_sha=hashlib.sha256((ROOT/'src_v2/tabubu_TimeDependent_v2.cpp').read_bytes()).hexdigest()
    assert source_sha=='16a46a7a7712580cc78a54ff4e44ec99c9c5013be12650269f927e043855bdac'
    summary={'all_passed':True,'budget_tests':pairs,'repeatability':True,'matched_multi_attempt_initialization':True,
             'time_limit_tests':capped,
             'disabled_workload_accepted_ties':control['accepted_ties'],
             'small_instance_accepted_ties':original['accepted_ties'],
             '100_customer_example_accepted_ties':exercise['accepted_ties'],
             'workload_run_makespan':original['makespan'],'control_makespan':control['makespan'],
             'disabled_feature_flags_rejected':True,'original_source_sha256':source_sha}
    path=BIN/'workload_ablation_validation.json';path.write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps(summary,indent=2))

if __name__=='__main__':main()
