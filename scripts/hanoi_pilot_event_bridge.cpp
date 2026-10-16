// Export exact event times without changing the production search or evaluator.
#define main hanoi_solver_main
#include "../src_v2/tabubu_HanoiTraffic_v2.cpp"
#undef main

int main(int argc, char** argv) {
    int status = hanoi_solver_main(argc, argv);
    if (status != 0 && status != 2) return status;
    bool replay = false;
    for (int k = 1; k < argc; ++k)
        if (std::string(argv[k]).find("--evaluate-solution=") == 0) replay = true;
    Solution sol;
    std::string error;
    if (!load_solution_routes(replay ? "output_solution_evaluated.txt" : "output_solution_best.txt", sol, error)) {
        std::cerr << error << "\n";
        return 3;
    }
    sol = recalculate_solution(sol);
    std::ofstream events("customer_events.csv"), audit("event_audit.json");
    events << std::setprecision(17);
    events << "vehicle,vehicle_id,trip,customer,pickup_complete_sec,trip_return_sec,post_pickup_sec,limit_sec,excess_sec,excess_pct,trip_load_kg\n";
    double max_truck_load = 0, max_drone_load = 0, normalized_excess = 0;
    int count = 0;
    auto emit = [&](const vi& route, bool truck, int vehicle) {
        std::vector<std::pair<int, double>> pending;
        double load = 0;
        int trip = 1;
        vi prefix{0};
        for (int k = 1; k < (int)route.size(); ++k) {
            int node = route[k];
            prefix.push_back(node);
            double elapsed = truck ? compute_truck_route_time(prefix).first : compute_drone_route_time(prefix).first;
            if (node != 0) {
                pending.push_back({node, elapsed});
                load += demand[node];
            } else if (!pending.empty()) {
                if (truck) max_truck_load = std::max(max_truck_load, load);
                else max_drone_load = std::max(max_drone_load, load);
                for (auto [customer, pickup] : pending) {
                    double duration = elapsed - pickup;
                    double excess = std::max(0.0, duration - deadline[customer]);
                    if (duration > deadline[customer] + 1e-8)
                        normalized_excess += excess / deadline[customer];
                    events << (truck ? "truck" : "drone") << ',' << vehicle << ',' << trip << ',' << customer << ','
                           << pickup << ',' << elapsed << ',' << duration << ',' << deadline[customer] << ','
                           << excess << ',' << 100 * excess / deadline[customer] << ',' << load << '\n';
                    ++count;
                }
                pending.clear();
                load = 0;
                ++trip;
            }
        }
    };
    for (int k = 0; k < (int)sol.truck_routes.size(); ++k) emit(sol.truck_routes[k], true, k + 1);
    for (int k = 0; k < (int)sol.drone_routes.size(); ++k) emit(sol.drone_routes[k], false, k + 1);
    if (count != n || std::abs(normalized_excess / n - sol.deadline_violation) > 1e-10) {
        std::cerr << "Event export disagrees with solver freshness evaluation\n";
        return 3;
    }
    audit << std::setprecision(17)
          << "{\"customers\":" << count << ",\"makespan_sec\":" << sol.total_makespan
          << ",\"deadline_violation\":" << sol.deadline_violation
          << ",\"capacity_violation\":" << sol.capacity_violation
          << ",\"energy_violation\":" << sol.energy_violation
          << ",\"max_truck_trip_load_kg\":" << max_truck_load
          << ",\"max_drone_trip_load_kg\":" << max_drone_load << "}\n";
    return status;
}
