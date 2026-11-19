#!/usr/bin/env python3.12
"""Copy independent class interface documents to the components that require them.

Usage: python3.12 -B tools/sync_required_interfaces.py [--check] [--components DIR]
For every components/*/component.json, each required interface defined by a packaged class gets
interfaces/required-<id>.json, a byte copy of its class document. Class identities and inventory bytes must agree.
With --check nothing is written and differences give exit status 1.
"""

import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parent.parent


class SyncError(Exception):
    pass


def manifests(components):
    result = {}
    for directory in sorted(Path(components).iterdir()):
        path = directory / "component.json"
        if directory.is_dir() and not directory.name.startswith(".") and path.is_file():
            try:
                result[directory.name] = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as error:
                raise SyncError(f"{directory.name}/component.json: cannot parse ({error})") from error
    return result


def class_interfaces(found):
    """(interface type, version) -> (component, packaged authoritative document)."""
    result = {}
    for name, manifest in sorted(found.items()):
        for entry in manifest["classes"]:
            for port in entry["descriptor"]["provides"]:
                result.setdefault((port["type"], port["version"]), (name, port["path"]))
    return result


def class_conflicts(components, only=None):
    """Conflicting class identities, descriptors or inventory bytes (involving `only` when given)."""
    components = Path(components)
    identities = {}
    problems = []
    import sys
    sys.path.insert(0, str(ROOT / "aepm"))
    from aepm.model import canonical
    for name, manifest in manifests(components).items():
        for entry in manifest["classes"]:
            reference, descriptor = entry["reference"], entry["descriptor"]
            encoded = canonical(descriptor)
            if hashlib.sha256(encoded).hexdigest() != reference["digest"] and (only is None or only == name):
                problems.append(f"{name}: class descriptor digest differs for {reference['name']} {reference['version']}")
            identities.setdefault((reference["name"], reference["version"]), []).append((name, reference["digest"], encoded))
            for item in descriptor["inventory"]:
                path = components / name / item["path"]
                if (not path.is_file() or path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != item["digest"]) \
                        and (only is None or only == name):
                    problems.append(f"{name}: class inventory digest differs for {item['path']}")
    for (name, version), entries in sorted(identities.items()):
        if len({(digest, encoded) for _, digest, encoded in entries}) > 1 and (only is None or only in {package for package, _, _ in entries}):
            problems.append(f"conflicting class {name} {version}: " + ", ".join(sorted(package for package, _, _ in entries)))
    return problems


def required_copies(components, only=None):
    """Relative path -> bytes that each requiring component must hold (for one component when `only` is given)."""
    components = Path(components)
    found = manifests(components)
    provided = class_interfaces(found)
    copies = {}
    for name, manifest in found.items():
        if only is not None and name != only:
            continue
        for port in manifest.get("requires", []):
            source = provided.get((port["type"], port["version"]))
            if source is None or source[0] == name:
                continue
            document = components / source[0] / source[1]
            if not document.is_file():
                raise SyncError(f"{source[0]} provides {port['type']} but {source[1]} is missing")
            relative = f"interfaces/required-{port['id']}.json"
            key = relative if only is not None else f"{name}/{relative}"
            copies[key] = document.read_bytes()
    return copies


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="report differences without writing")
    parser.add_argument("--components", type=Path, default=ROOT / "components")
    arguments = parser.parse_args(argv)
    try:
        copies = required_copies(arguments.components)
        found = manifests(arguments.components)
    except SyncError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    differences, manifest_paths = [], []
    for relative, data in sorted(copies.items()):
        target = arguments.components / relative
        if target.is_symlink():
            print(f"error: {relative} is a symbolic link", file=sys.stderr)
            return 1
        if target.is_file() and target.read_bytes() == data:
            continue
        differences.append(relative)
        if not arguments.check:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
    for relative in sorted(copies):
        name, path = relative.split("/", 1)
        port_id = path[len("interfaces/required-"):-len(".json")]
        port = next(p for p in found[name]["requires"] if p["id"] == port_id)
        if port["path"] != path:
            manifest_paths.append(f"{name}/component.json: requires {port_id!r} names {port['path']}, expected {path}")
    for item in differences:
        print(("differs: " if arguments.check else "wrote: ") + item)
    for item in manifest_paths:
        print("manifest: " + item)
    conflicts = class_conflicts(arguments.components)
    for item in conflicts:
        print("classes: " + item)
    print(f"{len(copies)} required documents; {len(differences)} " + ("differ" if arguments.check else "written")
          + f"; {len(manifest_paths)} manifest paths to fix; {len(conflicts)} class conflicts")
    return 1 if (arguments.check and differences) or manifest_paths or conflicts else 0


if __name__ == "__main__":
    sys.exit(main())
