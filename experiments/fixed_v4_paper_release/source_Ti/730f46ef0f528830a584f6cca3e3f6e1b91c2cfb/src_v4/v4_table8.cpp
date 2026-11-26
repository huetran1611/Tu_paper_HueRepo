// Build: g++ -std=c++17 -O2 src_v4/v4_table8.cpp -o v4_table8
// Run a batch with scripts/run_v4_table8.py, or pass an instance and scenario here.
#include <cmath>
#include <string>

static std::string table8_scenario = "A";
static bool table8_calibration_iterations = false;
static bool table8_tie_break() { return table8_scenario == "E"; }
static bool table8_switches_mode() {
    return table8_scenario == "B" || table8_scenario == "D" || table8_scenario == "D_paper";
}

// N7/N8 already use fleet scope inside the V4 neighbourhood implementation.
static bool table8_fleet_scope(bool workload) {
    return table8_scenario == "C" || (table8_scenario == "D_paper" && workload);
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
                      << " INSTANCE --scenario=A|B|C|D|E|D_paper --result-json=PATH"
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
                const bool letter = value.size() == 1 && value[0] >= 'A' && value[0] <= 'E';
                if (scenario_given || (!letter && value != "D_paper"))
                    throw std::runtime_error("Pass one --scenario: A, B, C, D, E, or D_paper");
                scenario_given = true;
                table8_scenario = value;
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
            if (parse_kv_flag(arg, "--iters", value)) table8_calibration_iterations = true;
            if (arg == "--auto-tune" || arg == "--print-distance-matrix" ||
                parse_kv_flag(arg, "--time-limit", value))
                throw std::runtime_error("Table 8 uses a single solver run with an optional process deadline");
            args.push_back(argv[i]);
        }
        if (!scenario_given || !result_given || (!deadline_given && table8_scenario != "A"))
            throw std::runtime_error("Required: --scenario, --result-json and a process time limit for timed scenarios");
        table8_calibration_iterations = table8_scenario == "A" && table8_calibration_iterations;
        const int count = static_cast<int>(args.size());
        args.push_back(nullptr);
        return v4_table8_solver_main(count, args.data());
    } catch (const std::exception& error) {
        std::cerr << "Error: " << error.what() << '\n';
        return 1;
    }
}
