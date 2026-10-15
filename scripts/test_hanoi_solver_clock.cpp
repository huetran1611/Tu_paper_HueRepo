#define main hanoi_solver_main
#include "../src_v2/tabubu_HanoiTraffic_v2.cpp"
#undef main
#include <cassert>

int main() {
    n = 1;
    CFG_TRUCK_VIJL_FILE = "test-profile";
    CFG_TRAFFIC_FIRST_HOUR = 6;
    CFG_TRAFFIC_START_HOUR = 8;
    time_segments_sigma.assign(12, 1.0);
    configure_time_segment_boundaries();
    truck_vmax_ij.assign(2, vd(2, 1.0));
    truck_theta_ijl.assign(12, vvd(2, vd(2, 30.0)));
    truck_theta_ijl[2][0][1] = 10.0;
    truck_theta_ijl[3][0][1] = 20.0;
    truck_distance_matrix.assign(2, vd(2, 54000.0));
    serve_truck.assign(2, 0.0);
    deadline.assign(2, 1e9);
    assert(get_time_segment(0) == 2);
    assert(get_time_segment(1) == 3);
    assert(abs(compute_truck_route_time({0, 1}).first - 4500.0) < 1e-7);
    CFG_TRAFFIC_START_HOUR = 9;
    assert(abs(compute_truck_route_time({0, 1}).first - 2700.0) < 1e-7);
    CFG_TRAFFIC_START_HOUR = 8.5;
    assert(abs(compute_truck_route_time({0, 1}).first - 3600.0) < 1e-7);
    CFG_TRAFFIC_START_HOUR = 17;
    assert(get_time_segment(2) == 11);
    truck_distance_matrix[0][1] = 216000.0;
    assert(abs(compute_truck_route_time({0, 1}).first - 7200.0) < 1e-7);
    CFG_C_TAU = 2;
    update_tabu_tenures();
    assert(TABU_TENURE_BASE == 20);
    n = 100;
    CFG_C_TAU = 3;
    update_tabu_tenures();
    assert(TABU_TENURE_BASE == 30);
    CFG_TRUCK_VIJL_FILE.clear();
    CFG_TRAFFIC_FIRST_HOUR = 0;
    CFG_TRAFFIC_START_HOUR = -1;
    assert(get_time_segment(13.0) == 1);
    cout << "Clock boundary, departure offset, last-hour hold, and tenure tests passed.\n";
}
