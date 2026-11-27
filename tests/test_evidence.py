"""Build reports have the shape the catalogue accepts and add up: a sample report, and every report in tools/evidence."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import library  # noqa: E402
import publish_evidence  # noqa: E402

SAMPLE = {
    "format": 2, "kind": "recipe-build",
    "recipe": {"name": "pkg:clod/com.clodhub/ourpoker-recipe", "version": "0.13.0", "digest": "sha256:" + "0" * 64},
    "clod": {"repository": "clodhub/clod", "commit": "0123456"},
    "date": "2026-11-26",
    "agent": {"harness": "OpenCode", "model": "anthropic/claude-opus-5-5", "session": "a new session"},
    "prompt": "Build an application from the OurPoker recipe.", "conditions": "Defaults throughout.",
    "answers": {"targets": ["web"]},
    "measured": {"method": "event stream", "elapsedMinutes": 10.0, "waitingForPersonMinutes": 0, "activeMinutes": 10.0,
                 "replies": 3, "toolCalls": 5,
                 "tokens": {"input": 1, "output": 2, "reasoning": 3, "cacheRead": 4, "cacheWrite": 5, "total": 15}, "costUsd": 0.5},
    "results": [{"target": "server", "built": True, "tested": "tests", "ran": "locally"},
                {"target": "web", "built": True, "tested": "browser check", "ran": "locally"}],
    "update": "Not tried.", "problems": [], "rating": {"score": 5, "of": 5, "by": "the agent", "mostHelpful": "the recipe"},
}


def recipe_platforms(name, version):
    """The platforms of the named recipe at that version, from its folder's history in the workspace."""
    for folder in library.recipes():
        if json.loads((folder / "component.json").read_text())["identity"]["name"] != name:
            continue
        for commit in subprocess.check_output(["git", "-C", str(folder), "log", "--format=%H", "--", "component.json"], text=True).split():
            manifest = json.loads(subprocess.check_output(["git", "-C", str(folder), "show", f"{commit}:component.json"]))
            if manifest["identity"]["version"] == version:
                return set(manifest["platforms"])
    raise AssertionError(f"{name} {version} is not in the history of a listed recipe")


class Evidence(unittest.TestCase):
    def reports(self):
        paths = sorted(publish_evidence.EVIDENCE.glob("*.json"))
        return [(Path("sample.json"), copy.deepcopy(SAMPLE))] + [(path, json.loads(path.read_text())) for path in paths]

    def test_each_report_has_the_catalogue_shape(self):
        for path, report in self.reports():
            short = publish_evidence.check(report, path)
            self.assertEqual(short, report["recipe"]["name"].rsplit("/", 1)[1], path.name)
            if path.name != "sample.json":
                self.assertEqual(path.name, f"{short}-{report['recipe']['version']}-{report['date']}.json")

    def test_figures_add_up(self):
        for path, report in self.reports():
            measured = report["measured"]
            tokens = measured["tokens"]
            self.assertEqual(sum(value for key, value in tokens.items() if key != "total"), tokens["total"], path.name)
            self.assertLessEqual(measured["activeMinutes"], measured["elapsedMinutes"], path.name)
            self.assertAlmostEqual(measured["elapsedMinutes"] - measured["waitingForPersonMinutes"], measured["activeMinutes"], delta=0.5, msg=path.name)

    def test_every_chosen_target_has_a_result(self):
        for path, report in self.reports():
            if "targets" in report["answers"]:
                targets = set(report["answers"]["targets"]) | {"server"}
            else:
                # A recipe without a targets question builds every platform it offers.
                targets = recipe_platforms(report["recipe"]["name"], report["recipe"]["version"])
            self.assertEqual({result["target"] for result in report["results"]}, targets, path.name)

    def test_a_wrong_report_is_refused(self):
        for change, message in ((lambda r: r.pop("rating"), "fields"), (lambda r: r.update(format=1), "format 2"),
                                (lambda r: r["recipe"].update(name="clodhub/ourpoker"), "recipe name"),
                                (lambda r: r["recipe"].update(name="pkg:clod/com.clodhub/ourpoker"), "recipe name")):
            report = copy.deepcopy(SAMPLE)
            change(report)
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                publish_evidence.check(report, Path("sample.json"))


if __name__ == "__main__":
    unittest.main()
