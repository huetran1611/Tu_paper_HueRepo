// Build: g++ -std=c++17 -O2 src_v4/v4_table8.cpp -o v4_table8
// Run a batch with scripts/run_v4_table8.py, or pass an instance and scenario here.
#include <cmath>
#include <string>

static char table8_scenario = 'A';
static bool table8_tie_break() { return table8_scenario == 'E'; }
static bool table8_switches_mode() { return table8_scenario == 'B' || table8_scenario == 'D'; }

// N7/N8 already use fleet scope inside the V4 neighbourhood implementation.
static bool table8_fleet_scope(bool workload, int op) {
    return table8_scenario == 'C' || (table8_scenario == 'D' && workload && op == 0);
}

static bool table8_improves(double primary, double secondary, double reference,
                           double reference_secondary, double tolerance) {
    return primary + tolerance < reference ||
           (table8_tie_break() && std::abs(primary - reference) <= tolerance &&
            secondary + tolerance < reference_secondary);
}

#define V4_TABLE8_EXPERIMENT
#define main v4_table8_solver_main
#include "tabubu_TimeDependent_v4.cpp"
#undef main

int main(int argc, char* argv[]) {
    try {
        if (argc < 3) {
            std::cerr << "Usage: " << argv[0]
                      << " INSTANCE --scenario=A|B|C|D|E --result-json=PATH"
                         " [--process-time-limit=SECONDS] [V4 options]\n";
            return 1;
        }
        std::vector<char*> args{argv[0], argv[1]};
        bool scenario_given = false;
        bool result_given = false;
        bool deadline_given = false;
        for (int i = 2; i < argc; ++i) {
            std::string arg = argv[i], value;
            if (parse_kv_flag(arg, "--scenario", value)) {
                if (scenario_given || value.size() != 1 || value[0] < 'A' || value[0] > 'E')
                    throw std::runtime_error("Pass one --scenario from A to E");
                scenario_given = true;
                table8_scenario = value[0];
                continue;
            }
            if (parse_kv_flag(arg, "--process-time-limit", value)) {
                size_t end = 0;
                double seconds = std::stod(value, &end);
                if (deadline_given || end != value.size() || !std::isfinite(seconds) ||
                    seconds <= 0 || seconds >= std::chrono::duration<double>(
                        std::chrono::steady_clock::duration::max() / 2).count())
                    throw std::runtime_error("Pass one positive, representable process time limit");
                deadline_given = true;
            }
            if (parse_kv_flag(arg, "--result-json", value)) result_given = !value.empty();
            if (arg == "--auto-tune" || arg == "--print-distance-matrix" ||
                parse_kv_flag(arg, "--time-limit", value))
                throw std::runtime_error("Table 8 uses a single solver run with an optional process deadline");
            args.push_back(argv[i]);
        }
        if (!scenario_given || !result_given || (!deadline_given && table8_scenario != 'A'))
            throw std::runtime_error("Required: --scenario, --result-json and a process time limit for B through E");
        const int count = static_cast<int>(args.size());
        args.push_back(nullptr);
        return v4_table8_solver_main(count, args.data());
    } catch (const std::exception& error) {
        std::cerr << "Error: " << error.what() << '\n';
        return 1;
    }
}
