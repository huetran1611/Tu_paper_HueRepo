#!/usr/bin/env python3
"""Reoptimize Hanoi policies at 48/60/72 minutes and with a static Q25 baseline."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import shutil
import statistics
from collections import defaultdict
from pathlib import Path

import hanoi_case_study_33_cloud as base

SCALES = (0.8, 1.0, 1.2)
POLICIES = ('P0_mean', 'P0_q25', 'P1', 'P2')


def percentile(values, probability=0.25):
    """Linear interpolation at (N-1)*p (inclusive sample quantile, R type 7)."""
    ordered = sorted(values)
    if not ordered or not 0 <= probability <= 1:
        raise ValueError('A nonempty sample and probability in [0,1] are required')
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    return ordered[lower] + (position - lower) * (ordered[upper] - ordered[lower])


def tasks(datasets=base.DATASETS, seeds=base.SEEDS, hours=base.START_HOURS):
    result = []
    for dataset in datasets:
        for seed in seeds:
            for policy in POLICIES[:2]:
                result.append(dict(task_id=f'{policy}-{dataset}-seed{seed:02d}',
                                   policy=policy, dataset=dataset, seed=seed))
            for hour in hours:
                result.append(dict(task_id=f'P1-{dataset}-{hour:02d}h-seed{seed:02d}',
                                   policy='P1', dataset=dataset, seed=seed, start_hour=hour))
                for profile in base.PROFILES:
                    result.append(dict(task_id=f'P2-{dataset}-{profile}-{hour:02d}h-seed{seed:02d}',
                                       policy='P2', dataset=dataset, seed=seed,
                                       start_hour=hour, actual_profile=profile))
    return result


def compact(task):
    return [POLICIES.index(task['policy']), base.dataset_index(task['dataset']), task['seed'],
            task.get('start_hour', 7), base.PROFILES.index(task.get('actual_profile', 'weekday'))]


def expand(value):
    policy, index, seed, hour, profile = map(int, value)
    dataset = f'set_{index:02d}'
    if dataset not in base.DATASETS or seed not in base.SEEDS or hour not in base.START_HOURS:
        raise ValueError(f'Invalid task {value}')
    if not 0 <= policy < len(POLICIES) or not 0 <= profile < len(base.PROFILES):
        raise ValueError(f'Invalid policy/profile {value}')
    return next(task for task in tasks([dataset], [seed], [hour])
                if task['policy'] == POLICIES[policy]
                and (policy != 3 or task['actual_profile'] == base.PROFILES[profile]))


def build_matrix(job_count=250):
    batches = [[] for _ in range(job_count)]
    loads = [0] * job_count
    # Place expensive replay tasks first; balance optimization and replay work.
    ordered = sorted(tasks(), key=lambda task: POLICIES.index(task['policy']))
    for task in ordered:
        index = min(range(job_count), key=lambda i: (loads[i], len(batches[i]), i))
        batches[index].append(task)
        replays = 12 if task['policy'].startswith('P0') else 3 if task['policy'] == 'P1' else 0
        loads[index] += 600 + replays * 2
    return {'include': [dict(batch_id=f'sensitivity-batch{i+1:03d}', task_count=len(batch),
                             tasks_json=json.dumps([compact(task) for task in batch], separators=(',', ':')))
                        for i, batch in enumerate(batches) if batch]}


def scale_instance(source, target, scale):
    if scale not in SCALES:
        raise ValueError('Supported freshness scales: 0.8, 1.0, 1.2')
    lines = source.read_text().splitlines()
    header = 'X Y Dronable Demand Drone_service Truck_service Lw'
    start = lines.index(header) + 1
    count = 0
    for index in range(start, len(lines)):
        if not lines[index].strip() or lines[index].startswith('#'):
            continue
        fields = lines[index].split()
        if len(fields) != 7:
            raise ValueError(f'Expected 7 customer fields in {source}:{index+1}')
        float_values = list(map(float, fields))
        if float_values[-1] != 3600:
            raise ValueError(f'Expected original 60-minute limit in {source}')
        fields[-1] = f'{float_values[-1] * scale:.6f}'
        lines[index] = ' '.join(fields)
        count += 1
    if count != 100:
        raise ValueError(f'Expected 100 customers in {source}, found {count}')
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('\n'.join(lines) + '\n')


def prepare(repo, profile_root, work_root, dataset, scale):
    base.build_dataset_profiles(repo, profile_root, work_root, dataset)
    output = work_root / dataset
    source = base.source_paths(repo, dataset)
    scaled = output / f'freshness_{round(scale*100):03d}.txt'
    scale_instance(source['instance'], scaled, scale)
    q25_path = base.speed_path(work_root, dataset, 'P0_q25')
    metadata = output / 'sensitivity.json'
    if not q25_path.exists():
        # Compute quantile from unrounded hourly arc speeds, like the original P0 mean.
        nodes = {profile: base.load_node_profiles(source['points'], base.load_road_profiles(
            profile_root / base.PROFILE_FILENAMES[profile])) for profile in base.PROFILES}
        values = [base.edge_speed(nodes[p][i], nodes[p][j], segment)
                  for i in range(101) for j in range(101) for segment in range(12)
                  for p in base.PROFILES]
        speed = percentile(values)
        with q25_path.open('w') as stream:
            stream.write(f'# Static Q25={speed:.6f} km/h; R7 quantile; all pairs including self\n')
            for i in range(101):
                for j in range(101):
                    for hour in base.TRAFFIC_HOURS:
                        stream.write(f'{i} {j} {hour} {speed:.6f}\n')
        metadata.write_text(json.dumps(dict(dataset=dataset, p0_mean_kph=sum(values)/len(values),
                                           p0_q25_kph=speed, quantile_method='linear (R7)',
                                           speed_observations=len(values)), indent=2) + '\n')
    source = {**source, 'instance': scaled}
    return source, json.loads(metadata.read_text())


def run_batch(repo, binary, profile_root, work_root, batch, output, scale, smoke=False):
    output.mkdir(parents=True, exist_ok=True)
    if smoke:
        base.MAX_ITERATIONS, base.SEGMENT_ITERATIONS, base.TIME_LIMIT_SECONDS = 2, 1, 60
    contexts = {dataset: prepare(repo, profile_root, work_root, dataset, scale)
                for dataset in sorted({task['dataset'] for task in batch})}
    optimizations, evaluations, failures = [], [], []
    for position, task in enumerate(batch, 1):
        policy, dataset, seed = task['policy'], task['dataset'], task['seed']
        hour = task.get('start_hour', 7)
        source, metadata = contexts[dataset]
        speed_label = 'P0' if policy == 'P0_mean' else task.get('actual_profile', policy)
        task_dir = output / task['task_id']
        task_dir.mkdir(parents=True, exist_ok=True)
        print(f'[{position}/{len(batch)}] scale={scale} {task["task_id"]}', flush=True)
        shared = dict(freshness_scale=scale, freshness_limit_min=60*scale,
                      **metadata, policy=policy, seed=seed)
        try:
            _, elapsed, _ = base.run_command(base.solver_command(binary, source,
                base.speed_path(work_root, dataset, speed_label), hour, seed),
                task_dir, task_dir / 'solver.log', base.TIME_LIMIT_SECONDS + 120, (0,))
            saved = task_dir / f'final_solution_{task["task_id"]}.txt'
            shutil.copy2(task_dir / 'output_solution_best.txt', saved)
            parsed = base.parse_solution(saved)
            optimizations.append(dict(**shared, task_id=task['task_id'], planned_start_hour=hour,
                planned_profile=task.get('actual_profile', 'expected'),
                feasibility=parsed['feasibility'], planned_makespan_s=parsed['makespan_s'],
                wall_time_s=elapsed, max_iterations=base.MAX_ITERATIONS,
                segment_iterations=base.SEGMENT_ITERATIONS, time_limit_s=base.TIME_LIMIT_SECONDS,
                solution_file=str(saved.relative_to(output))))
            if policy.startswith('P0'):
                cases = [(p, h) for p in base.PROFILES for h in base.START_HOURS]
            elif policy == 'P1':
                cases = [(p, hour) for p in base.PROFILES]
            else:
                p = task['actual_profile']
                evaluations.append(dict(**shared, evaluation_id=task['task_id'], actual_profile=p,
                    start_hour=hour, feasibility=parsed['feasibility'], return_code=0,
                    realized_makespan_s=parsed['makespan_s'],
                    deadline_violation=parsed['deadline_violation'],
                    energy_violation=parsed['energy_violation'],
                    capacity_violation=parsed['capacity_violation'], evaluation_wall_time_s=0))
                cases = []
            for profile, replay_hour in cases:
                replay = base.evaluate_solution(binary, source,
                    base.speed_path(work_root, dataset, profile), saved, policy, dataset,
                    profile, replay_hour, seed, task_dir / 'replays' / profile / f'{replay_hour:02d}h')
                evaluations.append({**shared, **replay})
        except Exception as error:
            failures.append(dict(task_id=task['task_id'], error=repr(error)))
            (task_dir / 'failure.txt').write_text(repr(error) + '\n')
        base.write_csv(output / 'optimization_results.csv', optimizations)
        base.write_csv(output / 'evaluation_results.csv', evaluations)
    (output / 'failures.json').write_text(json.dumps(failures, indent=2) + '\n')
    (output / 'config.json').write_text(json.dumps(dict(freshness_scale=scale,
        freshness_limit_min=scale*60, smoke=smoke, input_tasks=batch,
        solver_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
        solver_source_sha256=hashlib.sha256((repo / 'src_v2/tabubu_HanoiTraffic_v2.cpp').read_bytes()).hexdigest(),
        speed_profile_sha256={p: hashlib.sha256((profile_root / f).read_bytes()).hexdigest()
                              for p, f in base.PROFILE_FILENAMES.items()}), indent=2) + '\n')
    if failures:
        raise SystemExit(f'{len(failures)} sensitivity tasks failed')


def collect(root, filename, key):
    records = {}
    for path in sorted(root.rglob(filename)):
        with path.open(newline='') as stream:
            for row in csv.DictReader(stream):
                identity = (float(row['freshness_scale']), row[key])
                if identity in records:
                    raise ValueError(f'Duplicate record {identity} in {path}')
                records[identity] = row
    return list(records.values())


def summarize(evaluations, min_pairs=30):
    benchmarks = {(float(r['freshness_scale']), r['dataset'], r['actual_profile'],
                   int(r['start_hour']), int(r['seed'])): r for r in evaluations if r['policy'] == 'P2'}
    groups = defaultdict(list)
    for row in evaluations:
        scale = float(row['freshness_scale'])
        groups[scale, row['policy'], row['actual_profile']].append(row)
        groups[scale, row['policy'], 'Overall'].append(row)
    result = []
    for (scale, policy, profile), rows in sorted(groups.items()):
        feasible = [r for r in rows if r['feasibility'] == 'FEASIBLE']
        regrets = []
        if policy != 'P2':
            for row in feasible:
                reference = benchmarks.get((scale, row['dataset'], row['actual_profile'],
                                             int(row['start_hour']), int(row['seed'])))
                if reference and reference['feasibility'] == 'FEASIBLE':
                    regrets.append(100*(float(row['realized_makespan_s']) /
                                       float(reference['realized_makespan_s']) - 1))
        result.append(dict(freshness_scale=scale, freshness_limit_min=60*scale,
                           policy=policy, actual_profile=profile, runs=len(rows), feasible=len(feasible),
                           failure_rate_pct=100*(len(rows)-len(feasible))/len(rows),
                           mean_feasible_makespan_s=statistics.fmean(float(r['realized_makespan_s'])
                               for r in feasible) if feasible else None,
                           conditional_regret_pct=statistics.fmean(regrets) if len(regrets) >= min_pairs else None,
                           conditional_regret_raw_pct=statistics.fmean(regrets) if regrets else None,
                           regret_cases=len(regrets), minimum_reporting_pairs=min_pairs))
    return result


def aggregate(root, output, scales=SCALES, smoke=False):
    from openpyxl import Workbook

    optimizations = collect(root, 'optimization_results.csv', 'task_id')
    evaluations = collect(root, 'evaluation_results.csv', 'evaluation_id')
    if smoke:
        scales = tuple(sorted({float(r['freshness_scale']) for r in evaluations}))
    expected_tasks = {t['task_id'] for t in tasks()}
    expected_evaluations = {(policy, dataset, profile, hour, seed)
        for policy in POLICIES for dataset in base.DATASETS for profile in base.PROFILES
        for hour in base.START_HOURS for seed in base.SEEDS}
    validation = []
    for scale in scales:
        actual_tasks = {r['task_id'] for r in optimizations if float(r['freshness_scale']) == scale}
        actual_evaluations = {(r['policy'], r['dataset'], r['actual_profile'], int(r['start_hour']), int(r['seed']))
                              for r in evaluations if float(r['freshness_scale']) == scale}
        validation.extend([dict(freshness_scale=scale, item='optimization_grid', found=len(actual_tasks),
            expected=len(expected_tasks), passed=actual_tasks == expected_tasks),
            dict(freshness_scale=scale, item='evaluation_grid', found=len(actual_evaluations),
            expected=len(expected_evaluations), passed=actual_evaluations == expected_evaluations)])
    actual_scales = {float(r['freshness_scale']) for r in optimizations + evaluations}
    validation.append(dict(item='scale_grid', found=len(actual_scales), expected=len(scales),
                           passed=actual_scales == set(scales)))
    configs = [json.loads(p.read_text()) for p in root.rglob('config.json')]
    if not smoke:
        validation.append(dict(item='production_configs', found=len(configs), expected='non-smoke',
                               passed=bool(configs) and all(not c['smoke'] for c in configs)))
        signatures = {(c['solver_sha256'], c['solver_source_sha256'],
                       json.dumps(c['speed_profile_sha256'], sort_keys=True)) for c in configs}
        validation.append(dict(item='consistent_solver_and_traffic', found=len(signatures), expected=1,
                               passed=len(signatures) == 1))
        expected_budget = all(int(r['max_iterations']) == 9000
                              and int(r['segment_iterations']) == 300
                              and int(r['time_limit_s']) == 600 for r in optimizations)
        validation.append(dict(item='production_search_budget', expected='9000/300/600',
                               passed=bool(optimizations) and expected_budget))
    summary = summarize(evaluations)
    departures = []
    for scale in scales:
        for profile in (*base.PROFILES, 'Overall'):
            means = {}
            for hour in base.START_HOURS:
                group = [r for r in evaluations if float(r['freshness_scale']) == scale
                         and r['policy'] == 'P2' and int(r['start_hour']) == hour
                         and (profile == 'Overall' or r['actual_profile'] == profile)]
                feasible = [float(r['realized_makespan_s']) for r in group if r['feasibility'] == 'FEASIBLE']
                if feasible:
                    means[hour] = statistics.fmean(feasible)
                departures.append(dict(freshness_scale=scale, actual_profile=profile, start_hour=hour,
                    runs=len(group), feasible=len(feasible), mean_feasible_makespan_s=means.get(hour)))
            best = min(means, key=means.get) if means else None
            for row in departures:
                if row['freshness_scale'] == scale and row['actual_profile'] == profile:
                    row['best_departure'] = best
                    row['saving_vs_08_pct'] = 100*(means[8]-means[best])/means[8] if 8 in means else None
    output.parent.mkdir(parents=True, exist_ok=True)
    workbook = Workbook()
    workbook.remove(workbook.active)
    for name, records in [('Summary', summary), ('Departure', departures), ('Optimization', optimizations),
                           ('Evaluations', evaluations), ('Validation', validation)]:
        base.add_sheet(workbook, name, records)
    notes = [dict(item='Design', definition='Reoptimize every policy at 48, 60, 72 minutes; 20 instances; 10 seeds.'),
             dict(item='Static Q25', definition='Per-instance R7 25th percentile across 3 profiles, 12 hours, all 101^2 pairs including self.'),
             dict(item='Regret', definition='Paired on scale, instance, profile, hour, seed. Both feasible. Report only >=30 pairs.'),
             dict(item='Departure', definition='Means over feasible P2 runs only; feasible counts must be considered when comparing hours.'),
             dict(item='Smoke', definition=str(smoke)),
             dict(item='Complete', definition=str(all(r['passed'] for r in validation)))]
    base.add_sheet(workbook, 'Notes', notes)
    workbook.save(output)
    base.write_csv(output.with_suffix('.summary.csv'), summary)
    output.with_suffix('.validation.json').write_text(json.dumps(validation, indent=2) + '\n')
    print(json.dumps(dict(workbook=str(output), smoke=smoke,
                          complete=all(r['passed'] for r in validation), validation=validation)))
    if not smoke and not all(r['passed'] for r in validation):
        raise SystemExit('Sensitivity experiment is incomplete; see Validation sheet')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('matrix')
    run = commands.add_parser('run-batch')
    run.add_argument('--repo', type=Path, default=Path.cwd())
    run.add_argument('--binary', type=Path, required=True)
    run.add_argument('--profile-root', type=Path, required=True)
    run.add_argument('--work-root', type=Path, required=True)
    run.add_argument('--output', type=Path, required=True)
    run.add_argument('--scale', type=float, choices=SCALES, required=True)
    run.add_argument('--tasks-json', required=True)
    run.add_argument('--smoke', action='store_true')
    combine = commands.add_parser('aggregate')
    combine.add_argument('--input-root', type=Path, required=True)
    combine.add_argument('--output', type=Path, required=True)
    combine.add_argument('--scale', type=float, choices=SCALES)
    combine.add_argument('--smoke', action='store_true')
    args = parser.parse_args()
    if args.command == 'matrix':
        print(json.dumps(build_matrix(), separators=(',', ':')))
    elif args.command == 'run-batch':
        run_batch(args.repo.resolve(), args.binary.resolve(), args.profile_root.resolve(),
                  args.work_root.resolve(), [expand(t) for t in json.loads(args.tasks_json)],
                  args.output.resolve(), args.scale, args.smoke)
    else:
        aggregate(args.input_root, args.output, (args.scale,) if args.scale is not None else SCALES, args.smoke)


if __name__ == '__main__':
    main()
