"""A package check prepares its clean copy before running the declared suite."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import check_component as checker


class PreparationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name).resolve()
        self.package, self.work = root / "package", root / "work"
        self.package.mkdir()
        self.work.mkdir()
        (self.package / "prepare.py").write_text("from pathlib import Path\nPath('prepared').write_text('ready')\n")
        (self.package / "check.py").write_text("from pathlib import Path\nassert Path('prepared').read_text() == 'ready'\n")
        self.suite = {"id": "prepared", "root": "package", "scope": "required-local", "argv": ["python3", "check.py"],
                      "cwd": ".", "tools": {"python3": "3.12"}, "timeoutSeconds": 30, "expectedExit": 0,
                      "network": False, "prepare": [{"argv": ["python3", "prepare.py"], "cwd": ".", "network": True}]}
        self.manifest = {"verification": [self.suite], "files": [{"path": "prepare.py"}, {"path": "check.py"}]}

    def run_checks(self, allowed):
        report = checker.Report()
        counts = checker.check_suites(self.package, self.manifest, True, report, self.work, allowed)
        return counts, report.errors

    def test_network_preparation_requires_permission(self):
        counts, errors = self.run_checks(False)
        self.assertEqual(counts, (1, 0))
        self.assertIn("requires --allow-network", errors[0])
        self.assertFalse((self.package / "prepared").exists())

    def test_preparation_runs_in_the_copy_before_the_suite(self):
        counts, errors = self.run_checks(True)
        self.assertEqual(counts, (1, 1))
        self.assertEqual(errors, [])
        self.assertFalse((self.package / "prepared").exists())

    def test_a_failed_prepare_stops_the_suite(self):
        (self.package / "prepare.py").write_text("raise SystemExit(7)\n")
        counts, errors = self.run_checks(True)
        self.assertEqual(counts, (1, 0))
        self.assertIn("exit 7", errors[0])


if __name__ == "__main__":
    unittest.main()
