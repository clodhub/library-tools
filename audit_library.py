#!/usr/bin/env python3.12
"""Audit package ownership, upstream releases and signed verification records of the library (tools/library.json).

Usage: PYTHONPATH=clod python3.12 -B tools/audit_library.py [--check-updates] [--store STORE --policy POLICY]
Without --check-updates no network requests are made. The report never infers a successful test date from package metadata.
"""
import argparse
import json
from pathlib import Path
import re
import sys
import tempfile
from urllib.parse import quote, unquote, urlsplit
from urllib.request import Request, urlopen

import publish_catalogue as publisher


def release_endpoint(source):
    url = urlsplit(source)
    parts = url.path.strip("/").split("/")
    if url.scheme == "https" and url.hostname == "github.com" and len(parts) >= 2:
        owner, repo = parts[:2]
        if all(re.fullmatch(r"[A-Za-z0-9_.-]+", value) for value in (owner, repo)):
            return f"https://api.github.com/repos/{owner}/{repo.removesuffix('.git')}/releases/latest"
    return None


def fetch_json(address):
    request = Request(address, headers={"User-Agent": "ourapps-library-audit/1.0", "Accept": "application/json"})
    with urlopen(request, timeout=30) as response:
        return json.load(response)


def compare_release(current, latest):
    left, right = current.removeprefix("v"), latest.removeprefix("v")
    if left == right:
        return "current"
    if re.fullmatch(r"\d+\.\d+\.\d+", left) and re.fullmatch(r"\d+\.\d+\.\d+", right):
        return "update-available" if tuple(map(int, right.split("."))) > tuple(map(int, left.split("."))) else "pinned-ahead"
    return "different-release"


def check_release(name, version, source, reader=fetch_json):
    endpoint = release_endpoint(source)
    if endpoint is None:
        return {"name": name, "source": source, "status": "manual-check", "reason": "no supported release endpoint"}
    try:
        latest = reader(endpoint)["tag_name"]
        if not isinstance(latest, str) or not latest:
            raise ValueError("release tag is missing")
        return {"name": name, "source": source, "version": version, "latest": latest, "status": compare_release(version, latest)}
    except Exception as error:
        return {"name": name, "source": source, "status": "unavailable", "reason": str(error)}


def upstream_update(record, reader=fetch_json):
    if record["purl"].startswith("pkg:npm/"):
        package = unquote(record["purl"][8:]).rsplit("@", 1)[0]
        endpoint = f"https://registry.npmjs.org/{quote(package, safe='@/')}/latest"
        try:
            latest = reader(endpoint)["version"]
            if not isinstance(latest, str) or not latest:
                raise ValueError("package version is missing")
            return {"name": record["name"], "source": endpoint, "version": record["version"], "latest": latest,
                    "status": compare_release(record["version"], latest)}
        except Exception as error:
            return {"name": record["name"], "source": endpoint, "status": "unavailable", "reason": str(error)}
    return check_release(record["name"], record["version"], record["repository"], reader)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check-updates", action="store_true")
    parser.add_argument("--store", type=Path)
    parser.add_argument("--policy", type=Path)
    args = parser.parse_args()
    if bool(args.store) != bool(args.policy):
        parser.error("--store and --policy must be supplied together")
    _, bundle, _, _, _ = publisher.load_clod()
    from clod.foundations import maintenance_facts
    directories = publisher.library.components() + publisher.library.recipes()
    cache = {}

    def read(address):
        if address not in cache:
            cache[address] = fetch_json(address)
        return cache[address]

    failures = 0
    with tempfile.TemporaryDirectory(prefix="library-audit-", dir=publisher.TEMP) as work:
        for directory in directories:
            try:
                reference = bundle.pack(directory, Path(work).resolve() / "store")
                manifest, _ = publisher.verified_package(reference, Path(work).resolve() / "store")
                record = {"package": reference, "maintenance": manifest["maintenance"], "platforms": manifest["platforms"],
                          "upstream": manifest["upstream"], "lastSuccessfulVerification": None, "verification": "not-checked"}
                if args.store:
                    try:
                        facts = maintenance_facts(reference, args.store.resolve(), args.policy.resolve())
                        record.update(facts)
                        record["verification"] = "signed-review-checked"
                    except Exception as error:
                        record["verification"] = "unavailable"
                        record["verificationError"] = str(error)
                        failures += 1
                if args.check_updates:
                    updates = manifest["maintenance"]["updates"]
                    own = check_release(reference["name"], reference["version"], updates["source"], read) if updates["strategy"] == "release" \
                        else {"name": reference["name"], "source": updates["source"], "status": "manual-check"}
                    record["updates"] = [own, *(upstream_update(source, read) for source in manifest["upstream"])]
                    if any(update["status"] == "unavailable" for update in record["updates"]):
                        failures += 1
                print(json.dumps(record, separators=(",", ":")))
            except Exception as error:
                print(json.dumps({"directory": str(directory), "error": str(error)}))
                failures += 1
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
