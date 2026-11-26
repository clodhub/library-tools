#!/usr/bin/env python3.12
"""Copy independent class interface documents to the components that require them.

Usage: python3.12 -B tools/sync_required_interfaces.py [--check]
For every component in tools/library.json, each required interface a listed class defines (or, failing that, a class
a listed component packages) gets interfaces/required-<id>.json, a byte copy of its class document. Class identities and
inventory bytes must agree.
With --check nothing is written and differences give exit status 1.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
import library  # noqa: E402


class SyncError(Exception):
    pass


def manifests(folders):
    """Folder name -> component.json of each given component folder."""
    result = {}
    for directory in map(Path, folders):
        try:
            result[directory.name] = json.loads((directory / "component.json").read_text(encoding="utf-8"))
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


def class_conflicts(folders, only=None):
    """Conflicting class identities, descriptors or inventory bytes among the given component folders (involving `only`
    when given)."""
    folders = {Path(directory).name: Path(directory) for directory in folders}
    identities = {}
    problems = []
    sys.path.insert(0, os.environ.get("CLOD_CHECKOUT", str(ROOT / "clod")))
    from clod.model import canonical
    for name, manifest in manifests(folders.values()).items():
        for entry in manifest["classes"]:
            reference, descriptor = entry["reference"], entry["descriptor"]
            encoded = canonical(descriptor)
            if hashlib.sha256(encoded).hexdigest() != reference["digest"] and (only is None or only == name):
                problems.append(f"{name}: class descriptor digest differs for {reference['name']} {reference['version']}")
            identities.setdefault((reference["name"], reference["version"]), []).append((name, reference["digest"], encoded))
            for item in descriptor["inventory"]:
                path = folders[name] / item["path"]
                if (not path.is_file() or path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != item["digest"]) \
                        and (only is None or only == name):
                    problems.append(f"{name}: class inventory digest differs for {item['path']}")
    for (name, version), entries in sorted(identities.items()):
        if len({(digest, encoded) for _, digest, encoded in entries}) > 1 and (only is None or only in {package for package, _, _ in entries}):
            problems.append(f"conflicting class {name} {version}: " + ", ".join(sorted(package for package, _, _ in entries)))
    return problems


def listed_class_documents(class_folders):
    """(interface type, version) -> (class folder name, its document) for each type a listed class provides."""
    result = {}
    for directory in map(Path, class_folders):
        try:
            descriptor = json.loads((directory / "class.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise SyncError(f"{directory.name}/class.json: cannot parse ({error})") from error
        for port in descriptor["provides"]:
            result.setdefault((port["type"], port["version"]), (directory.name, directory / Path(port["path"]).name))
    return result


def required_copies(folders, only=None, class_folders=()):
    """Relative path -> bytes that each requiring component among the given folders must hold (for one component when
    `only` is given). A listed class's own folder is the source of the types it provides; a type no listed class provides
    comes from a component that bundles its class."""
    folders = {Path(directory).name: Path(directory) for directory in folders}
    found = manifests(folders.values())
    bundled = class_interfaces(found)
    listed = listed_class_documents(class_folders)
    copies = {}
    for name, manifest in found.items():
        if only is not None and name != only:
            continue
        own = {(port["type"], port["version"]) for port in manifest.get("provides", [])}
        for port in manifest.get("requires", []):
            key = (port["type"], port["version"])
            if key in own:
                continue
            if key in listed:
                source, document = listed[key]
            elif key in bundled and bundled[key][0] != name:
                source, document = bundled[key][0], folders[bundled[key][0]] / bundled[key][1]
            else:
                continue
            if not document.is_file():
                raise SyncError(f"{source} provides {port['type']} but {document} is missing")
            relative = f"interfaces/required-{port['id']}.json"
            key = relative if only is not None else f"{name}/{relative}"
            copies[key] = document.read_bytes()
    return copies


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="report differences without writing")
    arguments = parser.parse_args(argv)
    try:
        folders = library.components()
        copies = required_copies(folders, class_folders=library.classes())
        found = manifests(folders)
    except (SyncError, library.LibraryError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    located = {directory.name: directory for directory in folders}
    differences, manifest_paths = [], []
    for relative, data in sorted(copies.items()):
        name, path = relative.split("/", 1)
        target = located[name] / path
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
    conflicts = class_conflicts(folders)
    for item in conflicts:
        print("classes: " + item)
    print(f"{len(copies)} required documents; {len(differences)} " + ("differ" if arguments.check else "written")
          + f"; {len(manifest_paths)} manifest paths to fix; {len(conflicts)} class conflicts")
    return 1 if (arguments.check and differences) or manifest_paths or conflicts else 0


if __name__ == "__main__":
    sys.exit(main())
