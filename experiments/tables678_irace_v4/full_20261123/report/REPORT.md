# Tables 6–8 — irace v4 configuration

Completed 9600/9600 runs with feasible results.
Full iteration budget: 9168. Feasible at the time limit: 432.
Seeds: 42, 43, 44, 45, 46, 47, 48, 49, 50, 51.
V4 solver with the winning irace parameters, on Windows. The n=200 wall-clock caps are 7,200 s at standard budget and 14,400 s at doubled budget. Feasible incumbents at the limit enter the tables.
Time is solver-process CPU user+system. Gaps are computed per instance and averaged with equal instance weight.

CPU-time gaps use only paired instance groups measured in the same runner pool. Makespan results include both pools, with the same wall-clock caps.

## Table 6 — baseline cyclic

| Variant | Instances/96 | Best gap % | Mean gap % | CPU time gap % |
|---|---:|---:|---:|---:|
| random | 96/96 | +0.086 | +0.172 | +1.139 (96 instances) |
| adaptive_mk | 96/96 | +0.298 | +0.306 | +4.784 (96 instances) |

## Table 7 — baseline adaptive_mk

| Variant | Instances/96 | Best gap % | Mean gap % | CPU time gap % |
|---|---:|---:|---:|---:|
| switch | 96/96 | -1.044 | -2.301 | +4.676 (96 instances) |
| mk_tie | 96/96 | -0.335 | -0.808 | +25.920 (96 instances) |
| mk_tie_2x | 96/96 | -0.887 | -1.629 | +119.049 (91 instances) |
| mk_2x | 96/96 | -0.560 | -0.773 | +62.982 (91 instances) |

## Table 8 — baseline switch

| Variant | Instances/96 | Best gap % | Mean gap % | CPU time gap % |
|---|---:|---:|---:|---:|
| switch_dr | 96/96 | -0.753 | -1.653 | +27.823 (96 instances) |
| switch_sa | 96/96 | -0.886 | -1.711 | +3.083 (96 instances) |
| switch_dr_sa | 96/96 | -1.159 | -2.324 | +29.379 (95 instances) |

