"""Compile and verify v4 without modifying v3 or any irace artifacts."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

HERE=Path(__file__).resolve().parent
ROOT=HERE.parent


def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--compiler',default='D:/mingw64/bin/g++.exe')
    args=p.parse_args()
    artifacts=ROOT/'experiments/irace_v3_log/runs/log15000_884_20261112/artifacts'
    preserved=[ROOT/'src_v3/tabubu_TimeDependent_v3.cpp',ROOT/'src_v3/paper_neighborhoods.hpp',
               artifacts/'tabubu_TimeDependent_v3.cpp',artifacts/'paper_neighborhoods.hpp',artifacts/'tabubu_TimeDependent_v3.exe']
    before={str(f):digest(f) for f in preserved}
    validation=HERE/'validation';validation.mkdir(exist_ok=True)
    binary=HERE/'tabubu_TimeDependent_v4.exe'
    unit=validation/'test_paper_model.exe'
    subprocess.run([args.compiler,'-std=c++17','-O2','-Wall','-Wextra',str(HERE/'tabubu_TimeDependent_v4.cpp'),'-o',str(binary)],check=True)
    subprocess.run([args.compiler,'-std=c++17','-O1','-g','-D_GLIBCXX_DEBUG','-Wall','-Wextra',str(HERE/'test_paper_model.cpp'),'-o',str(unit)],check=True)
    subprocess.run([str(unit)],check=True)
    instance=ROOT/'instance_time_dependent/10.10.1.txt'
    cases=[]
    def run(name,extra,expected=0):
        folder=validation/name;folder.mkdir(exist_ok=True)
        command=[str(binary),str(instance),'--attempts=1','--iters=300','--seed=42',
                 '--truck-vmax-file='+str(instance.with_name(instance.stem+'.vmax_ij.txt')),
                 '--truck-theta-file='+str(instance.with_name(instance.stem+'.theta_ijl.txt')),
                 '--result-json='+str(folder/'result.json'),*extra]
        result=subprocess.run(command,cwd=folder,capture_output=True,text=True,timeout=45)
        (folder/'stdout.log').write_text(result.stdout,encoding='utf-8');(folder/'stderr.log').write_text(result.stderr,encoding='utf-8')
        assert result.returncode==expected,(name,result.returncode,result.stderr)
        data=json.loads((folder/'result.json').read_text()) if expected==0 else None
        if data:
            assert data['feasible'] and data['completed_iterations']==300
            assert all(data['enabled_components'].values())
            assert data['parameters']['rho']==.1 and data['parameters']['eta']==1
            assert data['parameters']['alpha']==.02 and data['parameters']['truck_capacity']==70
            assert data['parameters']['gamma1']==1 and data['parameters']['gamma2']==.4
            assert data['parameters']['gamma3']==.1
            assert data['telemetry']['sa_trials']>0
        cases.append(dict(name=name,exit_code=result.returncode))
        return data
    first=run('default10',[])
    second=run('repeat10',[])
    assert (first['makespan'],first['truck_routes'],first['drone_routes'])==(second['makespan'],second['truck_routes'],second['drone_routes'])
    assert first['segment_iters']==20
    custom=run('scaling_and_ratios',['--cseg=1.3','--psi2=.4','--psi3=.25','--h-gap=3'])
    assert custom['segment_iters']==13 and custom['parameters']['h_div']==4
    for name,arg in [('old_eta','--eta=0'),('bad_alpha','--alpha=1'),('bad_rho','--rho=0'),('bad_target','--tau-v=0')]:
        run(name,[arg],1)
    # Smoke the larger time-dependent evaluator with the same paper defaults.
    folder=validation/'smoke50';folder.mkdir(exist_ok=True)
    inst50=ROOT/'instance_time_dependent/50.10.1.txt'
    cmd=[str(binary),str(inst50),'--attempts=1','--seed=42','--iters=80','--process-time-limit=20',
         '--result-json='+str(folder/'result.json'),
         '--truck-vmax-file='+str(inst50.with_name(inst50.stem+'.vmax_ij.txt')),
         '--truck-theta-file='+str(inst50.with_name(inst50.stem+'.theta_ijl.txt'))]
    r=subprocess.run(cmd,cwd=folder,capture_output=True,text=True,timeout=25)
    (folder/'stdout.log').write_text(r.stdout,encoding='utf-8');(folder/'stderr.log').write_text(r.stderr,encoding='utf-8')
    assert r.returncode==0,r.stderr
    data=json.loads((folder/'result.json').read_text());assert data['feasible'] and data['completed_iterations']==80
    # An infeasible instance must still exercise mode switching/diversification,
    # preserve a valid result schema, and report an unsuccessful search honestly.
    folder=validation/'forced_stagnation';folder.mkdir(exist_ok=True)
    impossible=folder/'instance.txt'
    impossible.write_text('trucks_count 1\ndrones_count 1\ncustomers 1\ndepot 0 0\nCoordinate X Coordinate Y Dronable Demand\nX Y Dronable Demand Drone_service Truck_service Lw\n1000 0 0 1 0 0 0.001\n')
    r=subprocess.run([str(binary),str(impossible),'--iters=100','--segment-iters=5','--seed=42',
                      '--result-json='+str(folder/'result.json')],cwd=folder,capture_output=True,text=True,timeout=20)
    (folder/'stdout.log').write_text(r.stdout,encoding='utf-8');(folder/'stderr.log').write_text(r.stderr,encoding='utf-8')
    assert r.returncode==2,r.stderr
    data=json.loads((folder/'result.json').read_text())
    assert not data['feasible'] and data['makespan'] is None
    assert data['telemetry']['workload_segments']>0 and data['telemetry']['destroy_repair_calls']>0
    cases.append(dict(name='forced_stagnation',exit_code=r.returncode))
    assert before=={str(f):digest(f) for f in preserved},'Original v3 artifacts changed'
    report=dict(status='PASS',cases=cases,n50_full_smoke_iterations=80,seed_reproducibility=True,
                original_v3_unchanged=True,binary_sha256=digest(binary),source_sha256=digest(HERE/'tabubu_TimeDependent_v4.cpp'))
    (validation/'verification.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))


if __name__=='__main__': main()
