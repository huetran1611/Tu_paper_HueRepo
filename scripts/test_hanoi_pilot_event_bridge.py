import csv
import json
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
BINARY = ROOT / "build/hanoi_p2_deadline_pilot/protocol_solver"


@unittest.skipUnless(BINARY.exists(), "Compile hanoi_pilot_event_bridge.cpp first")
class EventBridgeTests(unittest.TestCase):
    def test_hour_crossing_trip_reset_and_infeasible_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            instance = work / "instance.txt"
            instance.write_text("trucks_count 1\ndrones_count 1\ncustomers 3\ndepot 0 0\n"
                                "Coordinate X Coordinate Y Dronable Demand\n"
                                "X Y Dronable Demand Drone_service Truck_service Lw\n"
                                "1 0 1 1 60 60 3600\n2 0 1 1 60 60 1200\n3 0 1 1 60 60 100\n")
            truck = {(0, 1): 15000, (1, 0): 1000, (0, 2): 1000, (2, 0): 10000}
            drone = {(0, 3): 3130, (3, 0): 3130}
            for name, entries in (("truck.txt", truck), ("drone.txt", drone)):
                (work / name).write_text("".join(f"{i} {j} {entries.get((i, j), 0)}\n"
                                               for i in range(4) for j in range(4)))
            (work / "speed.txt").write_text("".join(f"{i} {j} {hour} {speed}\n"
                        for hour, speed in ((8, 10), (9, 20)) for i in range(4) for j in range(4)))
            (work / "routes.txt").write_text("Truck Routes:\nTruck 1: 0 1 0 2 0\nDrone Routes:\nDrone 1: 0 3 0\n")
            result = subprocess.run([str(BINARY), str(instance), "--start-hour=8",
                       "--trucks=1", "--drones=1", "--truck-capacity=40", "--drone-capacity=2.27",
                       f"--truck-distance-file={work / 'truck.txt'}",
                       f"--drone-distance-file={work / 'drone.txt'}",
                       f"--truck-vijl-file={work / 'speed.txt'}",
                       f"--evaluate-solution={work / 'routes.txt'}"], cwd=work, capture_output=True, text=True)
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            with (work / "customer_events.csv").open() as stream:
                events = {int(row["customer"]): row for row in csv.DictReader(stream)}
            self.assertEqual(set(events), {1, 2, 3})
            self.assertAlmostEqual(float(events[1]["pickup_complete_sec"]), 4560)
            self.assertAlmostEqual(float(events[1]["post_pickup_sec"]), 180)
            self.assertEqual(events[1]["trip"], "1")
            self.assertAlmostEqual(float(events[2]["post_pickup_sec"]), 1800)
            self.assertAlmostEqual(float(events[2]["excess_sec"]), 600)
            self.assertEqual(events[2]["trip"], "2")
            self.assertAlmostEqual(float(events[2]["trip_load_kg"]), 1)
            self.assertAlmostEqual(float(events[3]["post_pickup_sec"]), 100 + 50 / 15.6 + 50 / 7.8)
            audit = json.loads((work / "event_audit.json").read_text())
            self.assertAlmostEqual(audit["makespan_sec"], 6780)
            self.assertAlmostEqual(audit["max_truck_trip_load_kg"], 1)


if __name__ == "__main__":
    unittest.main()
