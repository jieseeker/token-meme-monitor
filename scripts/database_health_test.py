import importlib.util
import tempfile
import unittest
from pathlib import Path


spec = importlib.util.spec_from_file_location("database_health", Path(__file__).with_name("database_health.py"))
database_health = importlib.util.module_from_spec(spec)
spec.loader.exec_module(database_health)


class DatabaseHealthTest(unittest.TestCase):
    def test_reports_wal_growth_and_missing_database(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "monitor.db"
            with self.assertRaises(FileNotFoundError):
                database_health.sample(database, now=100)
            database.write_bytes(b"d" * 100)
            Path(str(database) + "-wal").write_bytes(b"w" * 50)
            first = database_health.sample(database, now=100)
            self.assertEqual(first["total_bytes"], 150)
            self.assertIsNone(first["growth_bytes_per_day"])
            Path(str(database) + "-wal").write_bytes(b"w" * 150)
            second = database_health.sample(database, first, now=3700)
            self.assertEqual(second["growth_bytes_per_day"], 2400)
            self.assertGreater(second["free_bytes"], 0)


if __name__ == "__main__":
    unittest.main()
