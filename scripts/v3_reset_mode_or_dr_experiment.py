"""Controlled V3 experiment: reset SA once on mode change OR completed DR."""
import argparse
import hashlib
import json
from pathlib import Path

import v3_reset_experiment as core

ROOT=Path(__file__).resolve().parents[1]
LOCAL=ROOT/'result_final/Code_V3/v3_reset_mode_or_dr_100_10seeds_20261115'


def adapt(source):
    replace=core.replace_once
    source=replace(source,
        'int workload_segments = 0, makespan_segments = 0, destroy_repair_calls = 0, penalty_updates = 0;',
        'int workload_segments = 0, makespan_segments = 0, destroy_repair_calls = 0, penalty_updates = 0;\n'
        '    int temperature_resets = 0, last_temperature_reset_iteration = 0;\n'
        '    int mode_switches = 0, resets_due_to_mode = 0, resets_due_to_dr = 0, resets_due_to_both = 0;')
    source=replace(source,'    int iter = 0;\n    int total_iters',
        '    int iter = 0;\n    int sa_last_reset_iter = 0;\n    int total_iters')
    source=replace(source,'double T = T0 * pow(alpha, iter);',
        'double T = T0 * pow(alpha, iter - sa_last_reset_iter);')
    source=replace(source,'            if (next_scoring_mode != scoring_mode_iter) {\n                scoring_mode_iter = next_scoring_mode;',
        '            const bool mode_changed = next_scoring_mode != scoring_mode_iter;\n'
        '            if (mode_changed) {\n'
        '                ++run_telemetry.mode_switches;\n'
        '                scoring_mode_iter = next_scoring_mode;')
    source=replace(source,'            // Update weights according to w_q <- (1-gamma4)w_q + gamma4 R_q/U_q.',
        '''            // One union event: mode change OR completed DR; never reheat twice at this boundary.
            if (paper_ablation.sa && (mode_changed || diversify)) {
                sa_last_reset_iter = iter + 1;
                ++run_telemetry.temperature_resets;
                run_telemetry.last_temperature_reset_iteration = sa_last_reset_iter;
                if (mode_changed) ++run_telemetry.resets_due_to_mode;
                if (diversify) ++run_telemetry.resets_due_to_dr;
                if (mode_changed && diversify) ++run_telemetry.resets_due_to_both;
                cout << "SA temperature reset: next_iter=" << sa_last_reset_iter
                     << ", mode_changed=" << mode_changed << ", DR=" << diversify
                     << ", temperature=" << T0 << "\\n";
            }

            // Update weights according to w_q <- (1-gamma4)w_q + gamma4 R_q/U_q.''')
    source=replace(source,
        '<< ",\\\"penalty_updates\\\":" << run_telemetry.penalty_updates << ",\\\"operator_calls\\\":[";',
        '<< ",\\\"penalty_updates\\\":" << run_telemetry.penalty_updates\n'
        '        << ",\\\"temperature_resets\\\":" << run_telemetry.temperature_resets\n'
        '        << ",\\\"last_temperature_reset_iteration\\\":" << run_telemetry.last_temperature_reset_iteration\n'
        '        << ",\\\"mode_switches\\\":" << run_telemetry.mode_switches\n'
        '        << ",\\\"resets_due_to_mode\\\":" << run_telemetry.resets_due_to_mode\n'
        '        << ",\\\"resets_due_to_dr\\\":" << run_telemetry.resets_due_to_dr\n'
        '        << ",\\\"resets_due_to_both\\\":" << run_telemetry.resets_due_to_both\n'
        '        << ",\\\"operator_calls\\\":[";')
    return source


def create_local():
    core.LOCAL=LOCAL;core.adapt=adapt;core.create_local()
    (LOCAL/'source/v3_reset.cpp').rename(LOCAL/'source/v3_reset_mode_or_dr.cpp')
    p=LOCAL/'temperature_reset.patch';p.write_text(p.read_text().replace('source/v3_reset.cpp','source/v3_reset_mode_or_dr.cpp'))


def prepare(bundle,expected):
    core.adapt=adapt
    core.prepare(bundle,expected)
    m=json.loads((bundle/'manifest.json').read_text())
    m.update(experiment='v3_reset_mode_or_dr_100_10seeds',temperature_policy={
        'reset_on_actual_mode_change':True,'reset_after_completed_dr':True,
        'simultaneous_events_reset_once':True,'T0':0.005,'alpha_per_iteration':0.9982,
        'next_iteration_temperature':'T0','global_iteration_budget_unchanged':True})
    m.pop('protocol_id')
    m['protocol_id']=hashlib.sha256(json.dumps(m,sort_keys=True).encode()).hexdigest()
    core.save(bundle/'manifest.json',m)


def task(bundle,slot):
    bundle=bundle.resolve();m=json.loads((bundle/'manifest.json').read_text());task=m['tasks'][slot]
    assert task['n']==100 and task['variant']=='switch_dr_sa'
    assert core.digest(bundle/'source/solver.cpp')==m['adapted_source_sha256']['solver.cpp']
    assert m['temperature_policy']['reset_on_actual_mode_change']
    runner=core.load_runner(bundle);runner.run_one(bundle,Path('results').resolve(),task,m)
    p=Path('results')/task['key']/'completed.json';r=json.loads(p.read_text());s=r['solver_result'];t=s['telemetry']
    assert s['feasible'] and s['completed_iterations']==9000 and s['stop_reason']=='iteration_budget'
    assert all(s['enabled_components'][k] for k in ['mode_switching','destroy_repair','simulated_annealing'])
    assert t['resets_due_to_dr']==t['destroy_repair_calls']>0
    assert t['resets_due_to_mode']==t['mode_switches']>0
    assert t['temperature_resets']==t['resets_due_to_dr']+t['resets_due_to_mode']-t['resets_due_to_both']
    assert 0<t['last_temperature_reset_iteration']<=9000
    print(json.dumps({'task':task['key'],'makespan':r['makespan'],'resets':t['temperature_resets'],
        'mode_events':t['mode_switches'],'DR_events':t['destroy_repair_calls'],'simultaneous':t['resets_due_to_both']}),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();s=p.add_subparsers(dest='command',required=True)
    s.add_parser('create-local')
    a=s.add_parser('prepare');a.add_argument('--bundle',type=Path,required=True);a.add_argument('--expected',type=Path,required=True)
    a=s.add_parser('task');a.add_argument('--bundle',type=Path,required=True);a.add_argument('--slot',type=int,required=True)
    a=p.parse_args()
    if a.command=='create-local':create_local()
    elif a.command=='prepare':prepare(a.bundle,a.expected)
    else:task(a.bundle,a.slot)
