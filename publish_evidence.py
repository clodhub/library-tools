#!/usr/bin/env python3.12
"""Publish recipe build reports (tools/evidence/*.json) to clodhub.com.

usage: python3.12 -B tools/publish_evidence.py --key ch_... [REPORT.json ...]

Without arguments every report in tools/evidence/ is sent. Each goes to POST /api/dev/recipes/<name>/reports with a manage
key belonging to a catalogue curator (the catalogue's "Catalogue publish API", Build reports). A report already published
answers "unchanged".
"""
import argparse
import json
from pathlib import Path
import sys

import publish_catalogue as publisher

EVIDENCE = Path(__file__).resolve().parent / "evidence"
BUILD_REPORT_FORMAT = 2
FIELDS = {"format", "kind", "recipe", "clod", "date", "agent", "prompt", "conditions", "answers", "measured", "results",
          "update", "problems", "rating"}


def check(report, path):
    """The report's shape as the catalogue requires it; raises ValueError naming the file and what is wrong."""
    if set(report) != FIELDS:
        raise ValueError(f"{path}: fields {sorted(set(report) ^ FIELDS)} missing or extra")
    if report["format"] != BUILD_REPORT_FORMAT or report["kind"] != "recipe-build":
        raise ValueError(f"{path}: format {BUILD_REPORT_FORMAT}, kind recipe-build expected")
    recipe = report["recipe"]
    _, _, model, _, _ = publisher.load_clod()
    parts = model.parse_package(recipe["name"])
    if parts is None or parts["kind"] != "recipe" or not str(recipe["digest"]).startswith("sha256:"):
        raise ValueError(f"{path}: recipe name or digest")
    return parts["name"]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--key", required=True)
    parser.add_argument("reports", nargs="*", type=Path)
    args = parser.parse_args(argv)
    paths = args.reports or sorted(EVIDENCE.glob("*.json"))
    failed = 0
    for path in paths:
        report = json.loads(path.read_text())
        short = check(report, path)
        status, answer = publisher.post(f"/api/dev/recipes/{short}/reports", report, args.key)
        print(path.name, status, answer.get("status") or answer.get("error") or answer, flush=True)
        failed += status not in (200, 201)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
