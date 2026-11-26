// v4: paper revision (4); derived from frozen tuned-v3 artifacts.
#include <algorithm>
#include <array>
#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#endif
#include <iterator>
#include <stdexcept>
#include <tuple>
#include <chrono>
#include <ctime>
#include <cmath>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <list>
#include <map>
#include <numeric>
#include <optional>
#include <queue>
#include <random>
#include <sstream>
#include <string>
#include <unordered_set>
#include <unordered_map>
#include <utility>
#include <vector>

#define ll long long
#define pb push_back
#define mp make_pair
#define pii pair<int,int>
#define vi vector<int>
#define vd vector<double>
#define vvi vector<vector<int>>
#define vvd vector<vector<double>>
#define vpi vector<pair<int,int>>
#define all(v) v.begin(),v.end()
#define FOR(i,a,b) for(int i=a;i<=b;i++)
#define RFOR(i,a,b) for(int i=a-1;i>=b;i--)

using namespace std;

// Data structures and global variables
struct Point {
    double x = 0.0, y = 0.0;
    int id = -1;
    Point() = default;
    Point(double x_, double y_, int id_ = -1) : x(x_), y(y_), id(id_) {}
};

int n, h, d; //number of customers, number of trucks, number of drones
vector<Point> loc; // loc[i]: location (x, y) of customer i, if i = 0, it is depot
vd serve_truck, serve_drone; // time taken by truck and drone to serve each customer (seconds)
vi served_by_drone; //whether each customer can be served by drone or not, 1 if yes, 0 if no
vd deadline; // W_i: maximum time from pickup completion to trip return (seconds)
vd demand; // demand[i]: demand of customer i
double Dh = 70.0; // Synthetic; use --truck-capacity=40 for Hanoi.
double vmax = 15.6464; // truck base speed (m/s)
//vd time_segment = {0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12}; // time segment boundaries in hours
//vd time_segments_sigma = {1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0}; //sigma (truck velocity coefficient) for each time segments
vd time_segment = {0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12}; // time segment boundaries in hours
vd time_segments_sigma = {0.9, 0.8, 0.4, 0.6,0.9, 0.8, 0.6, 0.8, 0.8, 0.7, 0.5, 0.8}; //sigma (truck velocity coefficient) for each time segments
vvd truck_vmax_ij; // truck_vmax_ij[i][j]: edge-specific base speed vmax_ij (m/s)
vector<vvd> truck_theta_ijl; // truck_theta_ijl[l][i][j]: edge/time-specific coefficient theta_ijl
double Dd = 2.27, E = 7200000.0; //drone's weight and energy capacities (for all drones)
double v_fly_drone = 31.2928, v_take_off = 15.6464, v_landing = 7.8232; // 70, 35, and 17.5 mph
double height = 50; // height of the drone
//double height = 0; // height of the drone
//double power_beta = 0, power_gamma = 1.0; //coefficients for drone energy consumption per second
double power_beta = 24.2, power_gamma = 1329.0; //coefficients for drone energy consumption per second
vvd distance_matrix; // truck fixed-path distances (meters); Euclidean fallback

vvd drone_distance_matrix; // independent horizontal flight distances (meters)
static string CFG_TRUCK_DISTANCE_FILE, CFG_DRONE_DISTANCE_FILE;

static int TABU_TENURE_BASE = 0;
const int NUM_NEIGHBORHOODS = 9;
const int NUM_OF_INITIAL_SOLUTIONS = 1; // independent runs requested with --attempts
const int MAX_SEGMENT = 200;
const int MAX_NO_IMPROVE = 1000;
const int MAX_ITER_PER_SEGMENT = 1000;
const int MAX_ITERATIONS = MAX_SEGMENT * MAX_ITER_PER_SEGMENT;
static int H_MODE = 1;
static int H_DIV = 4;
static double gamma1 = 1.0;
static double gamma2 = 0.4;
static double gamma3 = 0.10;
static double gamma4 = 0.30;

// Runtime-configurable search knobs (initialized from compile-time defaults)
static int CFG_NUM_INITIAL = NUM_OF_INITIAL_SOLUTIONS;
static int CFG_MAX_SEGMENT = MAX_SEGMENT;
static int CFG_MAX_NO_IMPROVE = MAX_NO_IMPROVE;
static int CFG_MAX_ITER_PER_SEGMENT = MAX_ITER_PER_SEGMENT;
static int CFG_MAX_ITERATIONS = MAX_ITERATIONS;
static double CFG_TIME_LIMIT_SEC = 0.0; // 0 = unlimited
static double CFG_PROCESS_TIME_LIMIT_SEC = 0.0; // includes input and construction
static string CFG_RESULT_JSON;
static const double CFG_CLUSTER_ETA = 1.0; // Fixed by Appendix B.
static double CFG_RHO = 0.1, WORKLOAD_TAU = 0.0, CFG_C_SEG = 2.0;
static std::mt19937_64 search_rng(42);
static size_t uniform_index(size_t count) {
    if (!count) throw std::runtime_error("Cannot sample an empty set");
    return std::uniform_int_distribution<size_t>(0,count-1)(search_rng);
}
static double uniform_probability() { return std::generate_canonical<double,53>(search_rng); }
static chrono::steady_clock::time_point process_started = chrono::steady_clock::now();
static optional<chrono::steady_clock::time_point> process_deadline;
struct ProcessTimeLimit {};
struct RunTelemetry {
    array<long long, 9> operator_calls{};
    long long completed_iterations = 0, accepted_moves = 0, sa_trials = 0;
    int workload_segments = 0, makespan_segments = 0, destroy_repair_calls = 0, penalty_updates = 0;
    long long route_cache_hits = 0, route_cache_misses = 0;
};
static RunTelemetry run_telemetry;
static string run_stop_reason = "iteration_budget";
#ifdef V4_TABLE8_EXPERIMENT
static optional<double> table8_reporting_tau;
static double table8_reference_workload = numeric_limits<double>::infinity();
static array<long long, 2> table8_iterations_by_mode{};

static double table8_cpu_seconds() {
#ifdef _WIN32
    FILETIME created, exited, kernel, user;
    if (GetProcessTimes(GetCurrentProcess(), &created, &exited, &kernel, &user)) {
        auto ticks = [](FILETIME time) {
            return (static_cast<uint64_t>(time.dwHighDateTime) << 32) | time.dwLowDateTime;
        };
        return (ticks(kernel) + ticks(user)) / 1e7;
    }
    return -1;
#else
    const auto ticks = std::clock();
    return ticks == std::clock_t(-1) ? -1 : double(ticks) / CLOCKS_PER_SEC;
#endif
}
#endif
static void check_process_time_limit() {
    if (process_deadline && chrono::steady_clock::now() >= *process_deadline)
        throw ProcessTimeLimit{};
}
static string CFG_TRUCK_VMAX_FILE; // optional file: i j vmax_ij
static string CFG_TRUCK_THETA_FILE; // optional file: i j l theta_ijl, l is 0-based segment index
static double CFG_SEGMENT_LENGTH_SEC = 3600.0; // time-dependent segment length in seconds
static vd CFG_TIME_BOUNDARIES_SEC; // optional interval STARTS, beginning at 0
static bool CFG_SIGMA_EXPLICIT = false;
static int CFG_OVERRIDE_TRUCKS = -1;
static int CFG_OVERRIDE_DRONES = -1;
static int CFG_RANDOM_SEED = 42;
static double CFG_C_TABU = 2.0;
static int CFG_TABU_BASE_OVERRIDE = -1;

// Adaptive penalty coefficients for constraint violations
static double PENALTY_LAMBDA_CAPACITY = 1.0;      // λ for capacity violations
static double PENALTY_LAMBDA_ENERGY = 1.0;        // λ for energy violations  
static double PENALTY_LAMBDA_DEADLINE = 1.0;      // λ for deadline violations

static double PENALTY_TARGET_VIOLATION = 0.05; // tau_v
static double PENALTY_ADAPTATION_RATE = 0.5;   // kappa

static double T0 = 0.005; // Table 5 configuration; overridable from CLI
double alpha = 0.02; // Terminal-to-initial ratio, not per-step cooling.
static double sa_temperature(int iteration,int budget) {
    return T0*std::pow(alpha,double(std::max(0,iteration))/std::max(1,budget));
}

// Destroy and repair helper

static double DESTROY_RATE = 0.2; // fraction of customers to remove during destroy phase

struct Solution {
    vvi truck_routes; //truck_routes[i]: sequence of customers served by truck i
    vvi drone_routes; //drone_routes[i]: sequence of customers served by drone i
    vd truck_route_times; //truck_route_times[i]: total time of truck i
    vd drone_route_times; //drone_route_times[i]: total time of drone i
    double total_makespan = 0.0; //total makespan of the solution
    double capacity_violation = 0.0;    // sum of excess capacity / total capacity
    double energy_violation = 0.0;      // sum of excess energy / total battery
    double deadline_violation = 0.0;    // mean relative post-pickup excess over n customers
};




// Helper to parse key=value flags from argv
static bool parse_kv_flag(const std::string& s, const std::string& key, std::string& out) {
    if (s.rfind(key + "=", 0) == 0) { out = s.substr(key.size() + 1); return true; }
    return false;
}

// Build k-nearest neighbor lists based on Euclidean distance_matrix.
// Excludes depot (0) and self; sizes to n+1. Also builds adjacency for O(1) membership checks.
void input(string filepath){
        // Open the file
        ifstream fin(filepath);
        if (!fin) {
            cerr << "Error: Cannot open " << filepath << endl;
            exit(1);
        }
        string line;
        n = h = d = -1;
        // Read trucks_count, drones_count, customers
        while (getline(fin, line)) {
        check_process_time_limit();
            if (line.empty() || line[0] == '#') continue;
            stringstream ss(line);
            string key;
            ss >> key;
            if (key == "trucks_count") ss >> h;
            else if (key == "drones_count") ss >> d;
            else if (key == "customers") ss >> n;
            else if (key == "depot") break;
        }
        // Read depot location
        double depot_x = 0, depot_y = 0;
        stringstream ss_depot(line);
        string depot_key;
        ss_depot >> depot_key >> depot_x >> depot_y;
    if (n <= 0 || h < 0 || d < 0 || depot_key != "depot" || !ss_depot ||
        !isfinite(depot_x) || !isfinite(depot_y))
        throw runtime_error("Invalid instance header: " + filepath);
    // Prepare storage (use assign to ensure inner dimensions reset, avoiding stale sizes across batch runs)
    served_by_drone.assign(n+1, 0);
    serve_truck.assign(n+1, 0.0);
    serve_drone.assign(n+1, 0.0);
    deadline.assign(n+1, 0.0);
    demand.assign(n+1, 0.0);
    loc.assign(n+1, Point());
    distance_matrix.assign(n+1, vd(n+1, 0.0));
    loc[0] = {depot_x, depot_y, 0};
    // Skip headers until data lines
    int header_skips = 0;
    while (header_skips < 2 && getline(fin, line)) {
        if (!line.empty() && line[0] != '#') ++header_skips;
    }
    // Read customer data
    int cust = 1;
    while (cust <= n && getline(fin, line)) {
        if (line.empty() || line[0] == '#') continue;
        stringstream ss(line);
        double x, y, dronable, demand_val, drone_service, truck_service, deadline_val;
        if (!(ss >> x >> y >> dronable >> demand_val >> drone_service >> truck_service >> deadline_val) ||
            !isfinite(x) || !isfinite(y) || (dronable != 0 && dronable != 1) ||
            !isfinite(demand_val) || demand_val <= 0 || !isfinite(deadline_val) || deadline_val <= 0 ||
            !isfinite(drone_service) || drone_service < 0 || !isfinite(truck_service) || truck_service < 0)
            throw runtime_error("Invalid customer record " + to_string(cust));
        loc[cust] = {x, y, cust};
        served_by_drone[cust] = (int)dronable;
        demand[cust] = demand_val;
        serve_drone[cust] = drone_service;
        serve_truck[cust] = truck_service;
        deadline[cust] = deadline_val;
        ++cust;
    }
    if (cust != n+1) throw runtime_error("Instance contains fewer customers than declared");
}

void update_tabu_tenures() {
    TABU_TENURE_BASE = CFG_TABU_BASE_OVERRIDE > 0 ? CFG_TABU_BASE_OVERRIDE
        : max(1, (int)ceil(CFG_C_TABU*sqrt((double)n)));
}

// Initialize Euclidean distances; truck/flight overrides are applied separately.
void compute_distance_matrices(const vector<Point>& loc) {
    int n = loc.size() - 1; // assuming loc[0] is depot
    for (int i = 0; i <= n; ++i) {
        for (int j = 0; j <= n; ++j) {
            distance_matrix[i][j] = sqrt((loc[i].x - loc[j].x) * (loc[i].x - loc[j].x)
                                         + (loc[i].y - loc[j].y) * (loc[i].y - loc[j].y)); // Euclidean
        }
    }
}

static int truck_time_segment_count() {
    return max(1, (int)time_segment.size() - 1);
}

static void configure_time_segment_boundaries() {
    if (!CFG_TIME_BOUNDARIES_SEC.empty()) {
        int count = (int)CFG_TIME_BOUNDARIES_SEC.size();
        if (CFG_TIME_BOUNDARIES_SEC.front() != 0.0)
            throw runtime_error("Time interval starts must begin at 0 seconds");
        for (int i=1; i<count; ++i)
            if (CFG_TIME_BOUNDARIES_SEC[i] <= CFG_TIME_BOUNDARIES_SEC[i-1])
                throw runtime_error("Time interval starts must be strictly increasing");
        if (CFG_SIGMA_EXPLICIT && (int)time_segments_sigma.size() != count)
            throw runtime_error("Supply one truck sigma per interval start");
        if (!CFG_SIGMA_EXPLICIT) time_segments_sigma.assign(count, 1.0);
        time_segment.resize(count+1);
        for (int i=0; i<count; ++i) time_segment[i] = CFG_TIME_BOUNDARIES_SEC[i]/3600.0;
        time_segment[count] = numeric_limits<double>::infinity(); // storage sentinel only
        return;
    }
    int segment_count = max(1, (int)time_segments_sigma.size());
    double segment_length_hr = max(1e-9, CFG_SEGMENT_LENGTH_SEC / 3600.0);
    time_segment.assign(segment_count + 1, 0.0);
    for (int i = 0; i <= segment_count; ++i) {
        time_segment[i] = i * segment_length_hr;
    }
}

static vd parse_csv_numbers(string value) {
    replace(value.begin(), value.end(), ',', ' ');
    stringstream stream(value);
    vd result;
    double number;
    while (stream >> number) {
        if (!isfinite(number) || number < 0.0) throw runtime_error("Expected finite nonnegative numbers");
        result.push_back(number);
    }
    if (result.empty() || !stream.eof()) throw runtime_error("Invalid numeric list: " + value);
    return result;
}

static bool parse_numeric_record(string line, vector<double>& values) {
    values.clear();
    size_t comment_pos = line.find('#');
    if (comment_pos != string::npos) line = line.substr(0, comment_pos);
    replace(line.begin(), line.end(), ',', ' ');
    stringstream ss(line);
    double value;
    while (ss >> value) values.push_back(value);
    return !values.empty();
}

// Optional sparse directed distance overrides: i j distance_m. Unspecified
// edges retain their Euclidean default; depot is node 0. Never symmetrize roads.
static void load_distance_file(const string& path, vvd& matrix) {
    if (path.empty()) return;
    ifstream file(path);
    if (!file) throw runtime_error("Cannot open distance file: " + path);
    string line;
    vector<double> values;
    while (getline(file, line)) {
        check_process_time_limit();
        if (!parse_numeric_record(line, values)) continue;
        if (values.size() != 3) throw runtime_error("Expected i j distance_m in " + path);
        int i = (int)values[0], j = (int)values[1];
        if (i < 0 || i > n || j < 0 || j > n || values[0] != i || values[1] != j ||
            !isfinite(values[2]) || values[2] < 0.0 || (i == j && values[2] != 0.0))
            throw runtime_error("Invalid distance record in " + path);
        matrix[i][j] = values[2];
    }
}

static void initialize_time_dependent_truck_speed_model() {
    int segment_count = truck_time_segment_count();
    truck_vmax_ij.assign(n + 1, vd(n + 1, vmax));
    truck_theta_ijl.assign(segment_count, vvd(n + 1, vd(n + 1, 1.0)));

    for (int l = 0; l < segment_count; ++l) {
        double default_theta = (l < (int)time_segments_sigma.size()) ? time_segments_sigma[l] : 1.0;
        for (int i = 0; i <= n; ++i) {
            for (int j = 0; j <= n; ++j) {
                truck_theta_ijl[l][i][j] = default_theta;
            }
        }
    }
}

static void load_truck_vmax_ij_file(const string& filepath) {
    if (filepath.empty()) return;
    ifstream fin(filepath);
    if (!fin) {
        cerr << "Error: Cannot open truck vmax file " << filepath << endl;
        exit(1);
    }

    string line;
    vector<double> values;
    while (getline(fin, line)) {
        check_process_time_limit();
        if (!parse_numeric_record(line, values)) continue;
        if (values.size() < 3) continue;
        int i = (int)values[0];
        int j = (int)values[1];
        double edge_vmax = values[2];
        if (i < 0 || i > n || j < 0 || j > n || !isfinite(edge_vmax) || edge_vmax <= 0.0)
            throw runtime_error("Invalid truck nominal speed in " + filepath);
        truck_vmax_ij[i][j] = edge_vmax;
    }
}

static void load_truck_theta_ijl_file(const string& filepath) {
    if (filepath.empty()) return;
    ifstream fin(filepath);
    if (!fin) {
        cerr << "Error: Cannot open truck theta file " << filepath << endl;
        exit(1);
    }

    int segment_count = truck_time_segment_count();
    string line;
    vector<double> values;
    while (getline(fin, line)) {
        check_process_time_limit();
        if (!parse_numeric_record(line, values)) continue;
        if (values.size() < 4) continue;
        int i = (int)values[0];
        int j = (int)values[1];
        int l = (int)values[2];
        double theta = values[3];
        if (i < 0 || i > n || j < 0 || j > n || l < 0 || l >= segment_count || !isfinite(theta) || theta <= 0.0)
            throw runtime_error("Invalid truck congestion factor in " + filepath);
        truck_theta_ijl[l][i][j] = theta;
    }
}

static void configure_time_dependent_truck_speed_model() {
    initialize_time_dependent_truck_speed_model();
    load_truck_vmax_ij_file(CFG_TRUCK_VMAX_FILE);
    load_truck_theta_ijl_file(CFG_TRUCK_THETA_FILE);
}

static double get_truck_edge_speed(int from, int to, int segment_idx) {
    if (from < 0 || from > n || to < 0 || to > n) return vmax;
    double edge_vmax = vmax;
    if ((int)truck_vmax_ij.size() == n + 1 && (int)truck_vmax_ij[from].size() == n + 1) {
        edge_vmax = truck_vmax_ij[from][to];
    }

    double theta = 1.0;
    if (segment_idx >= 0 && segment_idx < (int)truck_theta_ijl.size() &&
        (int)truck_theta_ijl[segment_idx].size() == n + 1 &&
        (int)truck_theta_ijl[segment_idx][from].size() == n + 1) {
        theta = truck_theta_ijl[segment_idx][from][to];
    } else if (segment_idx >= 0 && segment_idx < (int)time_segments_sigma.size()) {
        theta = time_segments_sigma[segment_idx];
    }

    double speed = theta * edge_vmax;
    if (!isfinite(speed) || speed <= 0.0) throw runtime_error("Truck speed must be finite and positive");
    return speed;
}

// Section 3.1: finite boundaries; the final speed is maintained forever.
// Boundaries are stored in hours for compatibility with v2 profile files.
int get_time_segment(double t) {
    auto it = upper_bound(time_segment.begin(), time_segment.end(), t);
    return max(0, min(truck_time_segment_count()-1, int(it-time_segment.begin())-1));
}

static double truck_leg_time(int from, int to, double departure) {
    double remaining = distance_matrix[from][to];
    double time = departure;
    while (remaining > 0.0) {
        // Compare using the same seconds representation as boundary assignment,
        // avoiding roundoff loops for noninteger segment lengths.
        auto it = upper_bound(time_segment.begin(), time_segment.end(), time,
                              [](double t, double boundary_hr) { return t < boundary_hr*3600.0; });
        int seg = max(0, min(truck_time_segment_count()-1, int(it-time_segment.begin())-1));
        double speed = get_truck_edge_speed(from, to, seg);
        double boundary = seg+1 < truck_time_segment_count()
                        ? time_segment[seg+1]*3600.0 : numeric_limits<double>::infinity();
        double available = speed * max(0.0, boundary-time);
        if (remaining <= available) return time-departure + remaining/speed;
        remaining -= available;
        time = boundary;
    }
    return time-departure;
}

pair<double, double> compute_truck_route_time(const vi& route, double start=0) {
    double time = start, violation = 0.0;
    vector<pair<int,double>> pickups;
    for (int k = 1; k < (int)route.size(); ++k) {
        int from = route[k-1], to = route[k];
        if (from == to) continue;
        time += truck_leg_time(from, to, time);
        if (to) {
            time += serve_truck[to];
            pickups.emplace_back(to, time); // completion of pickup, not arrival
        } else {
            for (auto pickup : pickups) {
                double elapsed = time-pickup.second;
                double limit = deadline[pickup.first]; // W_i, NOT a completion deadline
                violation += max(0.0, (elapsed-limit)/limit);
            }
            pickups.clear();
        }
    }
    return {time-start, violation};
}

static double drone_leg_time(int from, int to) {
    if (from == to) return 0.0;
    return height/v_take_off + drone_distance_matrix[from][to]/v_fly_drone + height/v_landing;
}

// Section 3.2: payload grows after pickup; ground service consumes no energy.
// Return raw energy excess once per trip, rather than once per overloaded leg.
pair<double, double> compute_drone_route_energy(const vi& route) {
    double total = 0.0, trip_energy = 0.0, load = 0.0, excess = 0.0;
    bool open_trip = false;
    for (int k = 1; k < (int)route.size(); ++k) {
        int from = route[k-1], to = route[k];
        double energy = (power_beta*load + power_gamma)*drone_leg_time(from,to);
        total += energy;
        trip_energy += energy;
        if (to) { load += demand[to]; open_trip = true; }
        else {
            if (open_trip) excess += max(0.0, trip_energy-E);
            trip_energy = 0.0; load = 0.0; open_trip = false;
        }
    }
    if (open_trip) excess += max(0.0, trip_energy-E);
    return {total, excess};
}

pair<double, double> compute_drone_route_time(const vi& route) {
    double time = 0; // seconds
    double deadline_feasible = 0.0;
    // Index by customer id (0..n), not by position in route
    vector<double> visit_times(n+1, 0.0); // visit_times[id]: time when node id is last visited
    vector<int> customers_since_last_depot;
    for (int k = 1; k < (int)route.size(); ++k) {
        int from = route[k-1], to = route[k];
        if (from == to) continue;
        time += drone_leg_time(from, to);
        if (to != 0) {
            time += serve_drone[to]; // in seconds
            customers_since_last_depot.push_back(to);
        }
        visit_times[to] = time;
        // If we reach depot (except at start), check duration from leaving each customer to depot
        if (to == 0 && k != 1) {
            for (int cust : customers_since_last_depot) {
                double duration = time - visit_times[cust];
                if (duration > deadline[cust] + 1e-8) {
                    double deadline_norm = deadline[cust];
                    deadline_feasible += (duration - deadline[cust]) / deadline_norm;
                }
            }
            for (int cust : customers_since_last_depot) {
                visit_times[cust] = time;
            }
            customers_since_last_depot.clear();
        }
    }
    return {time, deadline_feasible};
}



// Eligibility stays as supplied by the instance. Feasibility is checked during
// construction and penalized during search, rather than removing drone eligibility.
void update_served_by_drone() { }

// Returns: [route_time, deadline_violation, energy_violation, capacity_violation]
vector<double> check_truck_route_feasibility(const vi& route, double start=0) {
    // Check deadlines
    auto [time, deadline_violation] = compute_truck_route_time(route, start);
    
    // Check capacity (reset at depot)
    double capacity_violation = 0.0;
    double total_demand = 0.0;
    for (int k = 1; k < (int)route.size(); ++k) {
        int customer = route[k];
        if (customer == 0) {
            capacity_violation += max(0.0, (total_demand - Dh) / Dh);
            total_demand = 0.0;
        } else {
            total_demand += demand[customer];
            if (k+1 == (int)route.size())
                capacity_violation += max(0.0, (total_demand - Dh) / Dh);
        }
    }
    
    // Trucks don't use energy (set to 0)
    double energy_violation = 0.0;
    
    return {time, deadline_violation, energy_violation, capacity_violation};
}

// Returns: [route_time, deadline_violation, energy_violation, capacity_violation]
vector<double> check_drone_route_feasibility(const vi& route) {
    // Check deadlines
    auto [time, deadline_violation] = compute_drone_route_time(route);
    
    // Check capacity (reset at depot)
    double capacity_violation = 0.0;
    double total_demand = 0.0;
    for (int k = 1; k < (int)route.size(); ++k) {
        int customer = route[k];
        if (customer == 0) {
            capacity_violation += max(0.0, (total_demand - Dd) / Dd);
            total_demand = 0.0;
        } else {
            total_demand += demand[customer];
            if (k+1 == (int)route.size())
                capacity_violation += max(0.0, (total_demand - Dd) / Dd);
        }
    }
    
    // Check energy
    auto energy_metrics = compute_drone_route_energy(route);
    double energy_violation = max(0.0, energy_metrics.second / E); // normalized excess beyond battery per sortie
    
    return {time, deadline_violation, energy_violation, capacity_violation};
}

// Unified wrapper
vector<double> check_route_feasibility(const vi& route, double start=0, bool is_truck = true) {
    if (is_truck) {
        return check_truck_route_feasibility(route, start);
    } else {
        return check_drone_route_feasibility(route);
    }
}

//score calculator
struct NormalizedViolations {
    double capacity = 0.0;
    double energy = 0.0;
    double deadline = 0.0;
};

static double route_capacity_excess_sum(const vi& route, double capacity, int& trip_count) {
    double excess = 0.0;
    double load = 0.0;
    bool has_customer = false;
    for (int i = 1; i < (int)route.size(); ++i) {
        int node = route[i];
        if (node == 0) {
            if (has_customer) {
                excess += max(0.0, (load - capacity) / capacity);
                ++trip_count;
            }
            load = 0.0;
            has_customer = false;
        } else {
            load += demand[node];
            has_customer = true;
        }
    }
    if (has_customer) {
        excess += max(0.0, (load - capacity) / capacity);
        ++trip_count;
    }
    return excess;
}

static double drone_energy_excess_sum(const vi& route, int& drone_trip_count) {
    double excess = 0.0;
    vi trip{0};
    for (int i = 1; i < (int)route.size(); ++i) {
        int node = route[i];
        if (node == 0) {
            if (trip.size() > 1) {
                trip.push_back(0);
                double trip_energy = compute_drone_route_energy(trip).first;
                excess += max(0.0, (trip_energy - E) / E);
                ++drone_trip_count;
            }
            trip = {0};
        } else {
            trip.push_back(node);
        }
    }
    if (trip.size() > 1) {
        trip.push_back(0);
        double trip_energy = compute_drone_route_energy(trip).first;
        excess += max(0.0, (trip_energy - E) / E);
        ++drone_trip_count;
    }
    return excess;
}

static NormalizedViolations compute_normalized_violations(const Solution& sol) {
    double capacity_excess = 0.0;
    double energy_excess = 0.0;
    double deadline_excess = 0.0;
    int total_trip_count = 0;
    int drone_trip_count = 0;

    for (int i = 0; i < h; ++i) {
        capacity_excess += route_capacity_excess_sum(sol.truck_routes[i], Dh, total_trip_count);
        deadline_excess += compute_truck_route_time(sol.truck_routes[i], 0.0).second;
    }
    for (int i = 0; i < d; ++i) {
        capacity_excess += route_capacity_excess_sum(sol.drone_routes[i], Dd, total_trip_count);
        energy_excess += drone_energy_excess_sum(sol.drone_routes[i], drone_trip_count);
        deadline_excess += compute_drone_route_time(sol.drone_routes[i]).second;
    }

    NormalizedViolations v;
    v.capacity = capacity_excess;
    v.energy = energy_excess;
    v.deadline = deadline_excess;
    return v;
}

static double penalty_multiplier_from_violations(const NormalizedViolations& v) {
    return 1.0
         + PENALTY_LAMBDA_CAPACITY * v.capacity
         + PENALTY_LAMBDA_ENERGY * v.energy
         + PENALTY_LAMBDA_DEADLINE * v.deadline;
}

// Stable log-sum-exp over all physical vehicles, including unused vehicles.
static double smooth_workload(const Solution& sol, double tau = WORKLOAD_TAU) {
    double maximum=0.;
    for(double t:sol.truck_route_times) maximum=max(maximum,t);
    for(double t:sol.drone_route_times) maximum=max(maximum,t);
    if(tau<=0.) return maximum;
    double sum=0.;
    for(double t:sol.truck_route_times) sum+=exp((t-maximum)/tau);
    for(double t:sol.drone_route_times) sum+=exp((t-maximum)/tau);
    return sum>0.?maximum+tau*log(sum):maximum;
}
static void freeze_workload_tau(const Solution& current) {
    WORKLOAD_TAU=(h+d>=2)?CFG_RHO*current.total_makespan/log(double(h+d)):0.;
}
double solution_score_workload(const Solution& sol) {
    return smooth_workload(sol)*penalty_multiplier_from_violations(compute_normalized_violations(sol));
}

double solution_score_l2_norm(const Solution& sol) {
    return solution_score_workload(sol);
}

double solution_score_makespan(const Solution& sol) {
    NormalizedViolations v = compute_normalized_violations(sol);
    double penalty_multiplier = penalty_multiplier_from_violations(v);
    return sol.total_makespan * penalty_multiplier;
}

double solution_score_total_time(const Solution& sol) {
    return solution_score_workload(sol);
}

double calculate_score_with_penalties(const double makespan, const double sum_sq, const double capacity_violation, const double energy_violation, const double deadline_violation) {
    (void)sum_sq;
    double penalty_multiplier = 1.0 + PENALTY_LAMBDA_CAPACITY * capacity_violation
                                + PENALTY_LAMBDA_ENERGY * energy_violation
                                + PENALTY_LAMBDA_DEADLINE * deadline_violation;
    return makespan * penalty_multiplier;
}

void reset_penalty_coefficients() {
    PENALTY_LAMBDA_CAPACITY = 1.0;
    PENALTY_LAMBDA_ENERGY = 1.0;
    PENALTY_LAMBDA_DEADLINE = 1.0;
}

void update_penalties_from_segment(double avg_capacity_violation,
                                   double avg_energy_violation,
                                   double avg_deadline_violation) {
    // The paper's multiplicative update, evaluated in log space. Numerical
    // bounds prevent overflow/underflow during severely infeasible searches.
    auto update = [](double& lambda, double average) {
        double exponent = log(lambda) + PENALTY_ADAPTATION_RATE * ((average-PENALTY_TARGET_VIOLATION)/(average+PENALTY_TARGET_VIOLATION));
        lambda = exp(max(log(1e-12), min(log(1e12), exponent)));
    };
    update(PENALTY_LAMBDA_CAPACITY, avg_capacity_violation);
    update(PENALTY_LAMBDA_ENERGY, avg_energy_violation);
    update(PENALTY_LAMBDA_DEADLINE, avg_deadline_violation);
}

vvi kmeans_clustering(int k, int max_iters=1000, uint64_t seed=UINT64_MAX) {
    if (n <= 0) return {};
    // Bound k to [1, n]
    if (k <= 0) k = 1;
    if (k > n) k = n;

    vvi clusters(k);
    vector<Point> centroids;
    centroids.reserve(k);

    // Random engine
    std::mt19937 gen(seed == UINT64_MAX ? std::random_device{}() : (uint32_t)seed);
    std::uniform_int_distribution<int> dis(1, n);

    // K-means++-like seeding: first random, next farthest from existing
    // First centroid
    centroids.push_back(loc[dis(gen)]);
    while ((int)centroids.size() < k) {
        double max_min_dist = -1.0;
        Point next_centroid = loc[1];
        for (int i = 1; i <= n; ++i) {
            const Point& p = loc[i];
            double min_dist = 1e18;
            for (const auto& c : centroids) {
                double dx = p.x - c.x;
                double dy = p.y - c.y;
                double dist = std::sqrt(dx*dx + dy*dy);
                min_dist = std::min(min_dist, dist);
            }
            if (min_dist > max_min_dist) {
                max_min_dist = min_dist;
                next_centroid = p;
            }
        }
        centroids.push_back(next_centroid);
    }

    // Iterations
    vector<int> assignment(n+1, -1); // assignment for customers 1..n
    for (int it = 0; it < max_iters; ++it) {
        check_process_time_limit();
        bool changed = false;
        for (auto& cl : clusters) cl.clear();

        // Assign step
        for (int i = 1; i <= n; ++i) {
            const Point& p = loc[i];
            double bestDist2 = 1e30;
            int bestC = 0;
            for (int c = 0; c < k; ++c) {
                double dx = p.x - centroids[c].x;
                double dy = p.y - centroids[c].y;
                double d2 = dx*dx + dy*dy;
                if (d2 < bestDist2) {
                    bestDist2 = d2;
                    bestC = c;
                }
            }
            if (assignment[i] != bestC) {
                assignment[i] = bestC;
                changed = true;
            }
            clusters[bestC].push_back(i);
        }

        // Update step
        for (int c = 0; c < k; ++c) {
            if (clusters[c].empty()) {
                // Reinitialize empty cluster to a random customer to avoid dead clusters
                int pick = dis(gen);
                centroids[c].x = loc[pick].x;
                centroids[c].y = loc[pick].y;
                continue;
            }
            double sumx = 0.0, sumy = 0.0;
            for (int idx : clusters[c]) {
                sumx += loc[idx].x;
                sumy += loc[idx].y;
            }
            centroids[c].x = sumx / clusters[c].size();
            centroids[c].y = sumy / clusters[c].size();
        }

        if (!changed) break; // converged
    }

    return clusters;
}

Solution recalculate_solution(Solution sol);
bool check_solution_integrity(const Solution& sol);
static int compute_segment_count(int total_iters, int iters_per_segment);

static bool has_invalid_drone_assignment(const Solution& sol) {
    for (const vi& route : sol.drone_routes) {
        for (int customer : route) {
            if (customer != 0 && (customer < 0 || customer > n || !served_by_drone[customer])) {
                return true;
            }
        }
    }
    return false;
}

static bool is_feasible_solution(const Solution& sol) {
    return sol.deadline_violation <= 1e-8 &&
           sol.capacity_violation <= 1e-8 &&
           sol.energy_violation <= 1e-8 &&
           !has_invalid_drone_assignment(sol);
}

static optional<Solution> paper_incumbent;
static bool incumbent_improved_in_segment = false;
static optional<chrono::steady_clock::time_point> search_deadline;
struct SearchTimeLimit {};
static void check_search_time_limit() {
    check_process_time_limit();
    if (search_deadline && chrono::steady_clock::now() >= *search_deadline) {
        run_stop_reason = "search_time_limit";
        throw SearchTimeLimit{};
    }
}

// Atomically persist every improved feasible incumbent so a process watchdog
// can recover it without parsing stdout or losing an interrupted neighbourhood.
static void write_tuning_result(const string& reason) {
    if (CFG_RESULT_JSON.empty()) return;
#ifdef V4_TABLE8_EXPERIMENT
    const double cpu_seconds = table8_cpu_seconds();
#endif
    filesystem::path destination(CFG_RESULT_JSON), temporary(CFG_RESULT_JSON + ".tmp");
    ofstream out(temporary);
    if (!out) throw runtime_error("Cannot open tuning result: " + temporary.string());
    out << setprecision(17) << "{\n\"schema_version\":1,\n\"stop_reason\":\"" << reason
        << "\",\n\"feasible\":" << (paper_incumbent ? "true" : "false")
        << ",\n\"makespan\":";
    if (paper_incumbent) out << paper_incumbent->total_makespan; else out << "null";
    out << ",\n\"elapsed_seconds\":" << chrono::duration<double>(chrono::steady_clock::now()-process_started).count()
        << ",\n\"n\":" << n << ",\n\"seed\":" << CFG_RANDOM_SEED
        << ",\n\"iteration_budget\":";
#ifdef V4_TABLE8_EXPERIMENT
    if (process_deadline && !table8_calibration_iterations) out << "null"; else out << CFG_MAX_ITERATIONS;
#else
    out << CFG_MAX_ITERATIONS;
#endif
    out
        << ",\n\"segment_iters\":" << CFG_MAX_ITER_PER_SEGMENT
        << ",\n\"process_time_limit_seconds\":" << CFG_PROCESS_TIME_LIMIT_SEC
        << ",\n\"completed_iterations\":" << run_telemetry.completed_iterations;
#ifdef V4_TABLE8_EXPERIMENT
    out << ",\n\"scenario\":\"" << table8_scenario << "\""
        << ",\n\"cpu_seconds\":";
    if (cpu_seconds >= 0.) out << cpu_seconds; else out << "null";
    out << ",\n\"makespan_iterations\":" << table8_iterations_by_mode[0]
        << ",\n\"workload_iterations\":" << table8_iterations_by_mode[1]
        << ",\n\"f_mk\":";
    if (paper_incumbent) out << paper_incumbent->total_makespan; else out << "null";
    out << ",\n\"f_wl\":";
    if (paper_incumbent && table8_reporting_tau)
        out << smooth_workload(*paper_incumbent, *table8_reporting_tau);
    else out << "null";
    out << ",\n\"reporting_tau\":";
    if (table8_reporting_tau) out << *table8_reporting_tau; else out << "null";
    auto write_fleet_neighborhoods = [&](const char* key, bool workload) {
        out << ",\n\"" << key << "\":[";
        bool first = true;
        for (int op = 0; op < NUM_NEIGHBORHOODS; ++op) {
            if (op < 7 && !table8_fleet_scope(workload)) continue;
            if (!first) out << ',';
            out << op;
            first = false;
        }
        out << ']';
    };
    write_fleet_neighborhoods("makespan_fleet_neighborhoods", false);
    if (table8_switches_mode()) write_fleet_neighborhoods("workload_fleet_neighborhoods", true);
    out << ",\n\"enabled_components\":{\"neighborhoods\":9,\"adaptive_selection\":true,"
           "\"tabu\":true,"
           "\"aspiration\":true,\"simulated_annealing\":false,\"adaptive_penalties\":true,"
           "\"mode_switching\":" << (table8_switches_mode() ? "true" : "false")
        << ",\"destroy_repair\":false,\"workload_tie_break\":"
        << (table8_tie_break() ? "true" : "false") << "},\n\"parameters\":{";
#else
    out
        << ",\n\"enabled_components\":{\"neighborhoods\":9,\"adaptive_selection\":true,\"tabu\":true,"
           "\"aspiration\":true,\"simulated_annealing\":true,\"adaptive_penalties\":true,"
           "\"mode_switching\":true,\"destroy_repair\":true},\n\"parameters\":{";
#endif
    out << "\"truck_capacity\":" << Dh << ",\"drone_capacity\":" << Dd
        << ",\"c_tabu\":" << CFG_C_TABU << ",\"h_mode\":" << H_MODE << ",\"h_div\":" << H_DIV
        << ",\"gamma1\":" << gamma1 << ",\"gamma2\":" << gamma2 << ",\"gamma3\":" << gamma3 << ",\"gamma4\":" << gamma4
        << ",\"T0\":" << T0 << ",\"alpha\":" << alpha << ",\"kappa\":" << PENALTY_ADAPTATION_RATE
        << ",\"tau_v\":" << PENALTY_TARGET_VIOLATION << ",\"r_destroy\":" << DESTROY_RATE << ",\"eta\":" << CFG_CLUSTER_ETA
        << ",\"rho\":" << CFG_RHO << ",\"cseg\":" << CFG_C_SEG
        << "},\n\"telemetry\":{\"accepted_moves\":" << run_telemetry.accepted_moves
        << ",\"sa_trials\":" << run_telemetry.sa_trials << ",\"workload_segments\":" << run_telemetry.workload_segments
        << ",\"makespan_segments\":" << run_telemetry.makespan_segments
        << ",\"destroy_repair_calls\":" << run_telemetry.destroy_repair_calls
        << ",\"penalty_updates\":" << run_telemetry.penalty_updates << ",\"operator_calls\":[";
    for (int op=0;op<9;++op) { if(op) out << ','; out << run_telemetry.operator_calls[op]; }
    out << "],\"route_cache_hits\":" << run_telemetry.route_cache_hits
        << ",\"route_cache_misses\":" << run_telemetry.route_cache_misses << "}";
    auto write_routes = [&](const char* key, const vvi& routes) {
        out << ",\n\"" << key << "\":[";
        for (int v=0;v<(int)routes.size();++v) {
            if(v) out << ',';
            out << '[';
            for (int p=0;p<(int)routes[v].size();++p) { if(p) out << ','; out << routes[v][p]; }
            out << ']';
        }
        out << ']';
    };
    if (paper_incumbent) {
        write_routes("truck_routes", paper_incumbent->truck_routes);
        write_routes("drone_routes", paper_incumbent->drone_routes);
    }
    out << "\n}\n";
    out.close();
    if (!out) throw runtime_error("Cannot flush tuning result");
#ifdef _WIN32
    for(int retry=0;;++retry) {
        if(MoveFileExW(temporary.c_str(),destination.c_str(),MOVEFILE_REPLACE_EXISTING|MOVEFILE_WRITE_THROUGH)) break;
        DWORD error=GetLastError();
        if(retry>=99 || (error!=ERROR_ACCESS_DENIED && error!=ERROR_SHARING_VIOLATION && error!=ERROR_LOCK_VIOLATION))
            throw runtime_error("Cannot atomically replace tuning result, Windows error "+to_string(error));
        Sleep(10);
    }
#else
    filesystem::rename(temporary, destination);
#endif
}
static void observe_complete_candidate(const Solution& candidate) {
#ifdef V4_TABLE8_EXPERIMENT
    if (table8_tie_break() && table8_reporting_tau && paper_incumbent &&
        fabs(candidate.total_makespan - paper_incumbent->total_makespan) <= 1e-12) {
        if (is_feasible_solution(candidate) && check_solution_integrity(candidate) &&
            smooth_workload(candidate, *table8_reporting_tau) + 1e-12 <
            smooth_workload(*paper_incumbent, *table8_reporting_tau)) {
            paper_incumbent = candidate;
            write_tuning_result("incumbent_checkpoint");
        }
        return;
    }
#endif
    if (!is_feasible_solution(candidate) || !isfinite(candidate.total_makespan) ||
        (paper_incumbent && candidate.total_makespan + 1e-12 >= paper_incumbent->total_makespan))
        return;
    // Also excludes partial solutions during destroy/repair and initialization.
    if (!check_solution_integrity(candidate)) return;
    paper_incumbent = candidate;
    incumbent_improved_in_segment = true;
    write_tuning_result("incumbent_checkpoint");
}

static double solution_score_from_cached_metrics(const Solution& sol, double (*solution_cost)(const Solution&)) {
    NormalizedViolations v{sol.capacity_violation, sol.energy_violation, sol.deadline_violation};
    double penalty_multiplier = penalty_multiplier_from_violations(v);
    if (solution_cost == solution_score_makespan) {
        return sol.total_makespan * penalty_multiplier;
    }
    if (solution_cost == solution_score_workload ||
        solution_cost == solution_score_l2_norm ||
        solution_cost == solution_score_total_time) {
        return smooth_workload(sol) * penalty_multiplier;
    }
    return solution_cost(sol);
}

static double score_recalculated_candidate(Solution& candidate, double (*solution_cost)(const Solution&)) {
    candidate = recalculate_solution(candidate);
    observe_complete_candidate(candidate);
    return solution_score_from_cached_metrics(candidate, solution_cost);
}

struct RouteViolationSummary {
    double capacity_excess = 0.0;
    int trip_count = 0;
    double energy_excess = 0.0;
    int drone_trip_count = 0;
    double deadline_excess = 0.0;
};

struct RouteEvaluation {
    double time = 0.0;
    RouteViolationSummary violation;
};

static RouteEvaluation evaluate_route_for_scoring(const vi& route, bool is_truck, double start_time = 0.0) {
    RouteEvaluation eval;
    // One forward pass instead of separate time/capacity/energy scans. Store
    // pickup completions only for the current trip, not an n+1 array per call.
    double time = start_time, load = 0.0, energy = 0.0;
    vector<pair<int,double>> pickups;
    pickups.reserve(route.size());
    for (int p=1;p<(int)route.size();++p) {
        int from=route[p-1], to=route[p];
        if (from==to) continue;
        double leg=is_truck ? truck_leg_time(from,to,time) : drone_leg_time(from,to);
        if (!is_truck) energy += (power_beta*load+power_gamma)*leg;
        time += leg;
        if (to) {
            time += is_truck ? serve_truck[to] : serve_drone[to];
            load += demand[to];
            pickups.emplace_back(to,time);
        } else {
            if (!pickups.empty()) {
                ++eval.violation.trip_count;
                double capacity=is_truck ? Dh : Dd;
                eval.violation.capacity_excess += max(0.0,(load-capacity)/capacity);
                if (!is_truck) {
                    ++eval.violation.drone_trip_count;
                    eval.violation.energy_excess += max(0.0,(energy-E)/E);
                }
                for (auto pickup:pickups)
                    eval.violation.deadline_excess += max(0.0,(time-pickup.second-deadline[pickup.first])/deadline[pickup.first]);
            }
            load=0.0;energy=0.0;pickups.clear();
        }
    }
    eval.time=time-start_time;
    return eval;
}

static RouteViolationSummary combine_route_violations(const RouteViolationSummary& a, const RouteViolationSummary& b) {
    RouteViolationSummary result;
    result.capacity_excess = a.capacity_excess + b.capacity_excess;
    result.trip_count = a.trip_count + b.trip_count;
    result.energy_excess = a.energy_excess + b.energy_excess;
    result.drone_trip_count = a.drone_trip_count + b.drone_trip_count;
    result.deadline_excess = a.deadline_excess + b.deadline_excess;
    return result;
}

struct ChangedVehicle {
    bool is_truck = true;
    int idx = -1;
    int first_changed_pos = 0;
};

struct RoutePrefixCache {
    vector<RouteEvaluation> at_position;
    vector<char> valid;
};

struct RouteVectorHash {
    size_t operator()(const vi& route) const noexcept {
        size_t value=1469598103934665603ULL;
        for(int customer:route) {value ^= size_t(customer+1);value *= 1099511628211ULL;}
        return value;
    }
};

// Bounded, exact-key LRU. Whole-vehicle routes always start at time zero, so
// a cache hit also preserves all time-dependent effects on later truck trips.
struct RouteEvaluationMemo {
    static constexpr size_t LIMIT=2048;
    using Order=list<vi>;
    struct Entry { RouteEvaluation evaluation; Order::iterator position; };
    Order order;
    unordered_map<vi,Entry,RouteVectorHash> values;
    RouteEvaluationMemo()=default;
    RouteEvaluationMemo(const RouteEvaluationMemo&)=delete;
    RouteEvaluationMemo& operator=(const RouteEvaluationMemo&)=delete;
    RouteEvaluationMemo(RouteEvaluationMemo&&)=default;
    RouteEvaluationMemo& operator=(RouteEvaluationMemo&&)=default;
    bool find(const vi& route,RouteEvaluation& evaluation) {
        auto it=values.find(route);
        if(it==values.end()) return false;
        order.splice(order.begin(),order,it->second.position);
        evaluation=it->second.evaluation;
        return true;
    }
    void insert(const vi& route,const RouteEvaluation& evaluation) {
        if(values.size()>=LIMIT) {values.erase(order.back());order.pop_back();}
        order.push_front(route);
        values.emplace(route,Entry{evaluation,order.begin()});
    }
};

struct SolutionEvaluationContext {
    vector<RouteViolationSummary> truck_violations;
    vector<RouteViolationSummary> drone_violations;
    vector<RoutePrefixCache> truck_prefix_cache;
    vector<RoutePrefixCache> drone_prefix_cache;
    vd truck_times;
    vd drone_times;
    double capacity_excess = 0.0;
    double energy_excess = 0.0;
    double deadline_excess = 0.0;
    int trip_count = 0;
    int drone_trip_count = 0;
    mutable vector<RouteEvaluationMemo> route_memo;

    explicit SolutionEvaluationContext(const Solution& sol) : route_memo(h+d) {
        truck_violations.assign(h, RouteViolationSummary{});
        drone_violations.assign(d, RouteViolationSummary{});
        truck_prefix_cache.assign(h, RoutePrefixCache{});
        drone_prefix_cache.assign(d, RoutePrefixCache{});
        truck_times.assign(h, 0.0);
        drone_times.assign(d, 0.0);

        for (int i = 0; i < h; ++i) {
            RouteEvaluation eval = evaluate_route_for_scoring(sol.truck_routes[i], true);
            truck_times[i] = eval.time;
            truck_violations[i] = eval.violation;
            truck_prefix_cache[i] = build_prefix_cache(sol.truck_routes[i], true);
            add(eval.violation);
        }
        for (int i = 0; i < d; ++i) {
            RouteEvaluation eval = evaluate_route_for_scoring(sol.drone_routes[i], false);
            drone_times[i] = eval.time;
            drone_violations[i] = eval.violation;
            drone_prefix_cache[i] = build_prefix_cache(sol.drone_routes[i], false);
            add(eval.violation);
        }
    }

    static RoutePrefixCache build_prefix_cache(const vi& route, bool is_truck) {
        RoutePrefixCache cache;
        cache.at_position.assign(route.size(), RouteEvaluation{});
        cache.valid.assign(route.size(), 0);
        RouteEvaluation accumulated;
        int previous=0;
        for (int pos = 0; pos < (int)route.size(); ++pos) {
            if (route[pos] != 0) continue;
            if(pos>previous) {
                vi trip(route.begin()+previous,route.begin()+pos+1);
                RouteEvaluation next=evaluate_route_for_scoring(trip,is_truck,accumulated.time);
                accumulated.time += next.time;
                accumulated.violation=combine_route_violations(accumulated.violation,next.violation);
            }
            previous=pos;
            cache.at_position[pos] = accumulated;
            cache.valid[pos] = 1;
        }
        return cache;
    }

    void add(const RouteViolationSummary& v) {
        capacity_excess += v.capacity_excess;
        energy_excess += v.energy_excess;
        deadline_excess += v.deadline_excess;
        trip_count += v.trip_count;
        drone_trip_count += v.drone_trip_count;
    }

    void subtract(const RouteViolationSummary& v) {
        capacity_excess -= v.capacity_excess;
        energy_excess -= v.energy_excess;
        deadline_excess -= v.deadline_excess;
        trip_count -= v.trip_count;
        drone_trip_count -= v.drone_trip_count;
    }

    static int restart_depot_position(const vi& route, int first_changed_pos) {
        if (route.empty()) return 0;
        int pos = min(max(0, first_changed_pos - 1), (int)route.size() - 1);
        while (pos > 0 && route[pos] != 0) --pos;
        return pos;
    }

    static RouteEvaluation evaluate_changed_route(const vi& route, bool is_truck, const RoutePrefixCache& cache, int first_changed_pos) {
        if (route.empty()) return RouteEvaluation{};
        int restart_pos = restart_depot_position(route, first_changed_pos);
        RouteEvaluation prefix;
        if (restart_pos < (int)cache.valid.size() && cache.valid[restart_pos]) {
            prefix = cache.at_position[restart_pos];
        } else {
            vi prefix_route(route.begin(), route.begin() + restart_pos + 1);
            prefix = evaluate_route_for_scoring(prefix_route, is_truck);
        }

        vi suffix_route(route.begin() + restart_pos, route.end());
        RouteEvaluation suffix = evaluate_route_for_scoring(suffix_route, is_truck, prefix.time);
        RouteEvaluation result;
        result.time = prefix.time + suffix.time;
        result.violation = combine_route_violations(prefix.violation, suffix.violation);
        return result;
    }

    RouteEvaluation evaluate_cached(const vi& route,bool is_truck,int index,int first_changed_pos) const {
        auto& memo=route_memo[is_truck ? index : h+index];
        RouteEvaluation result;
        if(memo.find(route,result)) {++run_telemetry.route_cache_hits;return result;}
        ++run_telemetry.route_cache_misses;
        const auto& prefix=is_truck ? truck_prefix_cache[index] : drone_prefix_cache[index];
        result=evaluate_changed_route(route,is_truck,prefix,first_changed_pos);
        memo.insert(route,result);
        return result;
    }

    double score_candidate(Solution& candidate, const vector<ChangedVehicle>& changed, double (*solution_cost)(const Solution&)) const {
        double new_capacity_excess = capacity_excess;
        double new_energy_excess = energy_excess;
        double new_deadline_excess = deadline_excess;
        int new_trip_count = trip_count;
        int new_drone_trip_count = drone_trip_count;

        candidate.truck_route_times = truck_times;
        candidate.drone_route_times = drone_times;

        vector<char> seen_truck(h, 0);
        vector<char> seen_drone(d, 0);
        for (const ChangedVehicle& change : changed) {
            if (change.is_truck) {
                if (change.idx < 0 || change.idx >= h || seen_truck[change.idx]) continue;
                seen_truck[change.idx] = 1;
                const RouteViolationSummary& old_v = truck_violations[change.idx];
                new_capacity_excess -= old_v.capacity_excess;
                new_energy_excess -= old_v.energy_excess;
                new_deadline_excess -= old_v.deadline_excess;
                new_trip_count -= old_v.trip_count;
                new_drone_trip_count -= old_v.drone_trip_count;

                RouteEvaluation eval = evaluate_cached(candidate.truck_routes[change.idx], true, change.idx, change.first_changed_pos);
                candidate.truck_route_times[change.idx] = eval.time;
                new_capacity_excess += eval.violation.capacity_excess;
                new_energy_excess += eval.violation.energy_excess;
                new_deadline_excess += eval.violation.deadline_excess;
                new_trip_count += eval.violation.trip_count;
                new_drone_trip_count += eval.violation.drone_trip_count;
            } else {
                if (change.idx < 0 || change.idx >= d || seen_drone[change.idx]) continue;
                seen_drone[change.idx] = 1;
                const RouteViolationSummary& old_v = drone_violations[change.idx];
                new_capacity_excess -= old_v.capacity_excess;
                new_energy_excess -= old_v.energy_excess;
                new_deadline_excess -= old_v.deadline_excess;
                new_trip_count -= old_v.trip_count;
                new_drone_trip_count -= old_v.drone_trip_count;

                RouteEvaluation eval = evaluate_cached(candidate.drone_routes[change.idx], false, change.idx, change.first_changed_pos);
                candidate.drone_route_times[change.idx] = eval.time;
                new_capacity_excess += eval.violation.capacity_excess;
                new_energy_excess += eval.violation.energy_excess;
                new_deadline_excess += eval.violation.deadline_excess;
                new_trip_count += eval.violation.trip_count;
                new_drone_trip_count += eval.violation.drone_trip_count;
            }
        }

        candidate.total_makespan = 0.0;
        for (double t : candidate.truck_route_times) candidate.total_makespan = max(candidate.total_makespan, t);
        for (double t : candidate.drone_route_times) candidate.total_makespan = max(candidate.total_makespan, t);
        candidate.capacity_violation = max(0.0,new_capacity_excess);
        candidate.energy_violation = max(0.0,new_energy_excess);
        candidate.deadline_violation = max(0.0,new_deadline_excess);
        observe_complete_candidate(candidate);
        return solution_score_from_cached_metrics(candidate, solution_cost);
    }
};

Solution generate_initial_solution_v3(uint64_t seed = UINT64_MAX) {
    Solution sol;
    sol.truck_routes.assign(h, vi{0, 0});
    sol.drone_routes.assign(d, vi{0, 0});
    sol.truck_route_times.assign(h, 0.0);
    sol.drone_route_times.assign(d, 0.0);
    sol.total_makespan = 0.0;
    sol.capacity_violation = 0.0;
    sol.energy_violation = 0.0;
    sol.deadline_violation = 0.0;

    auto route_violation = [](const vd& metrics) {
        if (metrics.size() < 4) return 1e18;
        return metrics[1] + metrics[2] + metrics[3];
    };

    auto normalize_init_route = [](vi route) {
        vi cleaned;
        cleaned.reserve(route.size() + 2);
        if (route.empty() || route.front() != 0) cleaned.push_back(0);
        for (int node : route) {
            if (node == 0 && !cleaned.empty() && cleaned.back() == 0) continue;
            cleaned.push_back(node);
        }
        if (cleaned.empty() || cleaned.front() != 0) cleaned.insert(cleaned.begin(), 0);
        if (cleaned.back() != 0) cleaned.push_back(0);
        return cleaned;
    };

    auto last_depot_pos = [](const vi& route) {
        for (int i = (int)route.size() - 1; i >= 0; --i) {
            if (route[i] == 0) return i;
        }
        return 0;
    };

    auto current_sortie = [&](const vi& route) {
        vi sortie{0};
        int start = last_depot_pos(route);
        for (int i = start + 1; i < (int)route.size(); ++i) {
            if (route[i] != 0) sortie.push_back(route[i]);
        }
        return sortie;
    };

    struct Builder {
        bool is_truck = true;
        int cluster = 0;
        vi route{0};
        bool active = true;
        double completion = 0.0;
        double payload = 0.0;
        double energy = 0.0;
        double wait_budget = 1e18;
    };

    struct Candidate {
        int customer = -1;
        double t_to = 0.0;
        double t_back = 0.0;
        double urgency = 0.0;
        double cap_ratio = 0.0;
        double energy_ratio = 0.0;
        double extension = 0.0;
        bool same_cluster = true;
    };

    int cluster_count = max(1, h);
    vvi clusters = kmeans_clustering(cluster_count, 1000, seed);
    vi cluster_assignment(n + 1, 0);
    for (int c = 0; c < (int)clusters.size(); ++c) {
        for (int cust : clusters[c]) cluster_assignment[cust] = c;
    }

    vector<Builder> builders;
    builders.reserve(h + (d > 0 ? cluster_count : 0));
    for (int i = 0; i < h; ++i) {
        Builder b;
        b.is_truck = true;
        b.cluster = min(i, cluster_count - 1);
        builders.push_back(b);
    }
    if (d > 0) {
        for (int i = 0; i < cluster_count; ++i) {
            Builder b;
            b.is_truck = false;
            b.cluster = i;
            builders.push_back(b);
        }
    }

    vector<char> assigned(n + 1, 0);
    int assigned_count = 0;

    auto compute_leg_time = [&](bool is_truck, int from, int to, double start_time) {
        if (from == to) return 0.0;
        if (is_truck) return compute_truck_route_time({from, to}, start_time).first;
        return compute_drone_route_time({from, to}).first;
    };

    auto route_with_candidate_return = [](vi route, int customer) {
        if (route.empty()) route.push_back(0);
        route.push_back(customer);
        route.push_back(0);
        return route;
    };

    auto drone_sortie_energy_ratio = [&](const Builder& b, int customer) {
        vi sortie = current_sortie(b.route);
        sortie.push_back(customer);
        sortie.push_back(0);
        return compute_drone_route_energy(sortie).first / E;
    };

    auto generate_candidates = [&](const Builder& b, bool restrict_to_cluster) {
        vector<Candidate> candidates;
        if (!b.active) return candidates;
        int current_node = b.route.empty() ? 0 : b.route.back();
        double direct_return = compute_leg_time(b.is_truck, current_node, 0, b.completion);

        for (int cust = 1; cust <= n; ++cust) {
            check_process_time_limit();
            if (assigned[cust]) continue;
            if (restrict_to_cluster && cluster_assignment[cust] != b.cluster) continue;
            if (!b.is_truck && !served_by_drone[cust]) continue;

            double capacity = b.is_truck ? Dh : Dd;
            double cap_ratio = (b.payload + demand[cust]) / capacity;
            if (cap_ratio > 1.0 + 1e-9) continue;

            double t_to = compute_leg_time(b.is_truck, current_node, cust, b.completion);
            double t_back = compute_leg_time(b.is_truck, cust, 0, b.completion + t_to);
            double residual_budget = min(b.wait_budget - t_to, deadline[cust]);
            if (residual_budget <= 1e-9) continue;
            double urgency = t_back / residual_budget;
            if (urgency > 1.0 + 1e-9) continue;

            vi candidate_route = route_with_candidate_return(b.route, cust);
            vd metrics = check_route_feasibility(candidate_route, 0.0, b.is_truck);
            if (route_violation(metrics) > 1e-9) continue;

            Candidate cand;
            cand.customer = cust;
            cand.t_to = t_to;
            cand.t_back = t_back;
            cand.urgency = urgency;
            cand.cap_ratio = cap_ratio;
            cand.energy_ratio = b.is_truck ? 0.0 : drone_sortie_energy_ratio(b, cust);
            cand.extension = t_to + t_back - direct_return;
            cand.same_cluster = (cluster_assignment[cust] == b.cluster);
            candidates.push_back(cand);
        }
        return candidates;
    };

    auto mad_value = [](const vector<double>& vals) {
        if (vals.empty()) return 0.0;
        double mean = accumulate(vals.begin(), vals.end(), 0.0) / vals.size();
        double mad = 0.0;
        for (double v : vals) mad += fabs(v - mean);
        mad /= vals.size();
        return mad;
    };

    auto select_best_candidate = [&](const vector<Candidate>& candidates, bool is_truck) {
        vector<double> urgencies, capacities, energies, extensions;
        urgencies.reserve(candidates.size());
        capacities.reserve(candidates.size());
        energies.reserve(candidates.size());
        extensions.reserve(candidates.size());
        double min_ext = 1e18, max_ext = -1e18;
        for (const Candidate& cand : candidates) {
            urgencies.push_back(cand.urgency * cand.urgency);
            capacities.push_back(cand.cap_ratio * cand.cap_ratio);
            if (!is_truck) energies.push_back(cand.energy_ratio * cand.energy_ratio);
            min_ext = min(min_ext, cand.extension);
            max_ext = max(max_ext, cand.extension);
        }
        for (const Candidate& cand : candidates) {
            double norm_ext = (max_ext - min_ext <= 1e-9) ? 0.0 : (cand.extension - min_ext) / (max_ext - min_ext);
            extensions.push_back(norm_ext);
        }

        double w1 = mad_value(urgencies);
        double w2 = mad_value(capacities);
        double w3 = is_truck ? 0.0 : mad_value(energies);
        double w4 = mad_value(extensions);
        double w_sum = w1 + w2 + w3 + w4;
        if (w_sum <= 1e-12) {
            double equal_weight = is_truck ? 1.0 / 3.0 : 1.0 / 4.0;
            w1 = equal_weight;
            w2 = equal_weight;
            w3 = is_truck ? 0.0 : equal_weight;
            w4 = equal_weight;
        } else {
            w1 /= w_sum;
            w2 /= w_sum;
            w3 /= w_sum;
            w4 /= w_sum;
        }

        int best_idx = -1;
        double best_score = 1e300;
        int tie_count = 0;
        const double eta = CFG_CLUSTER_ETA;
        for (int i = 0; i < (int)candidates.size(); ++i) {
            const Candidate& cand = candidates[i];
            double norm_ext = (max_ext - min_ext <= 1e-9) ? 0.0 : (cand.extension - min_ext) / (max_ext - min_ext);
            double cluster_penalty = cand.same_cluster ? 0.0 : eta;
            double score = w1 * cand.urgency * cand.urgency
                         + w2 * cand.cap_ratio * cand.cap_ratio
                         + w3 * cand.energy_ratio * cand.energy_ratio
                         + w4 * norm_ext
                         + cluster_penalty;
            if (score + 1e-12 < best_score) {
                best_score = score;
                best_idx = i;
                tie_count = 1;
            } else if (fabs(score - best_score) <= 1e-12) {
                ++tie_count;
                if (uniform_index(size_t(tie_count)) == 0) best_idx = i;
            }
        }
        return best_idx;
    };

    auto extend_builder = [&](Builder& b, const Candidate& cand) {
        b.route.push_back(cand.customer);
        b.completion += cand.t_to;
        b.payload += demand[cand.customer];
        if (!b.is_truck) {
            vi sortie = current_sortie(b.route);
            b.energy = compute_drone_route_energy(sortie).first;
        }
        b.wait_budget = min(b.wait_budget - cand.t_to, deadline[cand.customer]);
        assigned[cand.customer] = 1;
        ++assigned_count;
    };

    auto close_trip = [&](Builder& b) {
        int current_node = b.route.empty() ? 0 : b.route.back();
        if (current_node != 0) {
            b.completion += compute_leg_time(b.is_truck, current_node, 0, b.completion);
            b.route.push_back(0);
        }
        b.payload = 0.0;
        b.energy = 0.0;
        b.wait_budget = 1e18;
    };

    auto seed_builder = [&](Builder& b) {
        vector<Candidate> candidates = generate_candidates(b, true);
        if (candidates.empty()) return false;
        int best = uniform_index(candidates.size());
        if (best < 0) return false;
        extend_builder(b, candidates[best]);
        return true;
    };

    for (Builder& b : builders) {
        seed_builder(b);
    }

    int stall_count = 0;
    int max_iters = max(10000, 20 * max(1, n + (int)builders.size()));
    while (assigned_count < n && max_iters-- > 0) {
        check_process_time_limit();
        int best_builder = -1;
        double best_completion = 1e300;
        for (int i = 0; i < (int)builders.size(); ++i) {
            if (!builders[i].active) continue;
            if (builders[i].completion + 1e-9 < best_completion) {
                best_completion = builders[i].completion;
                best_builder = i;
            }
        }
        if (best_builder < 0) break;

        Builder& b = builders[best_builder];
        vector<Candidate> candidates = generate_candidates(b, false);
        if (!candidates.empty()) {
            int best = select_best_candidate(candidates, b.is_truck);
            if (best >= 0) {
                extend_builder(b, candidates[best]);
                stall_count = 0;
                continue;
            }
        }

        int current_node = b.route.empty() ? 0 : b.route.back();
        if (current_node != 0) {
            close_trip(b);
            stall_count = 0;
        } else {
            b.active = false;
            if (++stall_count > (int)builders.size() + 5) break;
        }
    }

    for (Builder& b : builders) {
        close_trip(b);
    }

    sol.truck_routes.assign(h, vi{0});
    int truck_idx = 0;
    for (const Builder& b : builders) {
        if (!b.is_truck) continue;
        if (truck_idx < h) sol.truck_routes[truck_idx++] = b.route;
    }

    struct Sortie {
        vi route;
        double duration = 0.0;
    };
    vector<Sortie> sorties;
    for (const Builder& b : builders) {
        if (b.is_truck) continue;
        vi cur{0};
        for (int i = 1; i < (int)b.route.size(); ++i) {
            int node = b.route[i];
            if (node == 0) {
                if (cur.size() > 1) {
                    cur.push_back(0);
                    sorties.push_back({cur, compute_drone_route_time(cur).first});
                }
                cur = {0};
            } else {
                cur.push_back(node);
            }
        }
        if (cur.size() > 1) {
            cur.push_back(0);
            sorties.push_back({cur, compute_drone_route_time(cur).first});
        }
    }

    sol.drone_routes.assign(d, vi{0});
    if (d > 0 && !sorties.empty()) {
        vector<int> order(sorties.size());
        iota(order.begin(), order.end(), 0);
        sort(order.begin(), order.end(), [&](int a, int b) {
            return sorties[a].duration > sorties[b].duration;
        });

        struct Load {
            double time = 0.0;
            int id = 0;
        };
        struct LoadCmp {
            bool operator()(const Load& a, const Load& b) const {
                return a.time > b.time;
            }
        };
        priority_queue<Load, vector<Load>, LoadCmp> pq;
        for (int i = 0; i < d; ++i) pq.push({0.0, i});
        vector<double> loads(d, 0.0);
        for (int sortie_id : order) {
            Load cur = pq.top();
            pq.pop();
            vi& route = sol.drone_routes[cur.id];
            if (route.empty()) route.push_back(0);
            route.insert(route.end(), sorties[sortie_id].route.begin() + 1, sorties[sortie_id].route.end());
            loads[cur.id] += sorties[sortie_id].duration;
            pq.push({loads[cur.id], cur.id});
        }
    }

    for (vi& route : sol.truck_routes) route = normalize_init_route(route);
    for (vi& route : sol.drone_routes) route = normalize_init_route(route);

    struct InsertionMove {
        bool found = false;
        bool is_truck = true;
        int vehicle_idx = -1;
        int pos = -1;
        vi route;
        vd metrics;
        double score = 1e300;
        double violation = 1e300;
        double new_makespan = 1e300;
        int ties = 0;
    };

    auto better_insertion = [](double, double, double score, InsertionMove& best) {
        if(!best.found || score+1e-9<best.score) {best.ties=1;return true;}
        if(fabs(score-best.score)>1e-9) return false;
        return uniform_index(size_t(++best.ties))==0;
    };
    auto fallback_insertion_score = [&](bool is_truck, int vehicle_idx, const vi& candidate_route) {
        Solution candidate = sol;
        if (is_truck) candidate.truck_routes[vehicle_idx] = candidate_route;
        else candidate.drone_routes[vehicle_idx] = candidate_route;
        candidate = recalculate_solution(candidate);
        observe_complete_candidate(candidate);
        double score = solution_score_makespan(candidate);
        double violation = PENALTY_LAMBDA_DEADLINE * candidate.deadline_violation
                         + PENALTY_LAMBDA_ENERGY * candidate.energy_violation
                         + PENALTY_LAMBDA_CAPACITY * candidate.capacity_violation;
        return tuple<double, double, double>{score, violation, candidate.total_makespan};
    };

    auto consider_route = [&](InsertionMove& best, int cust, bool is_truck, int vehicle_idx, const vi& base_route) {
        if (is_truck || served_by_drone[cust]) {
            vi route = base_route.empty() ? vi{0} : base_route;
            for (int p = 0; p < (int)route.size(); ++p) if (route[p] == 0) {
                vi candidate_route = route;
                candidate_route.insert(candidate_route.begin()+p+1, {cust, 0});
                auto [score, violation, new_makespan] = fallback_insertion_score(is_truck, vehicle_idx, candidate_route);
                if (better_insertion(violation, new_makespan, score, best)) {
                    best.found = true; best.is_truck = is_truck; best.vehicle_idx = vehicle_idx;
                    best.pos = p+1; best.route = candidate_route;
                    best.metrics = check_route_feasibility(candidate_route, 0.0, is_truck);
                    best.score = score; best.violation = violation; best.new_makespan = new_makespan;
                }
            }
        }
        if (!is_truck && !served_by_drone[cust]) return;
        if (!is_truck && d <= 0) return;
        if (!is_truck) {
            vi normalized_route = base_route;
            if (normalized_route.empty()) normalized_route = {0, 0};
            if (normalized_route.front() != 0) normalized_route.insert(normalized_route.begin(), 0);
            if (normalized_route.back() != 0) normalized_route.push_back(0);

            auto evaluate_drone_route = [&](vi candidate_route, int pos) {
                vd metrics = check_route_feasibility(candidate_route, 0.0, false);
                auto [score, violation, new_makespan] = fallback_insertion_score(false, vehicle_idx, candidate_route);

                if (better_insertion(violation, new_makespan, score, best)) {
                    best.found = true;
                    best.is_truck = false;
                    best.vehicle_idx = vehicle_idx;
                    best.pos = pos;
                    best.route = std::move(candidate_route);
                    best.metrics = std::move(metrics);
                    best.score = score;
                    best.violation = violation;
                    best.new_makespan = new_makespan;
                }
            };

            // Try inserting inside every existing drone trip. A route can contain
            // multiple trips separated by depot nodes, e.g. 0 a b 0 c 0.
            for (int pos = 1; pos < (int)normalized_route.size(); ++pos) {
                vi candidate_route = normalized_route;
                candidate_route.insert(candidate_route.begin() + pos, cust);
                evaluate_drone_route(std::move(candidate_route), pos);
            }

            // Also try opening a new trip after the last depot.
            vi new_sortie_route = normalized_route;
            if (new_sortie_route.size() == 2 && new_sortie_route[0] == 0 && new_sortie_route[1] == 0) {
                new_sortie_route = {0, cust, 0};
            } else {
                new_sortie_route.push_back(cust);
                new_sortie_route.push_back(0);
            }
            evaluate_drone_route(std::move(new_sortie_route), (int)normalized_route.size());
            return;
        }
        int first_insert_pos = 1;
        int last_insert_pos = max(1, (int)base_route.size() - 1);
        for (int pos = first_insert_pos; pos <= last_insert_pos; ++pos) {
            vi candidate_route = base_route;
            if (candidate_route.empty()) candidate_route = {0, 0};
            if (candidate_route.front() != 0) candidate_route.insert(candidate_route.begin(), 0);
            if (candidate_route.back() != 0) candidate_route.push_back(0);
            candidate_route.insert(candidate_route.begin() + pos, cust);

            vd metrics = check_route_feasibility(candidate_route, 0.0, is_truck);
            auto [score, violation, new_makespan] = fallback_insertion_score(is_truck, vehicle_idx, candidate_route);

            if (better_insertion(violation, new_makespan, score, best)) {
                best.found = true;
                best.is_truck = is_truck;
                best.vehicle_idx = vehicle_idx;
                best.pos = pos;
                best.route = std::move(candidate_route);
                best.metrics = std::move(metrics);
                best.score = score;
                best.violation = violation;
                best.new_makespan = new_makespan;
            }
        }
    };

    sol = recalculate_solution(sol);

    for (int cust = 1; cust <= n; ++cust) {
        if (assigned[cust]) continue;
        InsertionMove best;
        for (int i = 0; i < h; ++i) {
            consider_route(best, cust, true, i, sol.truck_routes[i]);
        }
        for (int i = 0; i < d; ++i) {
            consider_route(best, cust, false, i, sol.drone_routes[i]);
        }

        if (!best.found) {
            cerr << "Warning: no insertion move found for customer " << cust << "; appending to truck 0.\n";
            if (h > 0) {
                vi route = sol.truck_routes[0];
                if (route.empty()) route = {0, 0};
                if (route.back() != 0) route.push_back(0);
                route.insert(route.end() - 1, cust);
                sol.truck_routes[0] = route;
                sol.truck_route_times[0] = check_route_feasibility(route, 0.0, true)[0];
            }
            continue;
        }

        if (best.is_truck) {
            sol.truck_routes[best.vehicle_idx] = best.route;
            sol.truck_route_times[best.vehicle_idx] = best.metrics[0];
        } else {
            sol.drone_routes[best.vehicle_idx] = best.route;
            sol.drone_route_times[best.vehicle_idx] = best.metrics[0];
        }
        sol = recalculate_solution(sol);
    }

    for (vi& route : sol.truck_routes) route = normalize_init_route(route);
    for (vi& route : sol.drone_routes) route = normalize_init_route(route);
    sol = recalculate_solution(sol);
    if (!check_solution_integrity(sol)) {
        cerr << "Warning: v3 initial solution failed integrity check.\n";
    }
    return sol;
}

void print_solution(const Solution& sol) {
    cout << "Truck Routes:\n";
    for (int i = 0; i < h; ++i) {
        cout << "Truck " << i+1 << ": ";
        for (int node : sol.truck_routes[i]) {
            cout << node << " ";
        }
        cout << "\n";
    }
    cout << "Drone Routes:\n";
    for (int i = 0; i < d; ++i) {
        cout << "Drone " << i+1 << ": ";
        for (int node : sol.drone_routes[i]) {
            cout << node << " ";
        }
        cout << "\n";
    }
}

// Stream-based printer to avoid duplicating formatting on stdout/file
static void print_solution_stream(const Solution& sol, std::ostream& os) {
    os << "Truck Routes:\n";
    for (int i = 0; i < h; ++i) {
        os << "Truck " << i+1 << ": ";
        for (int node : sol.truck_routes[i]) {
            os << node << " ";
        }
        vd truck_metric = check_route_feasibility(sol.truck_routes[i], 0.0, true);
        os << "|Truck Time: " << sol.truck_route_times[i] << "|" << truck_metric[0] << "," << truck_metric[1] << "," << truck_metric[2] << "," << truck_metric[3];
        os << "\n";
    }
    os << "Drone Routes:\n";
    for (int i = 0; i < d; ++i) {
        os << "Drone " << i+1 << ": ";
        for (int node : sol.drone_routes[i]) {
            os << node << " ";
        }
        vd drone_metric = check_route_feasibility(sol.drone_routes[i], 0.0, false);
        os << "|Drone Time: " << sol.drone_route_times[i] << "|" << drone_metric[0] << "," << drone_metric[1] << "," << drone_metric[2] << "," << drone_metric[3];
        os << "\n";
    }
    os << "Total validation:" 
       << " Makespan=" << sol.total_makespan
       << ", Deadline violation=" << sol.deadline_violation
       << ", Energy violation=" << sol.energy_violation
       << ", Capacity violation=" << sol.capacity_violation
       << "\n";
}

pair<int, bool> critical_solution_index(const Solution& sol) {
    // Identify the vehicle (truck or drone) that contributes most to the penalized objective.
    // Drone 3 is indexed as h + 2. => returns (2, false)
    double best_violation_weight = -1.0;
    double best_score = -1.0;
    bool is_truck = true;
    int best_idx = -1; // unified index: trucks [0,h), drones [h,h+d)

    auto evaluate_route = [&](const vi& route, double cached_time, bool is_truck, int unified_idx) {
        vector<double> metrics = check_route_feasibility(route, 0.0, is_truck);
        double base_time = (route.size() > 1)
            ? (cached_time > 0.0 ? cached_time : metrics[0])
            : 0.0;
        double violation_weight =
            PENALTY_LAMBDA_DEADLINE * metrics[1] +
            PENALTY_LAMBDA_ENERGY   * metrics[2] +
            PENALTY_LAMBDA_CAPACITY * metrics[3];
        double penalty_multiplier = 1.0 + violation_weight;
        double score = base_time * penalty_multiplier;

        if (violation_weight > best_violation_weight + 1e-12 ||
            (std::fabs(violation_weight - best_violation_weight) <= 1e-12 && score > best_score + 1e-9)) {
            best_violation_weight = violation_weight;
            best_score = score;
            best_idx = unified_idx;
        }
    };

    for (int i = 0; i < h; ++i) {
        double cached_time = (i < (int)sol.truck_route_times.size()) ? sol.truck_route_times[i] : 0.0;
        evaluate_route(sol.truck_routes[i], cached_time, true, i);
    }
    for (int i = 0; i < (int)sol.drone_route_times.size(); ++i) {
        double cached_time = sol.drone_route_times[i];
        evaluate_route(sol.drone_routes[i], cached_time, false, h + i);
    }

    if (best_idx == -1) {
        // Fallback: pick the route with the largest cached time to keep progress moving.
        double max_time = -1.0;
        int fallback_idx = 0;
        for (int i = 0; i < h; ++i) {
            double t = (i < (int)sol.truck_route_times.size()) ? sol.truck_route_times[i] : 0.0;
            if (t > max_time) { max_time = t; fallback_idx = i; }
        }
        for (int i = 0; i < (int)sol.drone_route_times.size(); ++i) {
            double t = sol.drone_route_times[i];
            if (t > max_time) { max_time = t; fallback_idx = h + i; }
        }
        best_idx = fallback_idx;
    }
    if (best_idx < h) {
        is_truck = true;
    } else {
        is_truck = false;
        best_idx -= h;
    }
    return {best_idx, is_truck};
}

vector<int> critical_vehicle_indices_by_makespan(const Solution& sol) {
    vector<int> critical;
    double cmax = 0.0;
    for (double t : sol.truck_route_times) cmax = max(cmax, t);
    for (double t : sol.drone_route_times) cmax = max(cmax, t);

    const double eps = 1e-8;
    for (int i = 0; i < h && i < (int)sol.truck_route_times.size(); ++i) {
        if (fabs(sol.truck_route_times[i] - cmax) <= eps) critical.push_back(i);
    }
    for (int i = 0; i < d && i < (int)sol.drone_route_times.size(); ++i) {
        if (fabs(sol.drone_route_times[i] - cmax) <= eps) critical.push_back(h + i);
    }

    if (critical.empty()) {
        auto [idx, is_truck] = critical_solution_index(sol);
        critical.push_back(is_truck ? idx : h + idx);
    }
    return critical;
}

int tabu_expiration_iteration(int current_iter, int base_tenure) {
    if (base_tenure <= 0) return current_iter;
    int tenure = base_tenure + int(uniform_index(size_t(base_tenure)+1));
    return current_iter + tenure + 1; // forbid the next tenure iterations
}

bool admissible_by_tabu_or_aspiration(bool is_tabu, double candidate_score, double segment_reference_score) {
    return !is_tabu || candidate_score + 1e-8 < segment_reference_score;
}

bool better_score_with_random_tie(double candidate_score, double incumbent_score, int& tie_count) {
    if (candidate_score + 1e-8 < incumbent_score) {
        tie_count = 1;
        return true;
    }
    if (fabs(candidate_score - incumbent_score) <= 1e-8) {
        ++tie_count;
        return uniform_index(size_t(tie_count)) == 0;
    }
    return false;
}

#include "paper_neighborhoods.hpp"

Solution recalculate_solution(Solution sol) {
    if ((int)sol.truck_routes.size() != h || (int)sol.drone_routes.size() != d)
        throw runtime_error("Solution route count does not match fleet");
    sol.truck_route_times.assign(h, 0.0);
    sol.drone_route_times.assign(d, 0.0);
    for (int i = 0; i < h; ++i) {
        vd metrics = check_route_feasibility(sol.truck_routes[i], 0.0, true);
        sol.truck_route_times[i] = metrics[0];
    }
    for (int i = 0; i < d; ++i) {
        vd metrics = check_route_feasibility(sol.drone_routes[i], 0.0, false);
        sol.drone_route_times[i] = metrics[0];
    }

    sol.total_makespan = 0.0;
    for (int t = 0; t < h; ++t) sol.total_makespan = max(sol.total_makespan, sol.truck_route_times[t]);
    for (int t = 0; t < d; ++t) sol.total_makespan = max(sol.total_makespan, sol.drone_route_times[t]);

    NormalizedViolations violations = compute_normalized_violations(sol);
    sol.capacity_violation = violations.capacity;
    sol.energy_violation = violations.energy;
    sol.deadline_violation = violations.deadline;
    return sol;
}

bool check_solution_integrity(const Solution& sol) {
    if ((int)sol.truck_routes.size() != h || (int)sol.drone_routes.size() != d) return false;
    vector<char> seen(n+1, 0);
    int count = 0;
    for (int v = 0; v < h+d; ++v) {
        const vi& route = v < h ? sol.truck_routes[v] : sol.drone_routes[v-h];
        if (route.empty() || route.front() != 0 || route.back() != 0) return false;
        for (int c : route) {
            if (c < 0 || c > n) return false;
            if (!c) continue;
            if (seen[c] || (v >= h && !served_by_drone[c])) return false;
            seen[c] = 1; ++count;
        }
    }
    return count == n;
}

static vi normalized_route_copy(vi route) {
    if (route.empty()) route.push_back(0);
    if (route.front() != 0) route.insert(route.begin(), 0);
    if (route.back() != 0) route.push_back(0);
    vi cleaned;
    cleaned.reserve(route.size());
    for (int node : route) {
        if (!cleaned.empty() && cleaned.back() == 0 && node == 0) continue;
        cleaned.push_back(node);
    }
    return cleaned;
}

static vi route_with_removed_customer(const vi& route, int cust) {
    vi result = route;
    result.erase(remove(result.begin(), result.end(), cust), result.end());
    return normalized_route_copy(result);
}

Solution repair_solution_common(Solution sol, const unordered_set<int>& to_destroy) {
    std::mt19937 rng(static_cast<unsigned>(search_rng()));
    for (vi& route : sol.truck_routes) {
        for (int c : to_destroy) route.erase(remove(route.begin(),route.end(),c),route.end());
        route = join_trips(split_trips(route));
    }
    for (vi& route : sol.drone_routes) {
        for (int c : to_destroy) route.erase(remove(route.begin(),route.end(),c),route.end());
        route = join_trips(split_trips(route));
    }
    sol = recalculate_solution(sol);
    vi order(to_destroy.begin(), to_destroy.end());
    sort(order.begin(), order.end()); // stable input before seeded shuffling
    shuffle(order.begin(), order.end(), rng);
    for (int customer : order) {
        Solution best = sol;
        double best_score = numeric_limits<double>::infinity();
        int ties = 0;
        SolutionEvaluationContext context(sol);
        for (int v = 0; v < h+d; ++v) {
            if (v >= h && !served_by_drone[customer]) continue;
            const vi& route = v < h ? sol.truck_routes[v] : sol.drone_routes[v-h];
            visit_insertions(split_trips(route), vi{customer}, [&](const Trips& trips) {
                check_search_time_limit();
                Solution candidate = sol;
                if (v < h) candidate.truck_routes[v] = join_trips(trips);
                else candidate.drone_routes[v-h] = join_trips(trips);
                double score = context.score_candidate(candidate, {{v<h, v<h ? v : v-h, 0}}, solution_score_makespan);
                if (better_score_with_random_tie(score, best_score, ties)) {
                    best_score = score; best = std::move(candidate);
                }
            });
        }
        if (!isfinite(best_score)) throw runtime_error("No eligible insertion for customer " + to_string(customer));
        sol = std::move(best);
    }
    return recalculate_solution(sol);
}

Solution destroy_worst_repair_random(Solution sol) {
    unordered_set<int> to_destroy;
    int destroy_count = max(1, (int)floor(DESTROY_RATE * n));
    Solution current_sol = recalculate_solution(sol);
    
    for (int removed = 0; removed < destroy_count; ++removed) {
        check_search_time_limit();
        vector<int> critical_vehicles = critical_vehicle_indices_by_makespan(current_sol);
        double current_score = solution_score_makespan(current_sol);
        Solution best_candidate = current_sol;
        int best_cust = -1;
        double best_reduction = -numeric_limits<double>::infinity();
        int tie_count = 0;

        for (int veh : critical_vehicles) {
            bool is_truck = veh < h;
            int veh_idx = is_truck ? veh : veh - h;
            const vi& route = is_truck ? current_sol.truck_routes[veh_idx]
                                       : current_sol.drone_routes[veh_idx];
            for (int pos = 0; pos < (int)route.size(); ++pos) {
                int cust = route[pos];
                if (cust == 0 || to_destroy.count(cust)) continue;

                Solution candidate = current_sol;
                if (is_truck) {
                    candidate.truck_routes[veh_idx] = route_with_removed_customer(route, cust);
                } else {
                    candidate.drone_routes[veh_idx] = route_with_removed_customer(route, cust);
                }
                double candidate_score = score_recalculated_candidate(candidate, solution_score_makespan);
                double reduction = current_score - candidate_score;
                if (reduction > best_reduction + 1e-8 ||
                    (fabs(reduction - best_reduction) <= 1e-8 && (++tie_count, uniform_index(size_t(tie_count)) == 0))) {
                    if (reduction > best_reduction + 1e-8) tie_count = 1;
                    best_reduction = reduction;
                    best_candidate = candidate;
                    best_cust = cust;
                }
            }
        }

        if (best_cust == -1) break;
        to_destroy.insert(best_cust);
        current_sol = best_candidate;
    }

    return repair_solution_common(current_sol, to_destroy);
}

Solution tabu_search(const Solution& initial_solution, int num_initial_sol, vector<double>& iter_current,
                     vector<double>& iter_best, vector<bool>& iter_feasible,
                     const optional<Solution>& construction_best = nullopt) {
    (void)num_initial_sol;
    auto ts_start = std::chrono::high_resolution_clock::now();
    auto is_feasible = [](const Solution& sol) {
        return is_feasible_solution(sol);
    };
    reset_penalty_coefficients();
    clear_paper_tabu();
    paper_incumbent.reset();
    if (construction_best) observe_complete_candidate(*construction_best);
    observe_complete_candidate(initial_solution);
    incumbent_improved_in_segment = false;
    search_deadline.reset();
    if (CFG_TIME_LIMIT_SEC > 0.0)
        search_deadline = chrono::steady_clock::now() + chrono::duration_cast<chrono::steady_clock::duration>(chrono::duration<double>(CFG_TIME_LIMIT_SEC));
    // Initialize edge records


    Solution best_penalized_fallback_solution = initial_solution;
    Solution best_feasible_solution = initial_solution;
    bool initial_feasible = is_feasible(initial_solution);
    double best_feasible_makespan = initial_feasible
        ? initial_solution.total_makespan
        : std::numeric_limits<double>::infinity();
    double score[NUM_NEIGHBORHOODS] = {0.0};
    double weight[NUM_NEIGHBORHOODS];
    for (int i = 0; i < NUM_NEIGHBORHOODS; ++i) weight[i] = 1.0;
    int count[NUM_NEIGHBORHOODS] = {0};

    iter_current.clear();
    iter_best.clear();
    iter_feasible.clear();

    int no_improve_segments = 0;

    Solution current_sol = initial_solution;
#ifdef V4_TABLE8_EXPERIMENT
    freeze_workload_tau(current_sol);
    table8_reporting_tau = WORKLOAD_TAU;
#endif

    int iter = 0;
    int total_iters = max(1, CFG_MAX_ITERATIONS);
#ifdef V4_TABLE8_EXPERIMENT
    if (process_deadline && !table8_calibration_iterations) total_iters = numeric_limits<int>::max();
#endif
    CFG_MAX_SEGMENT = compute_segment_count(total_iters, CFG_MAX_ITER_PER_SEGMENT);
    int scoring_mode_iter = 0; // 0: makespan-oriented, 1: workload-oriented
    auto active_solution_score = [&](const Solution& sol) {
        return scoring_mode_iter == 0 ? solution_score_makespan(sol) : solution_score_workload(sol);
    };
    Solution best_segment_sol = current_sol;
    double best_segment_score = active_solution_score(current_sol);
    double best_penalized_fallback_score = active_solution_score(current_sol);
    cout << "=== Starting Unified Tabu Search (Minimizing Active Penalized Score) ===\n";
    cout << "Initial active score: " << best_penalized_fallback_score
         << ", max_iters=" << total_iters
         << ", segment_length=" << CFG_MAX_ITER_PER_SEGMENT
         << ", segments=" << CFG_MAX_SEGMENT << "\n";

    double current_score = best_penalized_fallback_score;
    int segments_per_mode[2] = {0, 0};
    bool feasible_incumbent_improved_this_segment = false;
    double segment_capacity_violation_sum = 0.0;
    double segment_energy_violation_sum = 0.0;
    double segment_deadline_violation_sum = 0.0;
    int segment_violation_observations = 0;
    while (iter < total_iters) {
#ifdef V4_TABLE8_EXPERIMENT
        check_process_time_limit();
#endif
        if (CFG_TIME_LIMIT_SEC > 0.0) {
            double elapsed = std::chrono::duration<double>(std::chrono::high_resolution_clock::now() - ts_start).count();
            if (elapsed >= CFG_TIME_LIMIT_SEC) break;
        }
        
        current_score = active_solution_score(current_sol);
        double current_pure_cost = current_sol.total_makespan;
        iter_current.push_back(current_pure_cost);;
        iter_best.push_back(paper_incumbent ? paper_incumbent->total_makespan : numeric_limits<double>::infinity());
        iter_feasible.push_back(is_feasible(current_sol));


        // Roulette Wheel Selection
        double total_weight = 0.0;
        for (int i = 0; i < NUM_NEIGHBORHOODS; ++i) {
            total_weight += weight[i];
        }
        double r = uniform_probability();
        int selected_neighbor = NUM_NEIGHBORHOODS - 1; // fallback: last bucket absorbs rounding
        double cumulative = 0.0;
        for (int i = 0; i < NUM_NEIGHBORHOODS; ++i) {
            cumulative += weight[i] / total_weight;
            if (r < cumulative) {
                selected_neighbor = i;
                break;
            }
        }

        // Change it to random selection for testing
        //selected_neighbor = rand() % NUM_NEIGHBORHOODS;

        //Change it to round-robin/cyclic for testing
        //selected_neighbor = iter % NUM_NEIGHBORHOODS;
        count[selected_neighbor]++;
        ++run_telemetry.operator_calls[selected_neighbor];

        
        // Local Search
        Solution init_neighbor;
        Solution neighbor;
        double segment_reference_score = best_segment_score;
#ifdef V4_TABLE8_EXPERIMENT
        const bool workload = scoring_mode_iter == 1;
        auto cost = workload ? solution_score_workload : solution_score_makespan;
        if (table8_tie_break())
            table8_reference_workload = solution_score_from_cached_metrics(best_segment_sol, solution_score_workload);
#endif
        try {
#ifdef V4_TABLE8_EXPERIMENT
            init_neighbor = paper_local_search(current_sol, selected_neighbor, iter, segment_reference_score,
                                               cost, table8_fleet_scope(workload));
#else
            if (scoring_mode_iter == 0) {
                init_neighbor = local_search(current_sol, selected_neighbor, iter, segment_reference_score, solution_score_makespan);
            }
            else if (scoring_mode_iter == 1){
                init_neighbor = local_search_all_vehicle(current_sol, selected_neighbor, iter, segment_reference_score, solution_score_workload);
            }
#endif
            neighbor = recalculate_solution(init_neighbor);
            if (std::abs(neighbor.deadline_violation - init_neighbor.deadline_violation) > 1e-8 ||
                std::abs(neighbor.capacity_violation - init_neighbor.capacity_violation) > 1e-8 ||
                std::abs(neighbor.energy_violation - init_neighbor.energy_violation) > 1e-8 ||
                std::abs(neighbor.total_makespan - init_neighbor.total_makespan) > 1e-8) {
                cerr << "Warning: iter " << iter
                     << ", neighborhood " << selected_neighbor
                     << " had stale cached metrics; using recalculated neighbor.\n";
            }
             if (!check_solution_integrity(neighbor)) {
                cout << "Iter " << iter << ", Selected Neighborhood: " << selected_neighbor << "failed integrity check!\n";
                cout << "Current Solution:\n";
                print_solution_stream(current_sol, cout);
                cout << "Neighbor Solution:\n";
                print_solution_stream(neighbor, cout);
                neighbor = current_sol;
                exit(1);
            }
        } catch (const ProcessTimeLimit&) {
            clear_pending_tabu_attributes();
            throw;
        } catch (const SearchTimeLimit&) {
            clear_pending_tabu_attributes();
            break;
        } catch (const std::exception& e) {
            cerr << "\n========== EXCEPTION CAUGHT ==========\n";
            cerr << "Iter: " << iter << " | Neighbor ID: " << selected_neighbor << "\n";
            cerr << "Error: " << e.what() << "\n";
            cerr << "Solution State causing error:\n";
            print_solution_stream(current_sol, cerr);
            cerr << "======================================\n";
            throw; // Re-throw to allow program termination/analysis
        } catch (...) {
            cerr << "\n========== UNKNOWN CRASH/EXCEPTION ==========\n";
            cerr << "Iter: " << iter << " | Neighbor ID: " << selected_neighbor << "\n";
            cerr << "Solution State causing error:\n";
            print_solution_stream(current_sol, cerr);
            cerr << "=============================================\n";
            throw;
        }

        bool neighbor_feasible = is_feasible(neighbor);
        double neighbor_score = active_solution_score(neighbor);

#ifdef V4_TABLE8_EXPERIMENT
        auto improves = [&](double primary, const Solution& candidate, double reference, const Solution& incumbent) {
            if (!table8_tie_break()) return primary + 1e-12 < reference;
            return table8_improves(primary, solution_score_from_cached_metrics(candidate, solution_score_workload),
                                   reference, solution_score_from_cached_metrics(incumbent, solution_score_workload),
                                   1e-12);
        };
        bool improves_segment_reference = improves(neighbor_score, neighbor, segment_reference_score, best_segment_sol);
        bool improves_current_solution = improves(neighbor_score, neighbor, current_score, current_sol);
#else
        bool improves_segment_reference = neighbor_score + 1e-12 < segment_reference_score;
        bool improves_current_solution = neighbor_score + 1e-12 < current_score;
#endif

        // Reward is based on the evaluated candidate, independent of acceptance.
        if (paper_candidate_found && improves_segment_reference) {
            score[selected_neighbor] += gamma1;
        } else if (paper_candidate_found && improves_current_solution) {
            score[selected_neighbor] += gamma2;
        } else {
            score[selected_neighbor] += gamma3;
        }

        // Update Feasible Best
        if (neighbor_feasible) {
             double n_cost = neighbor.total_makespan;
             if (n_cost + 1e-12 < best_feasible_makespan) {
                 best_feasible_solution = neighbor;
                 best_feasible_makespan = n_cost;
                 feasible_incumbent_improved_this_segment = true;
                 cout << "Iter " << iter << " New Best Feasible Makespan: " << best_feasible_makespan << "\n";
             }
        }

        bool accept_candidate = false;
        if (improves_current_solution) {
            accept_candidate = true;
#ifdef V4_TABLE8_EXPERIMENT
        }
#else
        } else {
            ++run_telemetry.sa_trials;
            double T = sa_temperature(iter,total_iters);
            double denominator = max(fabs(current_score), 1e-12);
            double delta_rel = max(0.0, (neighbor_score - current_score) / denominator);
            double ap = (T > 0.0) ? exp(-delta_rel / T) : 0.0;
            ap = min(1.0, max(0.0, ap));
            double rand_val = uniform_probability();
            accept_candidate = (rand_val < ap);
        }
#endif

        if (paper_candidate_found && accept_candidate) {
            ++run_telemetry.accepted_moves;
            current_sol = neighbor;
            current_score = neighbor_score;
            commit_pending_tabu_attributes(iter);
#ifdef V4_TABLE8_EXPERIMENT
            if (improves(current_score, current_sol, best_segment_score, best_segment_sol)) {
#else
            if (current_score + 1e-12 < best_segment_score) {
#endif
                best_segment_sol = current_sol;
                best_segment_score = current_score;
            }
        } else {
            clear_pending_tabu_attributes();
        }
        // Section 5.3.6 observes the CURRENT state, including rejected/no-move iterations.
        segment_capacity_violation_sum += current_sol.capacity_violation;
        segment_energy_violation_sum += current_sol.energy_violation;
        segment_deadline_violation_sum += current_sol.deadline_violation;
        ++segment_violation_observations;
        if (paper_incumbent) {
            best_feasible_solution = *paper_incumbent;
            best_feasible_makespan = paper_incumbent->total_makespan;
            iter_best.back() = best_feasible_makespan;
        }
        feasible_incumbent_improved_this_segment = incumbent_improved_in_segment;
        // Periodic Weight & Segment Mode Update. Rewards/counts are collected
        // during the segment; weights are updated only after the segment ends.
        if ((iter + 1) % CFG_MAX_ITER_PER_SEGMENT == 0 || iter + 1 == total_iters) {
            segments_per_mode[scoring_mode_iter]++;
            if (scoring_mode_iter) ++run_telemetry.workload_segments;
            else ++run_telemetry.makespan_segments;
            double avg_capacity_violation = segment_violation_observations > 0
                ? segment_capacity_violation_sum / segment_violation_observations
                : 0.0;
            double avg_energy_violation = segment_violation_observations > 0
                ? segment_energy_violation_sum / segment_violation_observations
                : 0.0;
            double avg_deadline_violation = segment_violation_observations > 0
                ? segment_deadline_violation_sum / segment_violation_observations
                : 0.0;
            cout << "=== End of Segment " << (iter / CFG_MAX_ITER_PER_SEGMENT + 1) << " ===\n";
            cout << "Best Penalized Fallback Score: " << best_penalized_fallback_score
                 << " with makespan " << best_penalized_fallback_solution.total_makespan << "\n";
            cout << "Current Solution Score: " << current_score << " with makespan " << current_sol.total_makespan << "\n";
            cout << "Current mode: " << (scoring_mode_iter == 0 ? "Makespan" : "Workload") << "\n";
            cout << "Average normalized violations: cap=" << avg_capacity_violation
                 << ", energy=" << avg_energy_violation
                 << ", deadline=" << avg_deadline_violation << "\n";
            cout << "Current Weights, Rewards and Count of neighborhoods: ";
            for (int i = 0; i < NUM_NEIGHBORHOODS; ++i) {
                cout << "N" << i << ": w=" << weight[i] << ", R=" << score[i] << ", U=" << count[i] << " | ";
            }
            cout << "\n";
            if (best_segment_score + 1e-12 < best_penalized_fallback_score) {
                best_penalized_fallback_solution = best_segment_sol;
                best_penalized_fallback_score = best_segment_score;
            }
            if (feasible_incumbent_improved_this_segment) {
                no_improve_segments = 0;
            } else {
                no_improve_segments++;
            }

            int next_scoring_mode = scoring_mode_iter;
            bool diversify = false;
#ifdef V4_TABLE8_EXPERIMENT
            if (scoring_mode_iter == 1) {
                next_scoring_mode = 0;
            } else if (table8_switches_mode() && no_improve_segments == H_MODE) {
                next_scoring_mode = 1;
            }
#else
            if (no_improve_segments >= H_DIV) {
                diversify = true;
                next_scoring_mode = 0;
            } else if (scoring_mode_iter == 1) {
                next_scoring_mode = 0;
            } else if (no_improve_segments == H_MODE) {
                next_scoring_mode = 1;
            }
#endif

            if (diversify) {
                ++run_telemetry.destroy_repair_calls;
                clear_paper_tabu();
                try {
                    current_sol = destroy_worst_repair_random(current_sol);
                } catch (const SearchTimeLimit&) {
                    break; // keep the last complete current state and the global incumbent
                }
                current_sol = recalculate_solution(current_sol);
                if (is_feasible(current_sol) && current_sol.total_makespan + 1e-12 < best_feasible_makespan) {
                    best_feasible_solution = current_sol;
                    best_feasible_makespan = current_sol.total_makespan;
                    cout << "Diversification produced New Best Feasible Makespan: "
                         << best_feasible_makespan << "\n";
                }
                no_improve_segments = 0;
                cout << "No improvement in feasible makespan for " << H_DIV
                     << " segments, applying perturbation. New makespan: "
                     << current_sol.total_makespan << "\n";
                clear_pending_tabu_attributes();
            }

            if (next_scoring_mode != scoring_mode_iter) {
                scoring_mode_iter = next_scoring_mode;
                cout << "Switching scoring mode to "
                     << (scoring_mode_iter == 0 ? "Makespan" : "Workload")
                     << " for the next segment.\n";
            }

            // Update weights according to w_q <- (1-gamma4)w_q + gamma4 R_q/U_q.
            // Unused operators keep their previous weights.
            for (int i = 0; i < NUM_NEIGHBORHOODS; ++i) {
                if (count[i] != 0) {
                    weight[i] = max(1e-12, (1.0 - gamma4) * weight[i] + gamma4 * (score[i] / count[i]));
                }
            }
            for (int i = 0; i < NUM_NEIGHBORHOODS; ++i) {
                score[i] = 0.0;
                count[i] = 0;
            }
            update_penalties_from_segment(avg_capacity_violation, avg_energy_violation, avg_deadline_violation);
            ++run_telemetry.penalty_updates;
#ifdef V4_TABLE8_EXPERIMENT
            if (scoring_mode_iter == 1 || table8_tie_break()) freeze_workload_tau(current_sol);
#else
            if(scoring_mode_iter==1) freeze_workload_tau(current_sol);
#endif
            current_score = active_solution_score(current_sol);
            best_penalized_fallback_score = active_solution_score(best_penalized_fallback_solution);
            cout << "Penalty lambdas: cap=" << PENALTY_LAMBDA_CAPACITY
                 << ", energy=" << PENALTY_LAMBDA_ENERGY
                 << ", deadline=" << PENALTY_LAMBDA_DEADLINE << "\n";
            segment_capacity_violation_sum = 0.0;
            segment_energy_violation_sum = 0.0;
            segment_deadline_violation_sum = 0.0;
            segment_violation_observations = 0;
            feasible_incumbent_improved_this_segment = false;
            incumbent_improved_in_segment = false;
            best_segment_sol = current_sol;
            best_segment_score = active_solution_score(current_sol);
        }

        iter++;
        ++run_telemetry.completed_iterations;
#ifdef V4_TABLE8_EXPERIMENT
        ++table8_iterations_by_mode[workload ? 1 : 0];
#endif
    }

    cout << "Segments per mode: Makespan " << segments_per_mode[0] << ", Workload " << segments_per_mode[1] << "\n";
    search_deadline.reset();
    if (paper_incumbent) {
        if (!iter_best.empty()) iter_best.back() = paper_incumbent->total_makespan;
        return *paper_incumbent;
    }
    return best_penalized_fallback_solution;
}

static bool write_iteration_file(const std::string& out_path, const vd& iter_current, const vd& iter_best, const vector<bool>& iter_feasible) {
    std::ofstream ofs(out_path);
    if (!ofs) return false;
    ofs.setf(std::ios::fixed); ofs << setprecision(6);
    ofs << "iter,current_cost,best_cost,feasible\n";
    for (size_t i = 0; i < iter_current.size(); ++i) {
        ofs << i + 1 << "," << iter_current[i] << "," << iter_best[i] << "," << (iter_feasible[i] ? "true" : "false") << "\n";
    }
    return true;
}


// Print the (n+1)x(n+1) distance matrix (Euclidean) with depot = 0.
// Wrapped with BEGIN/END markers to allow easy parsing and optional skipping.
void print_distance_matrix(){
    cout.setf(std::ios::fixed); cout << setprecision(6);
    cout << "BEGIN_DISTANCE_MATRIX\n";
    // Header row (comma separated): idx,0,1,...,n
    cout << "idx";
    for(int j=0;j<=n;++j) cout << "," << j;
    cout << "\n";
    for(int i=0;i<=n;++i){
        cout << i;
        for(int j=0;j<=n;++j){
            cout << "," << distance_matrix[i][j];
        }
        cout << "\n";
    }
    cout << "END_DISTANCE_MATRIX\n";
}



static int compute_total_iter_budget(int customer_count, int neighborhood_count) {
    // n * K * ceil(sqrt(n)): each neighborhood gets one sqrt(n)-depth pass over all customers
    int sqrt_n = max(1, (int)ceil(sqrt((double)customer_count)));
    return max(1, customer_count * neighborhood_count * sqrt_n);
}

static int compute_iters_per_segment(int customer_count, int neighborhood_count) {
    (void)neighborhood_count;
    return max(1, int(ceil(CFG_C_SEG*customer_count))); // Paper (4) segment scaling
}

static int compute_segment_count(int total_iters, int iters_per_segment) {
    return 1 + (max(1, total_iters) - 1) / iters_per_segment;
}

static bool write_output_file(const std::string& out_path, const Solution& sol, double cost, double mean_elapsed_sec, bool final_feasibility, double worst_cost, double mean_cost) {
    std::ofstream ofs(out_path);
    if (!ofs) return false;
    ofs.setf(std::ios::fixed); ofs << setprecision(6);
    ofs << "Initial solution cost: " << cost << "\n";
    ofs << "Improved solution cost: " << sol.total_makespan << "\n";
    ofs << "Worst solution cost: " << worst_cost << "\n";
    ofs << "Mean solution cost: " << mean_cost << "\n";
    ofs << "Mean elapsed time: " << mean_elapsed_sec << " seconds\n";
    ofs << "Final solution feasibility: " << (final_feasibility ? "FEASIBLE" : "INFEASIBLE") << "\n";
    ofs << "Solution Details:\n";
    print_solution_stream(sol, ofs);
    return true;
}

static int run_cli(int argc, char* argv[]) {
        process_started = chrono::steady_clock::now();
	    if (argc < 2) {
	        cerr << "Usage: " << argv[0]
	             << " input_file [--print-distance-matrix]"
	             << " [--attempts=N] [--iters=N] [--segment-iters=N] [--segments=N] [--no-improve=N] [--time-limit=SEC] [--segment-length-sec=SEC] [--auto-tune]"
	             << " [--trucks=N] [--drones=N]"
	             << " [--truck-capacity=KG] [--drone-capacity=KG]"
	             << " [--knn-k=K] [--knn-window=W]"
	             << " [--seed=N] [--alpha=X] [--T0=X]"
	             << " [--c-tabu=X] [--tabu-base=N] [--h-mode=N] [--h-div=N] [--h-gap=N] [--psi2=X] [--psi3=X]"
	             << " [--gamma1=X] [--gamma2=X] [--gamma3=X] [--gamma4=X]"
	             << " [--kappa=X] [--tau-v=X] [--r-destroy=X]"
                 << " [--truck-vmax-file=PATH] [--truck-theta-file=PATH] [--truck-distance-file=PATH] [--drone-distance-file=PATH]"
                 << " [--time-boundaries-sec=0,T1,...] [--truck-sigma=S0,S1,...]"
                 << " [--rho=X] [--cseg=X] [--eta=1] [--process-time-limit=SEC] [--result-json=PATH]"
             << "\n";
        return 1;
    }
    string input_file = argv[1];
    optional<int> h_gap;
    optional<double> psi2,psi3;
	    bool print_dist_matrix = false;
        bool iters_explicit = false, segment_iters_explicit = false;
	    int requested_segments = -1;
	    // Parse optional flags
	    for (int ai = 2; ai < argc; ++ai) {
	        string arg = argv[ai];
	        if (arg == "--print-distance-matrix") { print_dist_matrix = true; continue; }
	        string v;
            if (parse_kv_flag(arg, "--attempts", v)) { CFG_NUM_INITIAL = max(1, stoi(v)); continue; }
            if (parse_kv_flag(arg, "--iters", v)) { CFG_MAX_ITERATIONS = max(1, stoi(v)); iters_explicit = true; continue; }
            if (parse_kv_flag(arg, "--segment-iters", v)) { CFG_MAX_ITER_PER_SEGMENT = max(1, stoi(v)); segment_iters_explicit = true; continue; }
	        if (parse_kv_flag(arg, "--segments", v)) { requested_segments = max(1, stoi(v)); continue; }
	        if (parse_kv_flag(arg, "--no-improve", v)) { CFG_MAX_NO_IMPROVE = max(1, stoi(v)); continue; }
        if (parse_kv_flag(arg, "--time-limit", v)) { CFG_TIME_LIMIT_SEC = max(0.0, stod(v)); continue; }
        if (parse_kv_flag(arg, "--process-time-limit", v)) { CFG_PROCESS_TIME_LIMIT_SEC = max(0.0, stod(v)); continue; }
        if (parse_kv_flag(arg, "--result-json", v)) { CFG_RESULT_JSON = v; continue; }
        if (parse_kv_flag(arg, "--eta", v)) {
            if(stod(v)!=1.) throw runtime_error("Paper (4) fixes eta=1; eta is not rho");
            continue;
        }
        if (parse_kv_flag(arg, "--rho", v)) { CFG_RHO=stod(v); continue; }
        if (parse_kv_flag(arg, "--cseg", v)) { CFG_C_SEG=stod(v); continue; }
        if (parse_kv_flag(arg, "--segment-length-sec", v)) { CFG_SEGMENT_LENGTH_SEC = max(1e-9, stod(v)); continue; }
        if (parse_kv_flag(arg, "--time-boundaries-sec", v)) { CFG_TIME_BOUNDARIES_SEC = parse_csv_numbers(v); continue; }
        if (parse_kv_flag(arg, "--truck-sigma", v)) {
            time_segments_sigma = parse_csv_numbers(v);
            if (any_of(time_segments_sigma.begin(), time_segments_sigma.end(), [](double x){return x<=0;}))
                throw runtime_error("Truck sigma must be strictly positive");
            CFG_SIGMA_EXPLICIT = true;
            continue;
        }
        if (parse_kv_flag(arg, "--trucks", v)) { CFG_OVERRIDE_TRUCKS = max(0, stoi(v)); continue; }
        if (parse_kv_flag(arg, "--drones", v)) { CFG_OVERRIDE_DRONES = max(0, stoi(v)); continue; }
        if (parse_kv_flag(arg, "--truck-capacity", v)) { Dh = max(1e-9, stod(v)); continue; }
        if (parse_kv_flag(arg, "--drone-capacity", v)) { Dd = max(1e-9, stod(v)); continue; }
        if (parse_kv_flag(arg, "--knn-k", v)) { (void)stoi(v); continue; }
        if (parse_kv_flag(arg, "--knn-window", v)) { (void)stoi(v); continue; }
        if (parse_kv_flag(arg, "--seed", v)) { CFG_RANDOM_SEED = stoi(v); continue; }
        if (parse_kv_flag(arg, "--alpha", v)) { alpha=stod(v); continue; }
        if (parse_kv_flag(arg, "--T0", v)) { T0 = max(0.0, stod(v)); continue; }
        if (parse_kv_flag(arg, "--c-tabu", v)) { CFG_C_TABU = max(1e-9, stod(v)); continue; }
        if (parse_kv_flag(arg, "--tabu-base", v)) { CFG_TABU_BASE_OVERRIDE = max(1, stoi(v)); continue; }
        if (parse_kv_flag(arg, "--h-gap", v)) { h_gap=stoi(v);continue; }
        if (parse_kv_flag(arg, "--psi2", v)) { psi2=stod(v);continue; }
        if (parse_kv_flag(arg, "--psi3", v)) { psi3=stod(v);continue; }
        if (parse_kv_flag(arg, "--h-mode", v)) { H_MODE = max(0, stoi(v)); continue; }
        if (parse_kv_flag(arg, "--h-div", v)) { H_DIV = max(1, stoi(v)); continue; }
        if (parse_kv_flag(arg, "--gamma1", v)) { gamma1 = max(0.0, stod(v)); continue; }
        if (parse_kv_flag(arg, "--gamma2", v)) { gamma2 = max(0.0, stod(v)); continue; }
        if (parse_kv_flag(arg, "--gamma3", v)) { gamma3 = max(0.0, stod(v)); continue; }
        if (parse_kv_flag(arg, "--gamma4", v)) { gamma4 = min(1.0, max(0.0, stod(v))); continue; }
        if (parse_kv_flag(arg, "--kappa", v)) { PENALTY_ADAPTATION_RATE = max(0.0, stod(v)); continue; }
        if (parse_kv_flag(arg, "--tau-v", v)) { PENALTY_TARGET_VIOLATION = max(0.0, stod(v)); continue; }
        if (parse_kv_flag(arg, "--r-destroy", v)) { DESTROY_RATE = min(1.0, max(0.0, stod(v))); continue; }
        if (parse_kv_flag(arg, "--truck-vmax-file", v)) { CFG_TRUCK_VMAX_FILE = v; continue; }
        if (parse_kv_flag(arg, "--truck-theta-file", v)) { CFG_TRUCK_THETA_FILE = v; continue; }
        if (parse_kv_flag(arg, "--truck-distance-file", v)) { CFG_TRUCK_DISTANCE_FILE = v; continue; }
        if (parse_kv_flag(arg, "--drone-distance-file", v)) { CFG_DRONE_DISTANCE_FILE = v; continue; }
        if (arg == "--auto-tune") continue; // v3 already uses the paper's size-scaled defaults
        throw runtime_error("Unknown option: " + arg);
    }
    if(h_gap) H_DIV=H_MODE+*h_gap;
    if(psi2 || psi3) {
        gamma2=psi2.value_or(gamma2/gamma1)*gamma1;
        gamma3=psi3.value_or(0.25)*gamma2;
    }
    if(gamma1!=1. || !(gamma1>gamma2 && gamma2>gamma3 && gamma3>=0.))
        throw runtime_error("Paper (4) requires gamma1=1 and gamma1>gamma2>gamma3>=0");
    if(!(T0>0.) || !isfinite(T0) || !(PENALTY_ADAPTATION_RATE>0.) || !isfinite(PENALTY_ADAPTATION_RATE))
        throw runtime_error("T0 and kappa must be positive");
    if(!(alpha>0. && alpha<1.) || !isfinite(alpha)) throw runtime_error("alpha must be a terminal ratio in (0,1)");
    if(!(CFG_RHO>0.) || !isfinite(CFG_RHO)) throw runtime_error("rho must be positive");
    if(!(CFG_C_SEG>0.) || !isfinite(CFG_C_SEG)) throw runtime_error("cseg must be positive");
    if(!(PENALTY_TARGET_VIOLATION>0.) || !isfinite(PENALTY_TARGET_VIOLATION)) throw runtime_error("tau_v must be positive");
    search_rng.seed(CFG_RANDOM_SEED);
    srand(CFG_RANDOM_SEED);
    if (CFG_PROCESS_TIME_LIMIT_SEC > 0.0)
        process_deadline = process_started + chrono::duration_cast<chrono::steady_clock::duration>(chrono::duration<double>(CFG_PROCESS_TIME_LIMIT_SEC));
    if (!CFG_RESULT_JSON.empty() && CFG_NUM_INITIAL != 1)
        throw runtime_error("--result-json requires --attempts=1; run independent processes for multiple seeds");

    // Read input instance
    input(input_file);
    if (CFG_OVERRIDE_TRUCKS >= 0) h = CFG_OVERRIDE_TRUCKS;
    if (CFG_OVERRIDE_DRONES >= 0) d = CFG_OVERRIDE_DRONES;
    if (h+d <= 0 || H_DIV <= H_MODE || H_MODE < 1 || alpha <= 0.0 ||
        gamma1 < gamma2 || gamma2 < gamma3) {
        cerr << "Invalid fleet or search parameters (require Hdiv > Hmode >= 1, gamma1 >= gamma2 >= gamma3, alpha > 0).\n";
        return 1;
    }
    // Recalculate tenures based on instance size
    update_tabu_tenures();
    // Build distance matrix for downstream time computations
    compute_distance_matrices(loc);
    drone_distance_matrix = distance_matrix;
    load_distance_file(CFG_TRUCK_DISTANCE_FILE, distance_matrix);
    load_distance_file(CFG_DRONE_DISTANCE_FILE, drone_distance_matrix);
    configure_time_segment_boundaries();
    // Build edge/time-dependent truck speed model: v_ijl = theta_ijl * vmax_ij.
    configure_time_dependent_truck_speed_model();
    if (print_dist_matrix) {
        print_distance_matrix();
        return 0; // only print distance matrix and exit
    }

	    if (!iters_explicit) CFG_MAX_ITERATIONS = compute_total_iter_budget(n, NUM_NEIGHBORHOODS);
    if (!segment_iters_explicit) CFG_MAX_ITER_PER_SEGMENT = compute_iters_per_segment(n, NUM_NEIGHBORHOODS);
        if (requested_segments > 0) {
	        CFG_MAX_ITERATIONS = max(1, requested_segments * CFG_MAX_ITER_PER_SEGMENT);
	    }

	CFG_MAX_SEGMENT = compute_segment_count(CFG_MAX_ITERATIONS, CFG_MAX_ITER_PER_SEGMENT);
	    cout << "Search config: total_iters=" << CFG_MAX_ITERATIONS
	         << " (segments=" << CFG_MAX_SEGMENT
	         << ", segment_iters=" << CFG_MAX_ITER_PER_SEGMENT
	         << ", no_improve=" << CFG_MAX_NO_IMPROVE
	         << ", time_limit_sec=" << CFG_TIME_LIMIT_SEC << ")\n";

    // Pre-filter dronable customers by capacity/energy
    //For another data-testing: change all deadline to a constant 3600 and all serving time to 0

    update_served_by_drone();
    if (h == 0 && find(served_by_drone.begin()+1, served_by_drone.end(), 0) != served_by_drone.end()) {
        cerr << "UNSUCCESSFUL: at least one customer needs a truck, but the truck fleet is empty.\n";
        return 2;
    }
    //print test the served by drone
    /* cout << "Customers that can be served by drone:\n";
    for (int i = 1; i <= n; ++i) {
        if (served_by_drone[i]) {
            cout << i << " "; 
        }
    }
    cout << "\n";
    exit(1); */

    // Collect all attempt results, sort, take top-K for mean/worst
    struct AttemptResult {
        Solution sol;
        double initial_cost;
        vd iter_current;
        vd iter_best;
        vector<bool> iter_feasible;
    };
    vector<AttemptResult> all_results;
    all_results.reserve(CFG_NUM_INITIAL);

    auto start_time = std::chrono::high_resolution_clock::now();
    int ablation_seed = CFG_RANDOM_SEED;
    for (int attempt = 0; attempt < CFG_NUM_INITIAL; ++attempt) {
        paper_incumbent.reset(); // no cross-attempt incumbent leakage
        reset_penalty_coefficients(); // Also before construction/fallback.
        WORKLOAD_TAU=0.;
        search_rng.seed(uint64_t(ablation_seed)+attempt);
        Solution initial_solution = generate_initial_solution_v3(ablation_seed + attempt);
        const optional<Solution> construction_best = paper_incumbent;
        vd iter_current, iter_best;
        vector<bool> current_feasibility;
        Solution improved_sol = tabu_search(initial_solution, CFG_NUM_INITIAL, iter_current, iter_best, current_feasibility, construction_best);
        cout.setf(std::ios::fixed); cout << setprecision(6);
        cout << "Attempt " << attempt + 1 << " cost: " << improved_sol.total_makespan << "\n";
        print_solution_stream(improved_sol, cout);
        all_results.push_back({improved_sol, initial_solution.total_makespan,
                                iter_current, iter_best, current_feasibility});
    }
#ifdef V4_TABLE8_EXPERIMENT
    write_tuning_result(run_stop_reason);
    return paper_incumbent ? 0 : 2;
#endif

    // Sort ascending by makespan; best solution = rank 0
    sort(all_results.begin(), all_results.end(),
         [](const AttemptResult& a, const AttemptResult& b) {
             bool af = is_feasible_solution(a.sol) && check_solution_integrity(a.sol);
             bool bf = is_feasible_solution(b.sol) && check_solution_integrity(b.sol);
             if (af != bf) return af;
             return a.sol.total_makespan < b.sol.total_makespan;
         });

    auto end_time = std::chrono::high_resolution_clock::now();
    double elapsed_seconds = std::chrono::duration<double>(end_time - start_time).count();

    // Mean and worst computed from top-10 (best runs only)
    int successful_attempts = 0;
    for (const auto& result : all_results)
        if (is_feasible_solution(result.sol) && check_solution_integrity(result.sol)) ++successful_attempts;
    const int TOP_K = min(10, successful_attempts);
    double sum_overall_cost = 0.0;
    double worst_overall_cost = TOP_K ? -1.0 : numeric_limits<double>::infinity();
    for (int i = 0; i < TOP_K; ++i) {
        double mk = all_results[i].sol.total_makespan;
        sum_overall_cost += mk;
        if (mk > worst_overall_cost) worst_overall_cost = mk;
    }
    double mean_overall_cost = TOP_K ? sum_overall_cost / TOP_K : numeric_limits<double>::infinity();
    bool have_best = !all_results.empty();

    if (have_best) {
        const auto& best = all_results[0]; // lowest makespan
        cout << "\n=== Best Across Attempts (top " << TOP_K << "/" << (int)all_results.size() << ") ===\n";
        cout << "Initial Solution Cost: " << best.initial_cost << "\n";
        cout << "Improved Solution Cost: " << best.sol.total_makespan << "\n";
        cout << "Worst Solution Cost (top-" << TOP_K << "): " << worst_overall_cost << "\n";
        cout << "Mean Solution Cost (top-" << TOP_K << "): " << mean_overall_cost << "\n";
        cout << "Mean elapsed Time: " << (elapsed_seconds / all_results.size()) << " seconds\n";
        print_solution_stream(best.sol, cout);
        // check final feasibility
        Solution final_checked_sol = recalculate_solution(best.sol);
        bool final_feas = is_feasible_solution(final_checked_sol) && check_solution_integrity(final_checked_sol);
        if (final_feas) {
            cout << "Final solution feasibility: FEASIBLE\n";
        } else {
            cout << "Final solution feasibility: INFEASIBLE\n";
        }
        string out_best = "output_solution_best.txt";
        if (write_output_file(out_best, best.sol, best.initial_cost, elapsed_seconds / all_results.size(), final_feas, worst_overall_cost, mean_overall_cost)) {
            cout << "Best solution written to " << out_best << "\n";
        } else {
            cout << "Failed to write best solution to " << out_best << "\n";
        }
        string out_iter = "output_mode_0.txt";
        if (write_iteration_file(out_iter, best.iter_current, best.iter_best, best.iter_feasible)) {
            cout << "Iteration data written to " << out_iter << "\n";
        } else {
            cout << "Failed to write iteration data to " << out_iter << "\n";
        }
    }

    if (!successful_attempts) {
        write_tuning_result(run_stop_reason);
        cerr << "UNSUCCESSFUL: no feasible solution found; output is an infeasible diagnostic fallback.\n";
        return 2;
    }
    write_tuning_result(run_stop_reason);
    return 0;
}

int main(int argc, char* argv[]) {
    try { return run_cli(argc, argv); }
    catch (const ProcessTimeLimit&) {
        write_tuning_result("process_time_limit");
        cout << "Process time limit reached; feasible incumbent " << (paper_incumbent ? "saved" : "not found") << "\n";
        return paper_incumbent ? 0 : 2;
    }
    catch (const exception& error) {
        cerr << "Error: " << error.what() << "\n";
        return 1;
    }
}

// Run with : g++ -O3 -std=c++20 tabubu.cpp -o tabubu && ./tabubu instance/50.20.4.txt
// Plot history iteration: python plot_iteration.py --input output.txt --save iterations.png
// Plot route: python3 plot_sol.py instance/50.20.4.txt output_solution_best.txt
