"""Write a separate Excel scenario with makespan substitutions for Table 6."""

import argparse
from collections import defaultdict
import csv
from math import isfinite
from pathlib import Path
from statistics import mean

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter


BASELINE = "cyclic"
DONOR = "adaptive_mk"
TARGETS = (BASELINE, "random")
VARIANTS = (*TARGETS, DONOR)
REPLACEMENTS = 5
SEEDS = set(range(42, 52))
NUMERIC_FIELDS = ("makespan", "cpu_seconds", "wall_seconds")
PERCENT = "+0.000%;-0.000%;0.000%"


def read_runs(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        fields = reader.fieldnames
        required = {"instance", "variant", "seed", "execution_host", *NUMERIC_FIELDS}
        if fields is None or not required <= set(fields):
            raise ValueError("Missing required columns in runs CSV")
        rows = list(reader)
    groups = defaultdict(list)
    for row in rows:
        row["seed"] = int(row["seed"])
        for field in NUMERIC_FIELDS:
            row[field] = float(row[field])
            if not isfinite(row[field]):
                raise ValueError(f"Non-finite {field} in {row['instance']}")
        if row["variant"] in VARIANTS:
            groups[row["instance"], row["variant"]].append(row)
    instances = sorted({instance for instance, _ in groups},
                       key=lambda value: tuple(map(int, value.split("."))))
    if not instances:
        raise ValueError("No Table 6 instances in the source CSV")
    for instance in instances:
        for variant in VARIANTS:
            runs = groups[instance, variant]
            if len(runs) != len(SEEDS) or {row["seed"] for row in runs} != SEEDS:
                raise ValueError(f"Expected one result for each seed 42–51: {instance}, {variant}")
    return fields, rows, groups, instances


def substitutions(groups, instances):
    edited = {}
    for instance in instances:
        worst_adaptive = sorted(groups[instance, DONOR], key=lambda row: (-row["makespan"], row["seed"]))
        for variant in TARGETS:
            best_target = sorted(groups[instance, variant], key=lambda row: (row["makespan"], row["seed"]))
            for rank, (target, donor) in enumerate(zip(best_target[:REPLACEMENTS],
                                                     worst_adaptive[:REPLACEMENTS]), start=1):
                edited[instance, variant, target["seed"]] = (rank, donor["seed"], donor["makespan"])
    return edited


def stats(runs, edited):
    changed = [edited.get((r["instance"], r["variant"], r["seed"]), (None, None, r["makespan"]))[2]
               for r in runs]
    original = [r["makespan"] for r in runs]
    return min(original), mean(original), min(changed), mean(changed), mean(r["cpu_seconds"] for r in runs)


def gap(value, reference):
    return value / reference - 1 if reference > 0 else None


def format_sheet(sheet, widths, *, filters=True):
    sheet.freeze_panes = "A2"
    if filters:
        sheet.auto_filter.ref = sheet.dimensions
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="214F67")
    for column, width in enumerate(widths, start=1):
        sheet.column_dimensions[get_column_letter(column)].width = width


def write_workbook(source, destination):
    fields, rows, groups, instances = read_runs(source)
    edited = substitutions(groups, instances)
    workbook = Workbook()
    overview = workbook.active
    overview.title = "Table 6"
    overview.append(["Variant", "Makespan instances", "Original best gap", "Original mean gap",
                     "Edited best gap", "Edited mean gap", "CPU time gap", "CPU instances"])
    detail = workbook.create_sheet("Per instance")
    detail.append(["Instance", "Variant", "Original best s", "Original mean s", "Edited best s",
                   "Edited mean s", "Mean CPU s", "Original best gap", "Original mean gap",
                   "Edited best gap", "Edited mean gap", "CPU time gap"])
    for instance in instances:
        detail.append([instance, BASELINE, *stats(groups[instance, BASELINE], edited), *([0] * 5)])
    for variant in ("random", DONOR):
        comparisons = []
        for instance in instances:
            baseline = groups[instance, BASELINE]
            target = groups[instance, variant]
            baseline_stats, target_stats = stats(baseline, edited), stats(target, edited)
            gaps = [gap(t, b) for t, b in zip(target_stats, baseline_stats)]
            hosts = {row["execution_host"] for row in baseline + target}
            if len(hosts) != 1 or target_stats[-1] <= 0:
                gaps[-1] = None
            comparisons.append(gaps)
            detail.append([instance, variant, *target_stats, *gaps])
        columns = [[value for value in values if value is not None] for values in zip(*comparisons)]
        overview.append([variant, len(instances), *[mean(c) if c else None for c in columns], len(columns[-1])])

    runs = workbook.create_sheet("Edited runs")
    runs.append([*fields, "original_makespan", "substituted", "adaptive_source_seed"])
    audit = workbook.create_sheet("Substitutions")
    audit.append(["Instance", "Recipient variant", "Recipient seed", "Replacement rank",
                  "Original makespan s", "Adaptive source seed", "Substituted makespan s"])
    for row in rows:
        original = row["makespan"]
        replacement = edited.get((row["instance"], row["variant"], row["seed"]))
        rank, seed, makespan = replacement or (None, None, original)
        runs.append([makespan if field == "makespan" else row[field] for field in fields]
                    + [original, replacement is not None, seed])
        if replacement:
            audit.append([row["instance"], row["variant"], row["seed"], rank, original, seed, makespan])

    notes = workbook.create_sheet("Method")
    for entry in [
        ["Item", "Details"],
        ["Data status", "Edited scenario. Substituted makespans are values from adaptive_mk runs."],
        ["Source", str(source.resolve())],
        ["Selection", "Per instance, replace the five lowest makespans in cyclic and random with the same five highest adaptive_mk makespans."],
        ["Order", "Highest donor replaces lowest recipient, then follow rank. Equal makespans are ordered by ascending seed."],
        ["Seed attribution", "The original recipient seed and the adaptive donor seed are both recorded."],
        ["Metrics", "Only makespan is substituted. CPU time, wall time and other metadata retain their original values."],
        ["Gaps", "Edited gaps use the edited cyclic baseline. Compute gaps per instance, then take their arithmetic mean."],
        ["CPU matching", "CPU time gaps use only comparisons whose ten seeds on both sides share one execution host."],
        ["Scope", "Table 6 only. The source CSV and the original Tables 7–8 results are retained separately."],
        ["Coverage", f"{len(instances)} instances and {len(edited)} substitutions."],
    ]:
        notes.append(entry)

    format_sheet(overview, (20, 20, 20, 20, 18, 18, 16, 17))
    format_sheet(detail, (15, 20, 17, 17, 17, 17, 15, 20, 20, 18, 18, 16))
    format_sheet(runs, [max(14, len(field) + 3) for field in (*fields, "original_makespan", "substituted",
                                                            "adaptive_source_seed")])
    format_sheet(audit, (15, 23, 17, 19, 24, 22, 27))
    format_sheet(notes, (22, 150), filters=False)
    for sheet, start, stop in ((overview, 3, 7), (detail, 8, 12)):
        for cells in sheet.iter_rows(min_row=2, min_col=start, max_col=stop):
            for cell in cells:
                cell.number_format = PERCENT
    for sheet, columns in ((detail, (3, 4, 5, 6, 7)), (audit, (5, 7))):
        for column in columns:
            for (cell,) in sheet.iter_rows(min_row=2, min_col=column, max_col=column):
                cell.number_format = "0.000"
    workbook.save(destination)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runs_csv", type=Path)
    parser.add_argument("output_xlsx", type=Path)
    args = parser.parse_args()
    if args.output_xlsx.suffix.lower() != ".xlsx":
        parser.error("output must have an .xlsx extension")
    write_workbook(args.runs_csv, args.output_xlsx)
