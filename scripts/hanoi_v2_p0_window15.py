"""Isolated P0-only rerun of the frozen Hanoi V2 experiment on [08,15)."""
import argparse
import csv
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
SHARD_SIZE = 20


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + '\n')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def csv_file(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(dict.fromkeys(k for r in rows for k in r))
    with path.open('w', newline='', encoding='utf-8-sig') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def runtime(capsule):
    sys.path.insert(0, str(capsule / 'runtime/scripts'))
    import hanoi_v2_experiment as v2
    full, manifest = v2.load(capsule)
    if manifest.get('p0_window') != [8, 15] or manifest.get('rerun_policy') != 'P0':
        raise ValueError('Expected the isolated P0 [08,15) protocol')
    if len(manifest['tasks']) != 3200 or any(t['policy'] != 'P0' for t in manifest['tasks']):
        raise ValueError('Wrong P0-only task inventory')
    return v2, full, manifest


def prepare(args):
    # First verify the unmodified historical capsule, solver and input hashes.
    sys.path.insert(0, str(ROOT / 'scripts'))
    import hanoi_v2_experiment as v2
    v2.freeze(SimpleNamespace(capsule=args.capsule, experiment_id=args.experiment_id, commit=args.commit))
    manifest_path = args.capsule / 'experiment.json'
    manifest = json.loads(manifest_path.read_text())
    parent_protocol = manifest['protocol_id']
    parent_hashes = manifest['code_sha256'].copy()
    target = args.capsule / 'runtime/scripts/hanoi_buffer10_cloud.py'
    text = target.read_text()
    replacements = {
        'window = (hours >= 8) & (hours < 17)': 'window = (hours >= 8) & (hours < 15)',
        'if window.sum() != 9:': 'if window.sum() != 7:',
        'P0 needs nine hourly intervals [8,17)': 'P0 needs seven hourly intervals [8,15)',
    }
    for before, after in replacements.items():
        if text.count(before) != 1:
            raise ValueError(f'Historical P0 source changed: {before}')
        text = text.replace(before, after)
    target.write_text(text)
    frozen_runtime = args.capsule / 'runtime'
    shutil.copy2(Path(__file__), frozen_runtime / 'scripts/hanoi_v2_p0_window15.py')
    tasks = [t for t in manifest['tasks'] if t['policy'] == 'P0']
    assert len(tasks) == len({t['key'] for t in tasks}) == 3200
    manifest.update(tasks=tasks, p0_window=[8, 15], rerun_policy='P0',
                    parent_protocol_id=parent_protocol, parent_code_sha256=parent_hashes,
                    reference_policy='Existing pooled PI may be joined later; PI is not rerun',
                    shard_size=SHARD_SIZE,
                    code_sha256={str(p.relative_to(frozen_runtime)): sha(p)
                                 for p in sorted(frozen_runtime.rglob('*'))
                                 if p.is_file() and '__pycache__' not in p.parts})
    manifest.pop('protocol_id')
    manifest['protocol_id'] = v2.modules(args.capsule).digest(manifest)
    dump(manifest_path, manifest)
    # Delete bytecode from bootstrap imports so the modified source is reloaded.
    for directory in frozen_runtime.rglob('__pycache__'):
        shutil.rmtree(directory)
    shards = (len(tasks) + SHARD_SIZE - 1) // SHARD_SIZE
    dump(args.capsule / 'batch_plan.json', {'policy': 'P0', 'p0_window': [8, 15],
         'start_hour': 8, 'optimizations': 3200, 'scheduled_replays': 9600,
         'shards': shards, 'tasks_per_shard': SHARD_SIZE,
         'buffers_pct': [0, 5, 10, 15], 'P1_P2_PI_optimizations': 0})
    print(json.dumps({'include': [{'shard': i} for i in range(1, shards + 1)]}, separators=(',', ':')))


def check(args):
    import numpy as np
    _, full, manifest = runtime(args.capsule)
    store = full.Profiles(args.capsule / 'bundle')
    hours = store.data['hours']
    for name in store.instances:
        daily = store.daily(name)
        for fold in range(1, 5):
            profile, heldout, train = full.base.profiles_for(daily, hours, fold, 'P0', 'shared')
            assert len(train) == 9 and not set(train).intersection(heldout)
            window = (hours >= 8) & (hours < 15)
            manual = 1 / np.mean(1 / daily[train][..., window], axis=(0, 3))
            np.testing.assert_allclose(profile, np.repeat(manual[..., None], len(hours), axis=-1), rtol=1e-12)
    assert set(t['buffer_pct'] for t in manifest['tasks']) == {0, 5, 10, 15}
    assert len({t['seed'] for t in manifest['tasks']}) == 10
    assert manifest['start_hour'] == 8
    print('PASS: 20 instances / 4 folds; harmonic P0 over 9 train dates × 7 hours; 3200 P0 tasks only.')


def run_shard(args):
    _, _, manifest = runtime(args.capsule)
    first = (args.shard - 1) * SHARD_SIZE
    tasks = manifest['tasks'][first:first + SHARD_SIZE]
    if args.shard < 1 or not tasks:
        raise ValueError('Invalid shard')
    failures = []
    runner = args.capsule / 'runtime/scripts/hanoi_v2_experiment.py'
    for index, task in enumerate(tasks, first):
        target = args.output / task['key']
        command = [sys.executable, '-u', str(runner), 'run-task', '--capsule', str(args.capsule),
                   '--policy', 'P0', '--batch', str(index // 250 + 1),
                   '--task-key', task['key'], '--output', str(target)]
        print(f"Running {task['key']} (P0 window [08,15), start 08:00)", flush=True)
        result = subprocess.run(command)
        if result.returncode:
            failures.append(task['key'])
        else:
            # The V2 JSON embeds the exact routes and customer-event CSVs.
            # Keep full diagnostics for failed tasks; compact successful artifacts.
            for p in target.iterdir():
                if p.name == 'task_result.json':
                    continue
                if p.is_dir():
                    shutil.rmtree(p)
                else:
                    p.unlink()
    dump(args.output / 'shard_coverage.json', {'shard': args.shard,
         'expected_keys': [t['key'] for t in tasks], 'technical_failures': failures})
    if failures:
        raise RuntimeError(f'Technical task failures: {failures}')


def aggregate(args):
    v2, full, manifest = runtime(args.capsule)
    manifest['_capsule'] = args.capsule
    expected = {t['key']: t for t in manifest['tasks']}
    records, coverage = v2.collect(full, manifest, expected, args.input)
    plans, cases = [], []
    for key, record in records.items():
        task = record['task']
        plans.append({**task, **record['planning'], 'solution_text': record['planning_solution_text']})
        for case in record['cases']:
            cases.append({**task, 'day_type': case['day_type'], 'test_date': case['test_date'],
                          'plan_returned': record['planning']['feasible'], **(case['replay'] or {})})
    csv_file(args.output / 'all_planning_runs.csv', plans)
    csv_file(args.output / 'all_replay_cases.csv', cases)
    coverage.update(expected_optimizations=3200, completed_optimizations=len(records),
                    scheduled_replays=9600, replay_case_rows=len(cases),
                    p0_window=[8, 15], start_hour=8, protocol_id=manifest['protocol_id'])
    dump(args.output / 'coverage.json', coverage)
    summaries = []
    for day in full.base.DAYS:
        for buffer in (0, 5, 10, 15):
            group = [r for r in cases if r['day_type'] == day and r['buffer_pct'] == buffer]
            returned = [r for r in group if r['plan_returned']]
            violations = [r for r in returned if r['freshness_failure']]
            summaries.append({'day_type': day, 'policy': 'P0', 'buffer_pct': buffer,
                'scheduled': 800, 'records': len(group), 'returned': len(returned),
                'NP_pct': 100 * (len(group) - len(returned)) / len(group) if group else None,
                'RF_pct': 100 * len(violations) / len(returned) if returned else None,
                'mean_makespan_sec': sum(r['makespan_sec'] for r in returned) / len(returned) if returned else None})
    csv_file(args.output / 'P0_summary.csv', summaries)
    (args.output / 'README.md').write_text(
        'P0-only independent rerun: 20 instances × 4 folds × 10 seeds × 4 buffers.\n'
        'Static P0 pools nine training dates and hours [08:00,15:00); departure 08:00.\n'
        'Original 9000-iteration solver, fleet and input data are preserved. P1/P2/PI are not optimized.\n'
        'Each feasible plan is replayed unchanged on its three held-out dates using original freshness limits.\n'
        'No bottom-up incumbent transfer or pooled-PI updates have been applied to these new raw results.\n'
        'Gap is intentionally absent until these new records are joined to the existing PI references.\n'
        'Missing technical jobs are disclosed in coverage.json; they must not be treated as planning NP.\n')
    if any(coverage[k] for k in ('missing', 'conflicting_retries', 'invalid')) or len(cases) != 9600:
        raise RuntimeError('Incomplete or conflicting experiment; see coverage.json')
    print(json.dumps(coverage, indent=2))


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('action', choices=('prepare', 'check', 'run-shard', 'aggregate'))
    parser.add_argument('--capsule', type=Path, required=True)
    parser.add_argument('--experiment-id')
    parser.add_argument('--commit')
    parser.add_argument('--shard', type=int)
    parser.add_argument('--input', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    args.capsule = args.capsule.resolve()
    {'prepare': prepare, 'check': check, 'run-shard': run_shard, 'aggregate': aggregate}[args.action](args)


if __name__ == '__main__':
    main()
