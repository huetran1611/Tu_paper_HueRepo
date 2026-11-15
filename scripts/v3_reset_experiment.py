"""Isolated T678 experiment: reheat SA to T0 after each completed DR."""
import argparse
import csv
import difflib
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / 'result_final/Code_V3/v3_reset_100_10seeds_20261115'
PARENT_PROTOCOL = '43adc264ef4135c252a0c49f6fdeb7438097b6136d8f14f2a7a5b8d7c71a507c'


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, data):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + '\n')


def replace_once(text, old, new):
    assert text.count(old) == 1, old[:120]
    return text.replace(old, new, 1)


def adapt(source):
    source = replace_once(source,
        'int workload_segments = 0, makespan_segments = 0, destroy_repair_calls = 0, penalty_updates = 0;',
        'int workload_segments = 0, makespan_segments = 0, destroy_repair_calls = 0, penalty_updates = 0;\n'
        '    int temperature_resets = 0, last_temperature_reset_iteration = 0;')
    source = replace_once(source, '    int iter = 0;\n    int total_iters',
        '    int iter = 0;\n    int sa_last_reset_iter = 0; // Global index of first iteration after the latest DR.\n    int total_iters')
    source = replace_once(source, 'double T = T0 * pow(alpha, iter);',
        'double T = T0 * pow(alpha, iter - sa_last_reset_iter);')
    anchor = '                current_sol = recalculate_solution(current_sol);\n                if (is_feasible(current_sol)'
    replacement = '''                current_sol = recalculate_solution(current_sol);
                if (paper_ablation.sa) {
                    // This boundary follows iteration iter; the next iteration uses T=T0.
                    sa_last_reset_iter = iter + 1;
                    ++run_telemetry.temperature_resets;
                    run_telemetry.last_temperature_reset_iteration = sa_last_reset_iter;
                    cout << "SA temperature reset after DR: next_iter=" << sa_last_reset_iter
                         << ", temperature=" << T0 << "\\n";
                }
                if (is_feasible(current_sol)'''
    source = replace_once(source, anchor, replacement)
    source = replace_once(source,
        '<< ",\\\"penalty_updates\\\":" << run_telemetry.penalty_updates << ",\\\"operator_calls\\\":[";',
        '<< ",\\\"penalty_updates\\\":" << run_telemetry.penalty_updates\n'
        '        << ",\\\"temperature_resets\\\":" << run_telemetry.temperature_resets\n'
        '        << ",\\\"last_temperature_reset_iteration\\\":" << run_telemetry.last_temperature_reset_iteration\n'
        '        << ",\\\"operator_calls\\\":[";')
    return source


def load_runner(bundle):
    spec = importlib.util.spec_from_file_location('t678_frozen_runner', bundle/'paper_tables_678.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def create_local():
    frozen = ROOT/'result_final/Code_V3/frozen_t678'
    LOCAL.mkdir(parents=True, exist_ok=True)
    original = (frozen/'source/solver.cpp').read_text()
    reset = adapt(original)
    (LOCAL/'source').mkdir(exist_ok=True)
    (LOCAL/'source/v3_reset.cpp').write_text(reset)
    shutil.copyfile(frozen/'source/paper_neighborhoods.hpp', LOCAL/'source/paper_neighborhoods.hpp')
    (LOCAL/'temperature_reset.patch').write_text(''.join(difflib.unified_diff(
        original.splitlines(True), reset.splitlines(True), fromfile='frozen_t678/source/solver.cpp', tofile='source/v3_reset.cpp')))
    with (ROOT/'result_final/Code_V3/T678_github_actions_20261115/all_solutions_and_parameters.csv').open(encoding='utf-8-sig', newline='') as f:
        rows = [r for r in csv.DictReader(f) if r['customers']=='100' and r['variant']=='switch_dr_sa']
    assert len(rows)==160 and len({r['instance'] for r in rows})==16
    pilot = next(r for r in rows if r['instance']=='100.10.1' and r['explicit_run_seed']=='42')
    expected = {'parent_protocol_id': PARENT_PROTOCOL, 'source_sha256': digest(frozen/'source/solver.cpp'),
        'reset_source_sha256': digest(LOCAL/'source/v3_reset.cpp'),
        'header_sha256': digest(frozen/'source/paper_neighborhoods.hpp'),
        'pilot': {k:pilot[k] for k in ['task_key','makespan_s','truck_routes','drone_routes','solver_cpu_s']}}
    save(LOCAL/'expected.json', expected)
    with (LOCAL/'github_baseline_160.csv').open('w', encoding='utf-8-sig', newline='') as f:
        writer=csv.DictWriter(f, fieldnames=list(rows[0])); writer.writeheader();writer.writerows(rows)
    print(json.dumps({'local_source':str(LOCAL/'source/v3_reset.cpp'),'source_sha256':expected['reset_source_sha256'],'baseline_runs':len(rows)}))


def prepare(bundle, expected_path):
    bundle = bundle.resolve()
    expected = json.loads(expected_path.read_text())
    manifest = json.loads((bundle/'manifest.json').read_text())
    assert manifest['protocol_id']==expected['parent_protocol_id']
    assert digest(bundle/'source/solver.cpp')==expected['source_sha256']
    assert digest(bundle/'source/paper_neighborhoods.hpp')==expected['header_sha256']
    subprocess.run(['g++','-std=c++17','-O2','-Wall','-Wextra',str(bundle/'source/solver.cpp'),'-o',str(bundle/'solver_control')],check=True)
    runner = load_runner(bundle)
    control_manifest = {**manifest, 'binary':'solver_control'}
    pilot_task = next(t for t in manifest['tasks'] if t['key']==expected['pilot']['task_key'])
    runner.run_one(bundle, Path('control_results').resolve(), pilot_task, control_manifest)
    control = json.loads((Path('control_results')/pilot_task['key']/'completed.json').read_text())
    reference = expected['pilot']
    assert abs(control['makespan']-float(reference['makespan_s'])) < 1e-8, (control['makespan'],reference['makespan_s'])
    assert control['solver_result']['truck_routes']==json.loads(reference['truck_routes'])
    assert control['solver_result']['drone_routes']==json.loads(reference['drone_routes'])
    save('control_validation.json', {'matched_github_objective_and_routes':True,'task':pilot_task,
        'github_makespan_s':float(reference['makespan_s']),'recompiled_control':control,
        'compiler':subprocess.check_output(['g++','--version'],text=True).splitlines()[0]})
    original = (bundle/'source/solver.cpp').read_text()
    (bundle/'source/solver.cpp').write_text(adapt(original))
    assert digest(bundle/'source/solver.cpp')==expected['reset_source_sha256']
    subprocess.run(['g++','-std=c++17','-O2','-Wall','-Wextra',str(bundle/'source/solver.cpp'),'-o',str(bundle/'solver')],check=True)
    tasks = [dict(t) for t in manifest['tasks'] if t['n']==100 and t['variant']=='switch_dr_sa']
    assert len(tasks)==160
    for slot,t in enumerate(tasks): t.update(batch=1,slot=slot)
    manifest.update(parent_protocol_id=manifest['protocol_id'],experiment='v3_reset_after_dr_100_10seeds',
        tasks=tasks,instances=[i for i in manifest['instances'] if i['customers']==100],batch_size=160,batch_count=1,
        temperature_policy={'reset_after_completed_dr':True,'T0':0.005,'alpha_per_iteration':0.9982,
                            'next_iteration_temperature':'T0','global_iteration_budget_unchanged':True})
    manifest['adapted_source_sha256']['solver.cpp']=digest(bundle/'source/solver.cpp')
    manifest['binary_sha256']=digest(bundle/'solver')
    # Keep only the 100-customer input members in the distributed bundle.
    names={f['path'] for i in manifest['instances'] for f in i['files']}
    temporary=bundle/'inputs_100.zip'
    with ZipFile(bundle/'inputs.zip') as old, ZipFile(temporary,'w',compression=ZIP_DEFLATED) as new:
        for name in sorted(names): new.writestr(name,old.read(name))
    temporary.replace(bundle/'inputs.zip')
    manifest['inputs_zip_sha256']=digest(bundle/'inputs.zip')
    manifest['protocol_id']=hashlib.sha256(json.dumps(manifest,sort_keys=True).encode()).hexdigest()
    save(bundle/'manifest.json',manifest)
    (bundle/'solver_control').unlink()
    shutil.copyfile('control_validation.json',bundle/'control_validation.json')
    print('Pilot matched GitHub. Reset bundle ready: 160 tasks.',flush=True)


def run_task(bundle, slot):
    bundle=bundle.resolve();m=json.loads((bundle/'manifest.json').read_text())
    task=m['tasks'][slot]; assert task['n']==100 and task['variant']=='switch_dr_sa'
    assert digest(bundle/'source/solver.cpp')==m['adapted_source_sha256']['solver.cpp']
    runner=load_runner(bundle); runner.run_one(bundle,Path('results').resolve(),task,m)
    path=Path('results')/task['key']/'completed.json'; record=json.loads(path.read_text())
    telemetry=record['solver_result']['telemetry'];flags=record['solver_result']['enabled_components']
    assert flags['simulated_annealing'] and flags['destroy_repair'] and flags['mode_switching']
    assert telemetry['temperature_resets']==telemetry['destroy_repair_calls']>0
    assert record['solver_result']['completed_iterations']==9000
    print(json.dumps({'task':task['key'],'makespan':record['makespan'],'resets':telemetry['temperature_resets']}),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(); sub=p.add_subparsers(dest='command',required=True)
    sub.add_parser('create-local')
    a=sub.add_parser('prepare');a.add_argument('--bundle',type=Path,required=True);a.add_argument('--expected',type=Path,required=True)
    a=sub.add_parser('task');a.add_argument('--bundle',type=Path,required=True);a.add_argument('--slot',type=int,required=True)
    args=p.parse_args()
    if args.command=='create-local': create_local()
    elif args.command=='prepare': prepare(args.bundle,args.expected)
    else: run_task(args.bundle,args.slot)
