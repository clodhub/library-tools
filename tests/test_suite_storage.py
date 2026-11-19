"""A package check admits each suite's disk use, takes the machine locks its tools need and removes its copy when it ends."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import check_component as checker


class SuiteStorageTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name).resolve()
        self.package, self.work = root / "package", root / "work"
        self.package.mkdir()
        self.work.mkdir()
        state = mock.patch.dict(os.environ, {"AEPM_STATE_HOME": str(root / "state")})
        state.start()
        self.addCleanup(state.stop)
        # the suite reports which machine locks are held while it runs, and whether its copy is the only one present
        aepm = str(checker.AEPM)
        (self.package / "check.py").write_text(
            "import json, os, sys\nfrom pathlib import Path\nsys.path.insert(0, %r)\nos.environ['AEPM_STATE_HOME'] = %r\nfrom aepm import locks\n"
            "held = [h['kind'] for h in locks.holders() if h['held']]\n"
            "Path(sys.argv[1]).write_text(json.dumps({'held': held, 'copies': sorted(p.name for p in Path('..').iterdir())}))\n" % (aepm, str(root / "state")))
        self.out = root / "out"
        self.out.mkdir()
        self.suites = [{"id": f"s{n}", "root": "package", "scope": "required-local", "argv": ["python3", "check.py", str(self.out / f"s{n}.json")],
                        "cwd": ".", "tools": {"python3": "3.12"}, "timeoutSeconds": 30, "expectedExit": 0, "network": False, "prepare": []}
                       for n in range(2)]
        self.manifest = {"verification": self.suites, "files": [{"path": "check.py"}]}

    def run_checks(self):
        report = checker.Report()
        return checker.check_suites(self.package, self.manifest, True, report, self.work, False), report.errors

    def test_each_suite_runs_alone_and_its_copy_goes(self):
        counts, errors = self.run_checks()
        self.assertEqual((counts, errors), ((2, 2), []))
        for n in range(2):
            self.assertEqual(json.loads((self.out / f"s{n}.json").read_text())["copies"], [f"suite{n}"])
        self.assertEqual(list((self.work / "package").iterdir()), [])

    def test_a_suite_with_a_native_tool_holds_its_lock(self):
        with mock.patch.object(checker.locks, "kinds_for", return_value=["xcode"]):
            counts, errors = self.run_checks()
        self.assertEqual(errors, [])
        self.assertEqual(json.loads((self.out / "s0.json").read_text())["held"], ["xcode"])

    def test_too_little_space_stops_before_copying(self):
        with mock.patch("aepm.space.free_bytes", return_value=1024):
            counts, errors = self.run_checks()
        self.assertEqual(counts, (0, 0))
        self.assertIn("insufficient-space", errors[0])
        self.assertIn('"required"', errors[0])
        self.assertFalse((self.work / "package").exists())


if __name__ == "__main__":
    unittest.main()
