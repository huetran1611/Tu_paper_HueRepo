// Independent hand-calculated checks for the revised paper's equations.
static void test_revision4() {
    T0=.005;alpha=.02;
    for(int budget:{162,3600,9000,27000}) {
        expect_near(sa_temperature(0,budget),.005,1e-12);
        expect_near(sa_temperature(budget,budget),.0001,1e-12);
        expect_near(sa_temperature(budget/2,budget),.0007071067811865475,1e-12);
        assert(sa_temperature(budget/2,budget)>sa_temperature(budget,budget));
    }
    fixture(2,1,1);
    CFG_RHO=.1;
    Solution equal;equal.truck_route_times={100};equal.drone_route_times={100};equal.total_makespan=100;
    freeze_workload_tau(equal);
    expect_near(WORKLOAD_TAU,14.426950408889635,1e-12);
    expect_near(smooth_workload(equal),110.,1e-12);
    const double frozen=WORKLOAD_TAU;
    Solution other=equal;other.truck_route_times={101};other.drone_route_times={50};other.total_makespan=101;
    assert(smooth_workload(other)<smooth_workload(equal)); // Can trade makespan for balance.
    expect_near(WORKLOAD_TAU,frozen); // Candidate evaluation cannot reset tau.
    other.truck_route_times={0};other.drone_route_times={0};other.total_makespan=0;
    expect_near(smooth_workload(other),10.,1e-12); // Bound is relative to segment entry, not candidate.
    other.truck_route_times={100};other.drone_route_times={0};other.total_makespan=100;
    assert(smooth_workload(other)>100. && smooth_workload(other)<110.);
    other.truck_route_times={1e12};other.drone_route_times={1e12-1};other.total_makespan=1e12;
    assert(isfinite(smooth_workload(other))); // Stable without exp(1e12/tau).

    reset_penalty_coefficients();PENALTY_ADAPTATION_RATE=.5;PENALTY_TARGET_VIOLATION=.05;
    update_penalties_from_segment(.15,0.,.05);
    expect_near(PENALTY_LAMBDA_CAPACITY,1.2840254166877414,1e-12);
    expect_near(PENALTY_LAMBDA_ENERGY,.6065306597126334,1e-12);
    expect_near(PENALTY_LAMBDA_DEADLINE,1.,1e-12);
    reset_penalty_coefficients();update_penalties_from_segment(1e12,1e12,1e12);
    assert(PENALTY_LAMBDA_CAPACITY<=exp(.5)*(1.+1e-12));

    fixture(2,2,0);deadline[1]=deadline[2]=5;
    distance_matrix[0][2]=distance_matrix[2][0]=10;
    auto sol=solution({{0,1,0},{0,2,0}});
    expect_near(sol.deadline_violation,2.); // Two unit relative excesses, not their mean.
    fixture(1,1,1);Dd=1;demand[1]=2;E=.01;deadline[1]=.001;
    update_served_by_drone();assert(served_by_drone[1]==1);
    served_by_drone[1]=0;update_served_by_drone();assert(served_by_drone[1]==0);

    search_rng.seed(20261111);
    int first_half=0;
    for(int trial=0;trial<100;++trial) {
        int selected=0,ties=1;
        for(int candidate=1;candidate<100000;++candidate)
            if(better_score_with_random_tie(1.,1.,ties)) selected=candidate;
        if(selected<50000) ++first_half;
    }
    assert(first_half>=30 && first_half<=70); // Detects the old RAND_MAX=32767 distortion.
    reset_penalty_coefficients();WORKLOAD_TAU=0.;
    cout << "PASS: paper4 SA endpoints/budget normalization, H_tau/bound/frozen tau, bounded penalty update, summed violations, eligibility and uniform large-tie sampling.\n";
}
