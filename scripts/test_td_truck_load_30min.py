import unittest
from argparse import Namespace
import csv
from contextlib import redirect_stdout
import io
from pathlib import Path
import tempfile
from unittest.mock import patch

from td_truck_load_30min import aggregate, payloads, write_csv


class TripPayloadTests(unittest.TestCase):
    def test_depot_resets_load_and_metadata_is_not_a_customer(self):
        text = ('Truck 1: 0 1 2 0 3 0 |Truck Time: 999999|123,0,0,0\n'
                'Drone 1: 0 4 0 |Drone Time: 5|5,0,0,0\n')
        trips, best, covered, drone_max = payloads(text, [0, 2, 3, 4, 1])
        self.assertEqual([t['load_kg'] for t in trips], [5, 4])
        self.assertEqual(best['customers'], '1 2')
        self.assertTrue(covered)
        self.assertEqual(drone_max, 1)

    def test_duplicate_customer_does_not_pass_coverage(self):
        _, _, covered, _ = payloads('Truck 1: 0 1 2 1 0\n', [0, 2, 3])
        self.assertFalse(covered)

    def test_unclosed_route_is_rejected(self):
        with self.assertRaises(ValueError):
            payloads('Truck 1: 0 1 2\n', [0, 2, 3])

    def test_aggregate_does_not_accept_infeasible_record_as_maximum(self):
        names = [f'{n}.10.{i}' for n in [200, 500] for i in range(1, 17)]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for i, name in enumerate(names):
                folder = root / 'input' / name
                folder.mkdir(parents=True)
                write_csv(folder / 'result.csv', [dict(instance=name, customers=int(name.split('.')[0]),
                          process_status='completed', coverage_ok=True, payload_capacity_ok=True,
                          feasibility='INFEASIBLE' if i==0 else 'FEASIBLE',
                          max_truck_trip_load_kg=999 if i==0 else 10, trip_customers='1 2')])
            args = Namespace(root=root, input=root/'input', output=root/'output', summary_file=None)
            with patch('td_truck_load_30min.matrix', return_value=names), redirect_stdout(io.StringIO()):
                aggregate(args)
            with (args.output/'truck_load_max_by_size.csv').open() as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(rows[0]['max_truck_trip_load_kg'], '10')
            self.assertEqual(rows[0]['feasible_verified_instances'], '15')


if __name__ == '__main__':
    unittest.main()
