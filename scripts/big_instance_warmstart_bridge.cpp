// Keep the historical search and evaluators unchanged; replace initialization only.
#define main historical_big_instance_main
#include "big_multitrip_original.cpp"
#undef main
#include <stdexcept>

static bool feasible_warm(const Solution& sol) {
    return sol.deadline_violation <= 1e-8 && sol.capacity_violation <= 1e-8 &&
           sol.energy_violation <= 1e-8;
}

static void validate_warm(const Solution& sol) {
    if (!check_solution_integrity(sol)) throw runtime_error("Customer coverage failure");
    for (const auto& route : sol.drone_routes)
        for (int node : route)
            if (node && !served_by_drone[node]) throw runtime_error("Ineligible drone customer");
    if (!feasible_warm(sol)) throw runtime_error("Solution fails historical evaluator at capacity 40");
}

static Solution load_warm(const string& path) {
    ifstream file(path);
    if (!file) throw runtime_error("Cannot open warm-start solution");
    Solution sol;
    sol.truck_routes.resize(h);
    sol.drone_routes.resize(d);
    sol.truck_route_times.assign(h, 0);
    sol.drone_route_times.assign(d, 0);
    string line;
    while (getline(file, line)) {
        istringstream stream(line);
        string kind;
        int vehicle;
        char colon;
        if (!(stream >> kind >> vehicle >> colon) || colon != ':') continue;
        if (kind != "Truck" && kind != "Drone") continue;
        auto& routes = kind == "Truck" ? sol.truck_routes : sol.drone_routes;
        if (vehicle < 1 || vehicle > (int)routes.size() || !routes[vehicle-1].empty())
            throw runtime_error("Invalid or duplicate vehicle route");
        int node;
        while (stream >> node) {
            if (node < 0 || node > n) throw runtime_error("Invalid customer index");
            routes[vehicle-1].push_back(node);
        }
        const auto& route = routes[vehicle-1];
        if (route.empty() || route.front() != 0 || route.back() != 0)
            throw runtime_error("Route is not depot-to-depot");
    }
    for (const auto& route : sol.truck_routes)
        if (route.empty()) throw runtime_error("Missing truck route");
    for (const auto& route : sol.drone_routes)
        if (route.empty()) throw runtime_error("Missing drone route");
    sol = recalculate_solution(sol);
    validate_warm(sol);
    return sol;
}

static void write_warm_audit(const Solution& sol, double initial, double elapsed,
                             int iterations, bool evaluation_only, bool smoke) {
    ofstream events("customer_events.csv"), audit("audit.json");
    events << setprecision(17)
           << "vehicle,vehicle_id,trip,customer,pickup_complete_sec,trip_return_sec,post_pickup_sec,limit_sec,excess_sec,trip_load_kg\n";
    double max_truck = 0, max_drone = 0;
    int event_count = 0, failures = 0;
    auto emit = [&](const vi& route, bool truck, int vehicle) {
        vector<pair<int, double>> pending;
        vi prefix{0};
        double load = 0;
        int trip = 1;
        for (int k = 1; k < (int)route.size(); ++k) {
            int node = route[k];
            prefix.push_back(node);
            double time = truck ? compute_truck_route_time(prefix).first : compute_drone_route_time(prefix).first;
            if (node) {
                pending.push_back({node, time});
                load += demand[node];
            } else if (!pending.empty()) {
                if (truck) max_truck = max(max_truck, load);
                else max_drone = max(max_drone, load);
                for (auto [customer, pickup] : pending) {
                    double duration = time-pickup, excess = max(0.0, duration-deadline[customer]);
                    if (duration > deadline[customer]+1e-8) ++failures;
                    events << (truck ? "truck" : "drone") << ',' << vehicle << ',' << trip << ',' << customer << ','
                           << pickup << ',' << time << ',' << duration << ',' << deadline[customer] << ',' << excess << ',' << load << '\n';
                    ++event_count;
                }
                pending.clear();
                load = 0;
                ++trip;
            }
        }
    };
    for (int k=0; k<h; ++k) emit(sol.truck_routes[k], true, k+1);
    for (int k=0; k<d; ++k) emit(sol.drone_routes[k], false, k+1);
    if (event_count != n || failures || max_truck > 40+1e-8 || max_drone > Dd+1e-8)
        throw runtime_error("Independent event/load audit failed");
    audit << setprecision(17)
          << "{\"customers\":" << n << ",\"truck_capacity_kg\":" << Dh << ",\"drone_capacity_kg\":" << Dd
          << ",\"initial_makespan_s\":" << initial << ",\"makespan_s\":" << sol.total_makespan
          << ",\"solver_elapsed_s\":" << elapsed << ",\"actual_iterations\":" << iterations
          << ",\"planned_iterations\":" << CFG_MAX_SEGMENT*CFG_MAX_ITER_PER_SEGMENT
          << ",\"deadline_violation\":" << sol.deadline_violation
          << ",\"capacity_violation\":" << sol.capacity_violation << ",\"energy_violation\":" << sol.energy_violation
          << ",\"max_truck_trip_load_kg\":" << max_truck << ",\"max_drone_trip_load_kg\":" << max_drone
          << ",\"evaluation_only\":" << (evaluation_only ? "true" : "false")
          << ",\"smoke_test\":" << (smoke ? "true" : "false") << "}\n";
}

int main(int argc, char** argv) {
    try {
        if (argc < 2) throw runtime_error("Expected instance and --warmstart=PATH");
        string warm;
        bool evaluate = false;
        int smoke_iters = 0;
        Dh = 40;
        CFG_NUM_INITIAL = 1;
        for (int k=2; k<argc; ++k) {
            string arg = argv[k], value;
            if (parse_kv_flag(arg, "--warmstart", value)) warm = value;
            else if (parse_kv_flag(arg, "--drone-capacity", value)) Dd = stod(value);
            else if (parse_kv_flag(arg, "--truck-vmax-file", value)) CFG_TRUCK_VMAX_FILE = value;
            else if (parse_kv_flag(arg, "--truck-theta-file", value)) CFG_TRUCK_THETA_FILE = value;
            else if (parse_kv_flag(arg, "--segments", value)) CFG_MAX_SEGMENT = stoi(value);
            else if (parse_kv_flag(arg, "--iters", value)) CFG_MAX_ITER_PER_SEGMENT = stoi(value);
            else if (parse_kv_flag(arg, "--no-improve", value)) CFG_MAX_NO_IMPROVE = stoi(value);
            else if (parse_kv_flag(arg, "--time-limit", value)) CFG_TIME_LIMIT_SEC = stod(value);
            else if (parse_kv_flag(arg, "--smoke-iterations", value)) smoke_iters = stoi(value);
            else if (arg == "--evaluate-only") evaluate = true;
            else throw runtime_error("Unknown argument: "+arg);
        }
        if (warm.empty() || Dd <= 0 || CFG_MAX_SEGMENT <= 0 || CFG_MAX_ITER_PER_SEGMENT <= 0)
            throw runtime_error("Invalid warm-start configuration");
        input(argv[1]);
        update_tabu_tenures();
        compute_distance_matrices(loc);
        configure_time_dependent_truck_speed_model();
        CFG_MAX_ITER_PER_SEGMENT = min(CFG_MAX_ITER_PER_SEGMENT, compute_iters_per_segment(n, NUM_NEIGHBORHOODS));
        CFG_MAX_SEGMENT = min(CFG_MAX_SEGMENT, compute_segment_count(
            compute_total_iter_budget(n, NUM_NEIGHBORHOODS), compute_iters_per_segment(n, NUM_NEIGHBORHOODS)));
        CFG_MAX_NO_IMPROVE = 4*CFG_MAX_ITER_PER_SEGMENT;
        CFG_KNN_K = min(CFG_KNN_K, n <= 200 ? n : n/2);
        if (CFG_KNN_K > 0) compute_knn_lists(CFG_KNN_K);
        else { KNN_LIST.assign(n+1, {}); KNN_ADJ.assign(n+1, vector<char>(n+1, 0)); }
        update_served_by_drone();
        if (smoke_iters > 0) { CFG_MAX_SEGMENT = 1; CFG_MAX_ITER_PER_SEGMENT = smoke_iters; }
        Solution initial = load_warm(warm), best = initial;
        vd current, history;
        vector<bool> feasible;
        auto start = chrono::steady_clock::now();
        if (!evaluate) best = tabu_search(initial, 1, current, history, feasible);
        double elapsed = chrono::duration<double>(chrono::steady_clock::now()-start).count();
        best = recalculate_solution(best);
        validate_warm(best);
        if (best.total_makespan > initial.total_makespan+1e-7)
            throw runtime_error("Search lost its feasible warm-start incumbent");
        if (!write_output_file("output_solution_best.txt", best, initial.total_makespan,
                               elapsed, true, best.total_makespan, best.total_makespan) ||
            !write_iteration_file("output_mode_0.txt", current, history, feasible))
            throw runtime_error("Cannot save solver outputs");
        write_warm_audit(best, initial.total_makespan, elapsed, (int)history.size(), evaluate, smoke_iters > 0);
        return 0;
    } catch (const exception& error) {
        cerr << "Warm-start error: " << error.what() << '\n';
        return 3;
    }
}
