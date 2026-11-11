// Semantic regression tests against hand calculations for Sections 3 and 5.
// Build without -DNDEBUG; no solver or external testing library is required.
#define main solver_cli_main
#include "tabubu_TimeDependent_v3.cpp"
#undef main
#include <cassert>

static void expect_near(double actual, double expected, double tolerance = 1e-8) {
    if (fabs(actual-expected) > tolerance * max(1.0, fabs(expected))) {
        cerr << "Expected " << expected << ", got " << actual << "\n";
        abort();
    }
}

static void fixture(int customers, int trucks, int drones) {
    n = customers; h = trucks; d = drones;
    loc.assign(n+1, Point{});
    demand.assign(n+1, 1.0); demand[0] = 0;
    serve_truck.assign(n+1, 0); serve_drone.assign(n+1, 0);
    deadline.assign(n+1, 1000000.0);
    served_by_drone.assign(n+1, 1); served_by_drone[0] = 0;
    time_segments_sigma = {1}; CFG_SEGMENT_LENGTH_SEC = 3600;
    CFG_TIME_BOUNDARIES_SEC.clear(); CFG_SIGMA_EXPLICIT = false;
    configure_time_segment_boundaries();
    vmax = 1; initialize_time_dependent_truck_speed_model();
    Dh = Dd = 100; E = 1e12;
    height = 0; v_fly_drone = 1; v_take_off = 1; v_landing = 1;
    power_beta = 2; power_gamma = 3;
    distance_matrix.assign(n+1, vd(n+1, 0));
    for (int i = 0; i <= n; ++i) for (int j = 0; j <= n; ++j)
        distance_matrix[i][j] = abs(i-j)*10;
    drone_distance_matrix = distance_matrix;
    paper_incumbent.reset(); incumbent_improved_in_segment = false;
    search_deadline.reset(); clear_paper_tabu(); reset_penalty_coefficients();
    CFG_TABU_BASE_OVERRIDE = 3; update_tabu_tenures();
    srand(123);
}

static Solution solution(vvi trucks, vvi drones = {}) {
    Solution sol{}; sol.truck_routes = std::move(trucks); sol.drone_routes = std::move(drones);
    return recalculate_solution(sol);
}

static void test_fifo_and_final_interval() {
    fixture(1, 1, 0);
    time_segments_sigma = {1, .5}; CFG_SEGMENT_LENGTH_SEC = 10;
    configure_time_segment_boundaries(); initialize_time_dependent_truck_speed_model();
    distance_matrix[0][1] = 15;
    expect_near(truck_leg_time(0,1,0), 20); // 10m before boundary, 5m at 0.5m/s
    expect_near(truck_leg_time(0,1,10), 30);
    expect_near(truck_leg_time(0,1,100), 30); // final interval never wraps
    assert(get_time_segment(100.0/3600.0) == 1);
    double previous_arrival = -1;
    for (int k = 0; k < 200; ++k) {
        double start = k*.25, arrival = start + truck_leg_time(0,1,start);
        assert(arrival >= previous_arrival); previous_arrival = arrival;
    }
    CFG_SEGMENT_LENGTH_SEC = .1;
    configure_time_segment_boundaries(); initialize_time_dependent_truck_speed_model();
    expect_near(truck_leg_time(0,1,.1), 30); // fractional boundaries make progress
    CFG_TIME_BOUNDARIES_SEC = {0,5,20}; CFG_SIGMA_EXPLICIT = true;
    time_segments_sigma = {1,.5,2};
    configure_time_segment_boundaries(); initialize_time_dependent_truck_speed_model();
    // 5m at 1m/s, 7.5m at 0.5m/s, then 2.5m at 2m/s.
    expect_near(truck_leg_time(0,1,0),21.25);
    expect_near(truck_leg_time(0,1,100),7.5);
}

static void test_pickup_limits_and_successive_trips() {
    fixture(2,1,0);
    serve_truck[1] = 7; serve_truck[2] = 13;
    deadline[1] = 43; deadline[2] = 20;
    auto evaluated = compute_truck_route_time({0,1,2,0});
    expect_near(evaluated.first,60); expect_near(evaluated.second,0);
    deadline[1] = 40; // pickup 1 completes at 17, returns at 60: 43 seconds
    expect_near(compute_truck_route_time({0,1,2,0}).second,3.0/40);
    // Late collection is legal: the limit concerns only time ON BOARD.
    deadline[1] = 10;
    expect_near(compute_truck_route_time({0,1,0},10000).second,0);

    fixture(2,1,0);
    time_segments_sigma = {1,.5}; CFG_SEGMENT_LENGTH_SEC = 30;
    configure_time_segment_boundaries(); initialize_time_dependent_truck_speed_model();
    deadline[2] = 30;
    expect_near(compute_truck_route_time({0,1,0,2,0}).first,90);
    expect_near(compute_truck_route_time({0,1,0,2,0}).second,1.0/3);
    expect_near(compute_truck_route_time({0,2,0}).first,50);
    expect_near(compute_truck_route_time({0,2,0}).second,0);
    Solution base = solution({{0,1,0,2,0}});
    Solution candidate = base; candidate.truck_routes[0] = {0,2,0,1,0};
    SolutionEvaluationContext context(base);
    context.score_candidate(candidate, {{true,0,0}}, solution_score_makespan);
    Solution full = recalculate_solution(candidate);
    expect_near(candidate.total_makespan,full.total_makespan);
    expect_near(candidate.deadline_violation,full.deadline_violation);
}

static void test_drone_energy_and_separate_distances() {
    fixture(2,0,1);
    // Each flight leg lasts 1s; phase overhead adds 3s, giving 4s per leg.
    for (int i=0;i<=n;++i) for (int j=0;j<=n;++j) if(i!=j) drone_distance_matrix[i][j]=1;
    height=2; v_take_off=2; v_landing=1;
    demand[1]=1; demand[2]=3;
    serve_drone[1]=50; serve_drone[2]=70;
    // Loads 0,1,4: powers 3,5,11. Ground service contributes no energy.
    expect_near(compute_drone_route_energy({0,1,2,0}).first,76);
    expect_near(compute_drone_route_time({0,1,2,0}).first,132);
    // Reverse loads 0,3,4: powers 3,9,11 -> 92J.
    expect_near(compute_drone_route_energy({0,2,1,0}).first,92);
    E=30;
    expect_near(compute_drone_route_energy({0,1,2,0}).second,46); // excess counted once
    // Separate trips: 32J and 48J; battery/load reset at the depot.
    expect_near(compute_drone_route_energy({0,1,0,2,0}).first,80);
    expect_near(compute_drone_route_energy({0,1,0,2,0}).second,20);
    Dd=2;
    auto sol=solution({},{{0,1,0,2,0}});
    expect_near(sol.capacity_violation,.25); // (0 + (3-2)/2) / 2 trips
    expect_near(sol.energy_violation,1.0/3); // (2/30 + 18/30) / 2 trips
    expect_near(check_drone_route_feasibility({0,1,2,0})[3],1); // once per trip
    Dh=2;
    expect_near(check_truck_route_feasibility({0,1,2,0})[3],1);
}

static void test_incumbent_precedes_tabu_and_acceptance() {
    fixture(2,2,0);
    loc[1].x=10; loc[2].x=-10;
    distance_matrix[1][2]=distance_matrix[2][1]=20;
    distance_matrix[0][2]=distance_matrix[2][0]=10;
    Solution initial=solution({{0,1,2,0},{0}});
    expect_near(initial.total_makespan,40);
    observe_complete_candidate(initial);
    // Every newly added edge is tabu; aspiration is impossible at reference -inf.
    for(int v=0;v<h;++v) for(int i=0;i<=n;++i) for(int j=0;j<=n;++j)
        paper_tabu[0][{v,i,j}]=100;
    Solution retained=local_search(initial,0,0,-numeric_limits<double>::infinity(),solution_score_makespan);
    assert(!paper_candidate_found);
    expect_near(retained.total_makespan,40);
    assert(paper_incumbent); expect_near(paper_incumbent->total_makespan,20);
    assert(incumbent_improved_in_segment);
    // Incomplete assignments cannot enter the incumbent even with a lower score.
    auto partial=solution({{0},{0}});
    observe_complete_candidate(partial);
    expect_near(paper_incumbent->total_makespan,20);
    // A feasible alternative evaluated by construction survives a search timeout.
    auto construction_best = paper_incumbent;
    CFG_MAX_ITERATIONS=1; CFG_MAX_ITER_PER_SEGMENT=1; CFG_TIME_LIMIT_SEC=1e-12;
    vd current_history, best_history; vector<bool> feasible_history;
    auto returned=tabu_search(initial,1,current_history,best_history,feasible_history,construction_best);
    expect_near(returned.total_makespan,20);
    CFG_TIME_LIMIT_SEC=0;
}

static void test_neighbourhoods_and_repair() {
    fixture(6,2,2);
    served_by_drone[3]=0;
    Solution initial=solution({{0,1,2,0,3,0},{0,4,0}},{{0,5,6,0},{0}});
    int insertion_count=0; bool front=false, middle=false, back=false;
    visit_insertions(Trips{{1},{2}},vi{3},[&](const Trips& trips) {
        ++insertion_count;
        front |= trips==Trips{{3},{1},{2}};
        middle |= trips==Trips{{1},{3},{2}};
        back |= trips==Trips{{1},{2},{3}};
    });
    assert(insertion_count==7 && front && middle && back);
    for(int scope=0;scope<2;++scope) for(int op=0;op<9;++op) {
        clear_paper_tabu();
        auto cost=scope ? solution_score_workload : solution_score_makespan;
        Solution result=paper_local_search(initial,op,0,numeric_limits<double>::infinity(),cost,scope);
        assert(paper_candidate_found); assert(check_solution_integrity(result));
        Solution full=recalculate_solution(result);
        expect_near(result.total_makespan,full.total_makespan);
        expect_near(result.capacity_violation,full.capacity_violation);
        expect_near(result.energy_violation,full.energy_violation);
        expect_near(result.deadline_violation,full.deadline_violation);
        commit_pending_tabu_attributes(0);
        // Pure reordering of whole trips can preserve every directed trip edge.
        // In that case the chosen edge-based tabu scheme has no removed edges.
        vector<MoveEdge> expected_removed;
        for(int v=0;v<h+d;++v) {
            const auto& before=v<h?initial.truck_routes[v]:initial.drone_routes[v-h];
            const auto& after=v<h?result.truck_routes[v]:result.drone_routes[v-h];
            auto old_edges=vehicle_edges(v,before),new_edges=vehicle_edges(v,after);
            set_difference(old_edges.begin(),old_edges.end(),new_edges.begin(),new_edges.end(),back_inserter(expected_removed));
        }
        assert(paper_tabu[op].size()==expected_removed.size());
        for(const auto& edge:expected_removed) assert(paper_tabu[op].count(edge));
        for(auto entry:paper_tabu[op]) assert(entry.second>=4 && entry.second<=7);
    }
    // Empty-trip deletion and reinsertion into an unused eligible vehicle.
    auto repaired=repair_solution_common(initial,{2,4,6});
    assert(check_solution_integrity(repaired));
    DESTROY_RATE=.5;
    assert(check_solution_integrity(destroy_worst_repair_random(initial)));
    // Validation must reject duplicates, out-of-range ids and drone-ineligible ids.
    auto invalid=initial; invalid.truck_routes[0][1]=n+1; assert(!check_solution_integrity(invalid));
    invalid=initial; invalid.drone_routes[0][1]=3; assert(!check_solution_integrity(invalid));
}

static void test_customer_and_block_tabu() {
    // With all customer/block attributes forbidden and NO forbidden edges,
    // each entity-based operator must retain the current state. Aspiration
    // must override those attributes independently of feasibility checks.
    for(int op : {0,1,2,5,6,7}) {
        fixture(6,3,0);
        Solution initial=solution({{0,1,2,0},{0,3,4,0},{0,5,6,0}});
        for(int i=1;i<=n;++i) {
            paper_entity_tabu[op][{TabuEntityKind::Customer,i,0}]=100;
            for(int j=1;j<=n;++j) if(i!=j)
                paper_entity_tabu[op][{TabuEntityKind::OrderedPair,i,j}]=100;
        }
        auto blocked=paper_local_search(initial,op,0,-numeric_limits<double>::infinity(),solution_score_makespan,true);
        assert(!paper_candidate_found && pending_move_entities.empty());
        assert(blocked.truck_routes==initial.truck_routes);
        paper_local_search(initial,op,0,numeric_limits<double>::infinity(),solution_score_makespan,true);
        assert(paper_candidate_found); // aspiration overrides entity tabu
        assert(pending_move_entities.size()==size_t(op==0||op==2 ? 1 : 2));
        int pairs=0;
        for(const auto& entity:pending_move_entities)
            pairs += get<0>(entity)==TabuEntityKind::OrderedPair;
        assert(pairs==(op==2||op==5 ? 1 : op==6 ? 2 : 0));
        clear_pending_tabu_attributes(); // a rejected candidate commits nothing
        assert(paper_tabu[op].empty());

        // Once entity entries expire, moves are admissible without aspiration.
        paper_local_search(initial,op,100,-numeric_limits<double>::infinity(),solution_score_makespan,true);
        assert(paper_candidate_found && paper_entity_tabu[op].empty());
        auto attributes=pending_move_entities;
        commit_pending_tabu_attributes(100);
        assert(paper_entity_tabu[op].size()==attributes.size());
        int expiry=paper_entity_tabu[op].begin()->second;
        assert(expiry>=104 && expiry<=107);
        for(const auto& entity:attributes) assert(paper_entity_tabu[op].at(entity)==expiry);
        for(const auto& edge:paper_tabu[op]) assert(edge.second==expiry);
        assert(pending_move_entities.empty());
        clear_paper_tabu();
        assert(paper_entity_tabu[op].empty() && paper_tabu[op].empty());
    }

    // One customer, two vehicles: after accepting a relocate, returning it to
    // the original vehicle is forbidden. Removing edge memory isolates the
    // customer rule; only aspiration or expiration can allow the return.
    fixture(1,2,0);
    auto initial=solution({{0,1,0},{0}});
    auto moved=paper_local_search(initial,0,0,-numeric_limits<double>::infinity(),solution_score_makespan,true);
    assert(paper_candidate_found && moved.truck_routes[1]==vi({0,1,0}));
    assert(paper_entity_tabu[0].empty()); // selection is not acceptance
    commit_pending_tabu_attributes(0);
    int expiry=paper_entity_tabu[0].begin()->second;
    paper_tabu[0].clear();
    paper_local_search(moved,0,1,-numeric_limits<double>::infinity(),solution_score_makespan,true);
    assert(!paper_candidate_found);
    paper_local_search(moved,0,expiry,-numeric_limits<double>::infinity(),solution_score_makespan,true);
    assert(paper_candidate_found);

    // A swap has exactly one inverse here; test block/customer memory alone.
    fixture(2,2,0);
    initial=solution({{0,1,0},{0,2,0}});
    moved=paper_local_search(initial,1,0,-numeric_limits<double>::infinity(),solution_score_makespan,true);
    assert(paper_candidate_found);
    commit_pending_tabu_attributes(0);paper_tabu[1].clear();
    paper_local_search(moved,1,1,-numeric_limits<double>::infinity(),solution_score_makespan,true);
    assert(!paper_candidate_found);
}

static void test_route_memo_eviction() {
    RouteEvaluationMemo memo;
    for(size_t i=0;i<RouteEvaluationMemo::LIMIT;++i) {
        RouteEvaluation evaluation;evaluation.time=double(i);
        memo.insert(vi{0,int(i+1),0},evaluation);
    }
    RouteEvaluation got;
    assert(memo.find(vi{0,1,0},got));expect_near(got.time,0);
    RouteEvaluation extra;extra.time=123;
    memo.insert(vi{0,int(RouteEvaluationMemo::LIMIT+1),0},extra);
    assert(memo.values.size()==RouteEvaluationMemo::LIMIT);
    assert(!memo.find(vi{0,2,0},got));
    assert(memo.find(vi{0,1,0},got));expect_near(got.time,0);
    assert(memo.find(vi{0,int(RouteEvaluationMemo::LIMIT+1),0},got));expect_near(got.time,123);
}

int main() {
    test_fifo_and_final_interval();
    test_pickup_limits_and_successive_trips();
    test_drone_energy_and_separate_distances();
    test_incumbent_precedes_tabu_and_acceptance();
    test_neighbourhoods_and_repair();
    test_customer_and_block_tabu();
    test_route_memo_eviction();
    cout << "PASS: FIFO/final interval, successive schedules, post-pickup limits, energy, normalized violations, incumbent-before-tabu, all 9 operators in both scopes, repair, customer/block tabu, aspiration and expiry.\n";
}
