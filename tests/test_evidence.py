"""Every build report in tools/evidence has the shape the catalogue accepts and adds up."""
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import publish_evidence  # noqa: E402


class Evidence(unittest.TestCase):
    def reports(self):
        paths = sorted(publish_evidence.EVIDENCE.glob("*.json"))
        self.assertTrue(paths)
        return [(path, json.loads(path.read_text())) for path in paths]

    def test_each_report_has_the_catalogue_shape(self):
        for path, report in self.reports():
            self.assertEqual(publish_evidence.check(report, path), "ourpoker")

    def test_figures_add_up(self):
        for path, report in self.reports():
            measured = report["measured"]
            tokens = measured["tokens"]
            self.assertEqual(sum(value for key, value in tokens.items() if key != "total"), tokens["total"], path.name)
            self.assertLessEqual(measured["activeMinutes"], measured["elapsedMinutes"], path.name)
            self.assertAlmostEqual(measured["elapsedMinutes"] - measured["waitingForPersonMinutes"], measured["activeMinutes"], delta=0.5, msg=path.name)

    def test_every_answered_target_has_a_result(self):
        for path, report in self.reports():
            targets = set(report["answers"]["targets"]) | {"server"}
            self.assertEqual({result["target"] for result in report["results"]}, targets, path.name)

    def test_a_report_with_a_missing_field_is_refused(self):
        path, report = self.reports()[0]
        del report["rating"]
        with self.assertRaises(ValueError):
            publish_evidence.check(report, path)


if __name__ == "__main__":
    unittest.main()
