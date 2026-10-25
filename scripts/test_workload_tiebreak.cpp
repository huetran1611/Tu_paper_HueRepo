// Semantic tests for the lexicographic comparator used by both ablations.
#define WORKLOAD_ABLATION_BUDGET_MULTIPLIER 1
#define main workload_ablation_cli_main
#include "../src_v2/tabubu_TimeDependent_workload_tiebreak_impl.hpp"
#undef main
#include <cassert>

int main() {
    CFG_WORKLOAD_TIE_BREAK = true;
    // Primary makespan always wins, regardless of the secondary workload.
    assert(better_lexicographic(9.0, 10000.0, 10.0, 1.0));
    assert(!better_lexicographic(11.0, 1.0, 10.0, 10000.0));
    assert(better_lexicographic(10.0, 12.0, 10.0, 13.0));
    assert(!better_lexicographic(10.0, 14.0, 10.0, 13.0));
    assert(!better_lexicographic(10.0, 13.0, 10.0, 13.0));
    Solution a{};
    a.truck_route_times = {3.0, 4.0};
    a.drone_route_times = {12.0};
    assert(fabs(workload_l2(a) - 13.0) < 1e-12);
    int ties = 1;
    double incumbent_workload = 14.0;
    assert(better_score_with_workload_tie(10.0, 10.0, ties, a, incumbent_workload));
    assert(incumbent_workload == 13.0);
    assert(!better_score_with_workload_tie(11.0, 10.0, ties, a, incumbent_workload));
    assert(admissible_by_tabu_or_aspiration(true, 10.0, 10.0, a, 14.0));
    assert(!admissible_by_tabu_or_aspiration(true, 11.0, 10.0, a, 14.0));
    CFG_WORKLOAD_TIE_BREAK = false;
    assert(!better_lexicographic(10.0, 12.0, 10.0, 13.0));
    assert(!admissible_by_tabu_or_aspiration(true, 10.0, 10.0, a, 14.0));
    assert(better_lexicographic(9.0, 10000.0, 10.0, 1.0));
    cout << "PASS: primary priority, L2 tie-breaking, tabu aspiration, and disabled-workload control.\n";
}
