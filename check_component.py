#!/usr/bin/env python3.12
"""Validate a clod component directory against the component standard.

Usage: python3.12 -B tools/check_component.py components/NAME [--no-tests]
       python3.12 -B tools/check_component.py --all [--no-tests]   (every component in tools/library.json)
Prints one JSON result {component, ok, errors, warnings, testsRun, testsPassed} (a table with --all).
"""

import argparse
import contextlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.dont_write_bytecode = True

TOOLS_DIR = Path(__file__).resolve().parent
ROOT = TOOLS_DIR.parent
CLOD = Path(os.environ.get("CLOD_CHECKOUT", ROOT / "clod"))
STANDARD = ROOT / ".opencode" / "knowledge" / "component-standard.md"
GENERATOR = CLOD / "clod" / "diagram" / "generate.py"
TEMP_BASE = Path("/var/folders/yk/8tzc9b_1713dlhdp5vzkgg100000gn/T/opencode")
PINNED_PYTHON = Path("/Users/george/.pyenv/versions/3.12.8/bin/python3.12")
PLATFORMS = {"server", "web", "apple", "android", "desktop"}
TOP_PACKAGED = {"CONTRACT.md", "INSTRUCTIONS.md", "SOURCE.md", "LICENCE", "NOTICE", "suitability.json", "migration.json", "interfaces",
                 "examples", "implementation", "tests", "tools.lock.json", "visual", "classes", "notices", "PROVENANCE.md"}
TOP_UNPACKAGED = {"component.json", "AGENTS.md", ".opencode", "authoring", ".git", ".gitignore", ".gitattributes"}
JUNK = {".DS_Store", "__pycache__"}
CONTRACT_HEADINGS = ["Purpose and facilities", "User inputs", "Artefacts and dependencies", "Provided interfaces",
                     "Required interfaces", "Integration", "Visual definition and connected examples", "Infrastructure",
                     "Verification", "Scenarios and presentation", "Updates and removal"]
STRONG_SECRETS = [
    ("private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("AWS access key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("GitHub token", re.compile(r"gh[pousr]_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{40,}")),
    ("Stripe live key", re.compile(r"[sr]k_live_[0-9A-Za-z]{16,}")),
    ("Slack token", re.compile(r"xox[baprs]-[0-9A-Za-z-]{10,}")),
    ("Google API key", re.compile(r"AIza[0-9A-Za-z_-]{35}")),
]
WEAK_SECRET = re.compile(r"""(?i)(secret|password|passwd|api[_-]?key|token)["']?\s*[:=]\s*["'][A-Za-z0-9/+_=.-]{20,}["']""")
LOAD_KEYS = {"concurrentPeople", "requestsPerDay", "storedMegabytes"}
COSTS = {"free", "paid", "metered", "none"}
VERSION = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)")
KNOWN_JOBS = {"server-host", "data-store", "video", "hosting"}
VALIDATOR = TOOLS_DIR / "standards" / "validate-interface.mjs"

sys.path.insert(0, str(TOOLS_DIR))
import library  # noqa: E402
from sync_required_interfaces import class_conflicts, required_copies  # noqa: E402
sys.path.insert(0, str(CLOD))
from clod import locks, space  # noqa: E402
from clod.model import DEFAULT_NAMESPACE, MANIFEST_FORMAT, PACKAGE_PREFIX, ClodError, short_name  # noqa: E402
# Interface types of the standard's table: `<namespace>/<name>` in the default namespace.
TYPES = DEFAULT_NAMESPACE + "/"


class Report:
    def __init__(self):
        self.errors, self.warnings = [], []

    def error(self, message):
        self.errors.append(message)

    def warn(self, message):
        self.warnings.append(message)


def no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate key {key!r}")
        result[key] = value
    return result


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=no_duplicates)


def load_json(path, label, report):
    try:
        return read_json(path)
    except (OSError, ValueError) as error:
        report.error(f"{label}: cannot parse ({error})")
        return None


def standard_table():
    rows = {}
    try:
        lines = STANDARD.read_text(encoding="utf-8").splitlines()
    except OSError:
        return rows
    for line in lines:
        match = re.fullmatch(r"\|\s*([a-z0-9][a-z0-9-]*)\s*\|([^|]*)\|([^|]*)\|\s*", line)
        if match and match.group(1) != "component":
            split = lambda cell: {item.strip() for item in cell.split(",") if item.strip()}
            rows[match.group(1)] = (split(match.group(2)), split(match.group(3)))
    return rows


def load_generator():
    loader = importlib.util.spec_from_file_location("clod_diagram_generate", GENERATOR)
    module = importlib.util.module_from_spec(loader)
    loader.loader.exec_module(module)
    return module


def clean_environment(home):
    return {"PATH": os.defpath, "HOME": str(home), "TMPDIR": str(home), "PYTHONDONTWRITEBYTECODE": "1",
            "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "CI": "true"}


def run_clod(arguments, work):
    python = PINNED_PYTHON if PINNED_PYTHON.exists() else Path(sys.executable)
    environment = clean_environment(work)
    environment["PYTHONPATH"] = str(CLOD)
    result = subprocess.run([str(python), "-B", "-m", "clod", *arguments], env=environment, cwd=work,
                            capture_output=True, text=True, timeout=300)
    try:
        return json.loads(result.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"ok": False, "error": {"code": "unparseable-output", "path": (result.stderr or result.stdout)[:200]}}


def walk_disk(directory, report):
    """All files and directories on disk (skipping .git), reporting links and node_modules."""
    files = []
    for current, dirnames, filenames in os.walk(directory):
        relative = Path(current).relative_to(directory)
        keep = []
        for entry in sorted(dirnames):
            path = (relative / entry).as_posix()
            if (Path(current) / entry).is_symlink():
                report.error(f"symbolic link: {path}")
                continue
            if entry == ".git" and relative == Path("."):
                continue
            if entry == "node_modules":
                report.warn(f"node_modules present (development dependencies, never packaged by clod): {path}")
                continue
            keep.append(entry)
        dirnames[:] = keep
        for entry in sorted(filenames):
            path = (relative / entry).as_posix()
            if (Path(current) / entry).is_symlink():
                report.error(f"symbolic link: {path}")
            elif entry in JUNK:
                continue
            else:
                files.append(path)
    return files


def check_secrets(directory, files, report):
    for path in files:
        full = directory / path
        if full.is_symlink() or path.endswith((".png", ".jpg", ".jpeg", ".gif", ".ico", ".woff", ".woff2")):
            continue
        try:
            if full.stat().st_size > 2_000_000:
                continue
            content = full.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for label, pattern in STRONG_SECRETS:
            if pattern.search(content):
                report.error(f"secret pattern ({label}) in {path}")
        if WEAK_SECRET.search(content):
            report.warn(f"possible hard-coded credential in {path}")
        name = path.casefold().split("/")[-1]
        if name.startswith(".env") or name.endswith((".pem", ".key", ".p12", ".pfx", ".keystore", ".jks")):
            report.error(f"secret-bearing file name: {path}")


def check_layout(directory, disk, manifest, report):
    listed = {item["path"] for item in manifest["files"]}
    unpackaged = {}
    for path in disk:
        top = path.split("/")[0]
        if "__pycache__" in path.split("/"):
            report.warn(f"bytecode directory on disk: {path}")
            continue
        if top in TOP_UNPACKAGED:
            if path in listed:
                report.error(f"unpackaged area is listed in component.json: {path}")
            continue
        if top not in TOP_PACKAGED:
            if path in listed:
                continue  # listed in component.json and present: clod packs it, so the layout accepts it
            unpackaged[top] = unpackaged.get(top, 0) + 1
            continue
        if path not in listed:
            report.error(f"file on disk but not in component.json files (rerun scaffold): {path}")
    for top, count in sorted(unpackaged.items()):
        report.warn(f"{top}: outside the allowlist, not packaged ({count} files)")
    for path in sorted(listed):
        top = path.split("/")[0]
        if path.startswith("implementation/") and path.split("/")[1] not in PLATFORMS:
            report.warn(f"implementation folder is not a standard platform ({sorted(PLATFORMS)}): {path}")
        if path not in disk:
            report.error(f"listed file missing on disk: {path}")


def check_documents(directory, manifest, report):
    listed = {item["path"] for item in manifest["files"]}
    for name in ("INSTRUCTIONS.md", "SOURCE.md", "CONTRACT.md"):
        path = directory / name
        if not path.is_file():
            report.error(f"{name} missing")
            continue
        content = path.read_text(encoding="utf-8", errors="replace")
        if not content.strip():
            report.error(f"{name} is empty")
            continue
        if name not in listed:
            report.error(f"{name} is not listed in component.json files")
        if "TODO:" in content:
            report.warn(f"{name} contains TODO placeholders")
        if name == "CONTRACT.md":
            headings = re.findall(r"^## (.+?)\s*$", content, re.M)
            missing = [h for h in CONTRACT_HEADINGS if h not in headings]
            if missing:
                report.warn(f"CONTRACT.md lacks standard headings: {missing}")
    for name, label in (("LICENCE", "licence"), ("NOTICE", "notice"), ("tools.lock.json", "tools lock")):
        if not (directory / name).is_file():
            report.error(f"{label} file {name} missing")
        elif name in ("LICENCE", "NOTICE") and name not in listed:
            report.error(f"{name} is not listed in component.json files")
    for path in {manifest["documents"]["licenceNotice"], "NOTICE"}:
        document = directory / path
        if document.is_file() and not document.read_text(encoding="utf-8", errors="replace").strip():
            report.error(f"{path} is empty")


def check_standard_fields(directory, manifest, report):
    identity = manifest["identity"]
    if identity["name"] != f"{PACKAGE_PREFIX}{DEFAULT_NAMESPACE}/{directory.name}":
        report.error(f"identity.name {identity['name']!r} does not match directory {directory.name!r}")
    if manifest["kind"] != "component":
        report.warn(f"kind is {manifest['kind']!r}, expected 'component'")
    spec = directory / "authoring" / "spec.json"
    if spec.is_file():
        revision = read_json(spec).get("sourceRevision")
        if identity["sourceRevision"] != revision:
            report.warn(f"identity.sourceRevision {identity['sourceRevision']!r} differs from authoring/spec.json sourceRevision {revision!r}")
    if manifest["upgrade"] != {"migration": "files-only"}:
        report.warn(f"upgrade is {manifest['upgrade']}, standard says migration files-only")
    if manifest["format"] != MANIFEST_FORMAT:
        report.warn(f"manifest format {manifest['format']} (the package manifest format is {MANIFEST_FORMAT})")
    for item in manifest["collect"]:
        if item.get("secret") and "default" in item:
            report.error(f"collect {item['id']!r}: secrets never have defaults")


def expected_port_id(manifest, key, port):
    """The type's last segment; a requirement of a type the package also provides is wrapped-<segment>."""
    last = port["type"].rsplit("/", 1)[-1]
    if key == "requires" and any(item["type"] == port["type"] for item in manifest["provides"]):
        return "wrapped-" + last
    return last


def check_interfaces(directory, manifest, report):
    table = standard_table()
    if not table:
        report.warn("could not read the interface table from the standard")
        return
    provided_anywhere = {TYPES + t for provides, _ in table.values() for t in provides}
    name = directory.name
    row = table.get(name)
    if row is None:
        report.warn(f"component name {name!r} is not in the standard's table")
    for key in ("provides", "requires"):
        for port in manifest[key]:
            last = port["type"].rsplit("/", 1)[-1]
            if not port["type"].startswith(TYPES) or port["type"] not in {TYPES + t for p, r in table.values() for t in p | r}:
                report.warn(f"{key} type {port['type']!r} is not in the standard's interface table (addition: report it)")
            elif key == "requires" and port["type"] not in provided_anywhere:
                report.warn(f"requires type {port['type']!r} is not provided by any component in the table")
            if port["id"] != expected_port_id(manifest, key, port):
                report.error(f"{key} id {port['id']!r} must be {expected_port_id(manifest, key, port)!r} (the type's last segment"
                             f"{', prefixed wrapped- because the package also provides that type' if expected_port_id(manifest, key, port) != last else ''})")
            if port["version"] != "1.0.0":
                report.warn(f"{key} {port['id']!r} version {port['version']} (standard interfaces are 1.0.0)")
    if row is not None:
        for key, expected in (("provides", row[0]), ("requires", row[1])):
            actual = {p["type"].rsplit("/", 1)[-1] for p in manifest[key]}  # table rows list types, not port ids
            if actual != expected:
                report.warn(f"{key} {sorted(actual)} differs from the standard's table {sorted(expected)}")
    listed = {item["path"] for item in manifest["files"]}
    documents = sorted(p for p in listed if p.startswith("interfaces/") and p.endswith(".json"))
    results = validate_documents(directory, documents, report)
    for key in ("provides", "requires"):
        for port in manifest[key]:
            if not (port["path"].startswith("interfaces/") and port["path"].endswith(".json")) or port["path"] not in listed:
                report.error(f"{key} {port['id']!r}: path {port['path']} is not a packaged interfaces/*.json document")
                continue
            result = results.get(port["path"])
            if result is not None and (result.get("type"), result.get("version")) != (port["type"], port["version"]):
                report.error(f"{port['path']}: names {result.get('type')!r} {result.get('version')!r}, "
                             f"but the {key} entry {port['id']!r} is {port['type']!r} {port['version']!r}")
    # The library's components, and the checked one when it is not listed: required documents and classes come from these.
    folders = library.components()
    if directory.resolve() not in {folder.resolve() for folder in folders}:
        folders.append(directory)
    for path, expected in required_copies(folders, directory.name, library.classes()).items():
        current = directory / path
        if not current.is_file() or current.read_bytes() != expected:
            report.error(f"{path} is not a byte copy of its independent class document (run tools/sync_required_interfaces.py)")
    for problem in class_conflicts(folders, directory.name):
        report.error(problem)
    check_entry_modules(directory, manifest, documents, report, {port["path"] for port in manifest["provides"]})


NATIVE_PLATFORMS = {"apple": "swift", "android": "kotlin"}
SCRIPT_PLATFORMS = ("web", "server", "desktop")
MODULE_EXPORTS = TOOLS_DIR / "standards" / "module-exports.mjs"
SWIFT_PUBLIC = r"(?:public|open)\s+(?:(?:final|static|class|override|nonisolated|indirect|mutating|@\w+)\s+)*(?:func|class|struct|enum|protocol|actor|typealias|var|let|extension)\s+`?{name}\b"
KOTLIN_PUBLIC = r"(?m)^(?![ \t]*(?:private|internal|protected)\b)[ \t]*(?:(?:public|open|final|abstract|sealed|data|inline|value|suspend|operator|infix|override|enum|annotation|fun\s+interface|const|lateinit|companion|@\w+(?:\([^)]*\))?)\s+)*(?:fun|class|interface|object|val|var|typealias)\s+(?:<[^>]*>\s*)?(?:[\w.]+\.)?`?{name}\b"


def entry_prefix(manifest):
    """Where the package's files land relative to the installed root: components/<short name>/ for a component, nothing for a recipe."""
    return "" if manifest["kind"] == "recipe" else "components/" + short_name(manifest["identity"]["name"]) + "/"


def method_names(document, tag):
    """First dot-separated segment of every OpenRPC method tagged `tag`."""
    names = set()
    for method in document.get("methods", []) if isinstance(document.get("methods"), list) else []:
        tags = [item.get("name") if isinstance(item, dict) else None for item in method.get("tags", []) or []]
        if isinstance(method, dict) and tag in tags and isinstance(method.get("name"), str):
            names.add(method["name"].split(".")[0])
    return names


def typescript_exports(sources):
    """source path -> set of exported names, or None when the module could not be read."""
    if not sources:
        return {}
    node = shutil.which("node")
    if node is None:
        return {source: None for source in sources}
    result = subprocess.run([node, str(MODULE_EXPORTS), *map(str, sources)], capture_output=True, text=True, timeout=300)
    found = {}
    for line in result.stdout.splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            continue
        found[item["file"]] = set(item["exports"]) if item.get("ok") else None
    return {source: found.get(str(Path(source).resolve())) for source in sources}


def check_entry_modules(directory, manifest, documents, report, provided=None):
    """Every interface document declares x-clodhub-modules. For the documents of provided interfaces (all, when `provided` is
    None) each entry is installed and exports or declares its tagged methods; a required copy's entries are the provider's."""
    prefix = entry_prefix(manifest)
    by_destination = {item["destination"]: item["source"] for item in manifest["artefacts"]}
    pending = []
    for path in documents:
        document = load_json(directory / path, path, report)
        if not isinstance(document, dict):
            continue
        modules = document.get("x-clodhub-modules")
        if not isinstance(modules, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in modules.items()):
            report.error(f"{path}: x-clodhub-modules must be an object mapping platform to path")
            continue
        if provided is not None and path not in provided:
            continue
        for platform, entry in modules.items():
            if platform in SCRIPT_PLATFORMS:
                source = by_destination.get(prefix + entry)
                if source is None or not (directory / source).is_file():
                    report.error(f"{path}: {platform} entry module {entry} is not an installed file of the package ({prefix}{entry})")
                    continue
                pending.append((path, platform, entry, directory / source, method_names(document, "typescript")))
            elif platform in NATIVE_PLATFORMS:
                folder = prefix + entry.rstrip("/") + "/"
                sources = [directory / source for destination, source in by_destination.items() if destination.startswith(folder)]
                if not sources:
                    report.error(f"{path}: {platform} folder {entry} has no installed file ({folder})")
                    continue
                language = NATIVE_PLATFORMS[platform]
                text = "\n".join(item.read_text(encoding="utf-8", errors="replace") for item in sources if item.is_file())
                pattern = SWIFT_PUBLIC if language == "swift" else KOTLIN_PUBLIC
                for name in sorted(method_names(document, language)):
                    if not re.search(pattern.replace("{name}", re.escape(name)), text):
                        report.error(f"{path}: method {name} tagged {language} is not a public name in {entry}")
            else:
                report.error(f"{path}: x-clodhub-modules platform {platform!r} is not one of {sorted((*SCRIPT_PLATFORMS, *NATIVE_PLATFORMS))}")
    exports = typescript_exports([item[3] for item in pending])
    for path, platform, entry, source, names in pending:
        found = exports.get(source)
        if found is None:
            report.error(f"{path}: {platform} entry module {entry} could not be read by the TypeScript compiler")
            continue
        for name in sorted(names - found):
            report.error(f"{path}: {platform} entry module {entry} does not export {name} (method tagged typescript)")


def check_artefact_locations(manifest, report):
    prefix = entry_prefix(manifest)
    for item in manifest["artefacts"]:
        if manifest["kind"] == "component" and not item["destination"].startswith(prefix):
            report.error(f"artefact {item['source']} installs at {item['destination']}, outside {prefix}")
        if manifest["kind"] == "recipe" and item["destination"].split("/")[0] == "components":
            report.error(f"recipe artefact {item['source']} installs under components/")


def validate_documents(directory, documents, report):
    """Run the standards validator on each interface document; return path -> result."""
    if not documents:
        return {}
    node = shutil.which("node")
    if node is None:
        report.error("node is not installed; interface documents cannot be validated")
        return {}
    try:
        result = subprocess.run([node, str(VALIDATOR), *documents], cwd=directory, capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.TimeoutExpired) as error:
        report.error(f"interface validator did not run ({error})")
        return {}
    results = {}
    for line in result.stdout.splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            continue
        results[item.get("file")] = item
        for message in item.get("errors", []):
            report.error(f"{item.get('file')}: {item.get('kind') or 'interface'} validation: {message}")
    missing = [path for path in documents if path not in results]
    if missing or (result.returncode != 0 and not any(item.get("errors") for item in results.values())):
        report.error(f"interface validator failed (exit {result.returncode}): {(result.stderr or result.stdout).strip()[:300]}")
    return results


def check_json_documents(directory, manifest, report):
    for path in manifest["documents"]["interfaces"]:
        if path.endswith(".json"):
            load_json(directory / path, path, report)
    for path in ["tools.lock.json", manifest["visual"]["definition"], *manifest["visual"]["examples"]]:
        if (directory / path).is_file():
            load_json(directory / path, path, report)
    generator = None
    for path in manifest["visual"]["examples"]:
        document = load_json(directory / path, path, report) if (directory / path).is_file() else None
        if isinstance(document, dict):
            stale = sorted({d.get("version") for d in document.get("definitions", []) if isinstance(d, dict)
                            and d.get("package") == manifest["identity"]["name"] and d.get("version") != manifest["identity"]["version"]})
            if stale:
                report.error(f"{path}: names this package at version {', '.join(map(str, stale))}, not {manifest['identity']['version']}")
        if document is not None:
            try:
                generator = generator or load_generator()
                generator.validate_design(document)
            except Exception as error:
                report.error(f"{path}: rejected by clod's diagram generator ({error})")
    definition_path = directory / manifest["visual"]["definition"]
    if definition_path.is_file():
        definition = load_json(definition_path, manifest["visual"]["definition"], report)
        if isinstance(definition, dict):
            if manifest["kind"] == "component" and (definition.get("package") != manifest["identity"]["name"] or definition.get("version") != manifest["identity"]["version"]):
                report.error("visual/definition.json package or version differs from the manifest identity (clod install refuses it: diagram-version)")
            ports = {(p.get("id"), p.get("type"), p.get("version")) for p in definition.get("ports", [])}
            wanted = {(p["id"], p["type"], p["version"]) for p in manifest["provides"] + manifest["requires"]}
            if ports != wanted:
                report.error("visual/definition.json ports differ from the manifest provides/requires (clod install refuses it: diagram-interface)")


def validate_suitability(document, report):
    label = "suitability.json"
    if not isinstance(document, dict):
        return report.error(f"{label}: expected an object")
    unknown = set(document) - {"format", "jobs", "profiles", "notes"}
    if unknown:
        report.warn(f"{label}: unknown fields {sorted(unknown)}")
    if document.get("format") != 1:
        report.error(f"{label}: format must be 1")
    jobs = document.get("jobs")
    if not isinstance(jobs, list) or not jobs or not all(isinstance(j, str) and j for j in jobs):
        report.error(f"{label}: jobs must be a nonempty array of strings")
    profiles = document.get("profiles")
    if not isinstance(profiles, list) or not profiles:
        return report.error(f"{label}: profiles must be a nonempty array")
    ids = []
    for index, profile in enumerate(profiles):
        where = f"{label} profiles[{index}]"
        if not isinstance(profile, dict):
            report.error(f"{where}: expected an object")
            continue
        for key in ("id", "load", "cost", "regions", "compliance"):
            if key not in profile:
                report.error(f"{where}: missing {key}")
        extra = set(profile) - {"id", "load", "cost", "regions", "compliance", "provider", "notes"}
        if extra:
            report.warn(f"{where}: unknown fields {sorted(extra)}")
        if not isinstance(profile.get("id"), str) or not profile.get("id"):
            report.error(f"{where}: id must be a nonempty string")
        ids.append(profile.get("id"))
        load = profile.get("load", {})
        if not isinstance(load, dict):
            report.error(f"{where}: load must be an object")
        else:
            for key, value in load.items():
                if type(value) not in (int, float) or value < 0:
                    report.error(f"{where}: load.{key} must be a non-negative number")
                if key not in LOAD_KEYS:
                    report.warn(f"{where}: unusual load key {key!r}")
        if profile.get("cost") not in COSTS:
            report.error(f"{where}: cost must be one of {sorted(COSTS)}")
        for key in ("regions", "compliance"):
            value = profile.get(key, [])
            if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                report.error(f"{where}: {key} must be an array of strings")
        if not profile.get("regions"):
            report.error(f"{where}: regions must be nonempty (use [\"any\"])")
        if "provider" in profile:
            provider = profile["provider"]
            if not isinstance(provider, dict) or not {"name", "tier", "limitsSource"} <= set(provider):
                report.error(f"{where}: provider needs name, tier and limitsSource")
            else:
                source = provider["limitsSource"]
                if source is not None and not (isinstance(source, str) and source.startswith(("http://", "https://"))):
                    report.error(f"{where}: limitsSource must be a URL or null")
                if source is None and not str(profile.get("notes", "")).strip() and not str(document.get("notes", "")).strip():
                    report.warn(f"{where}: limitsSource is null; say so in notes")
        elif profile.get("cost") not in ("none", None):
            report.warn(f"{where}: cost {profile.get('cost')!r} without a provider")
    if len(ids) != len(set(ids)):
        report.error(f"{label}: duplicate profile ids")
    if isinstance(jobs, list):
        for job in jobs:
            if job not in KNOWN_JOBS:
                report.warn(f"{label}: job {job!r} is not a standard job (fine if it is the component name)")


def validate_migration(document, files, report):
    label = "migration.json"
    if not isinstance(document, dict):
        return report.error(f"{label}: expected an object")
    if document.get("format") != 1:
        report.error(f"{label}: format must be 1")
    for key in ("stores", "export", "import", "steps", "verification", "limits", "retention"):
        if key not in document:
            report.error(f"{label}: missing {key}")
    stores = document.get("stores", [])
    if not isinstance(stores, list) or not stores:
        report.error(f"{label}: stores must be a nonempty array")
    else:
        for store in stores:
            schema = store.get("schema") if isinstance(store, dict) else None
            if not isinstance(store, dict) or not isinstance(store.get("id"), str) or not isinstance(schema, dict) \
                    or not {"name", "version", "file"} <= set(schema):
                report.error(f"{label}: each store needs id and schema{{name,version,file}}")
            elif schema["file"] not in files:
                report.error(f"{label}: schema file {schema['file']} is not a packaged file")
    portable = False
    for key in ("export", "import"):
        entries = document.get(key, [])
        if not isinstance(entries, list) or not entries:
            report.error(f"{label}: {key} must be a nonempty array")
            continue
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("format"), str) or type(entry.get("portable")) is not bool \
                    or not isinstance(entry.get("operation"), str):
                report.error(f"{label}: {key} entries need format, portable (boolean) and operation")
            elif entry["portable"]:
                portable = portable or entry["format"] == "ndjson"
    if not portable:
        report.error(f"{label}: at least one portable ndjson format is required")
    for step in document.get("steps", []) if isinstance(document.get("steps"), list) else []:
        if not isinstance(step, dict) or not all(isinstance(step.get(k), str) and VERSION.fullmatch(step[k]) for k in ("from", "to")) \
                or not isinstance(step.get("description"), str):
            report.error(f"{label}: steps need from, to (versions) and description")
    verification = document.get("verification")
    if isinstance(verification, dict):
        if type(verification.get("counts")) is not bool or not isinstance(verification.get("checksums"), str) \
                or type(verification.get("sampleReadBack")) is not int:
            report.error(f"{label}: verification needs counts (boolean), checksums (string), sampleReadBack (integer)")
        end = verification.get("endToEnd")
        if not isinstance(end, str) or end not in files:
            report.error(f"{label}: verification.endToEnd must name a packaged file")
    elif "verification" in document:
        report.error(f"{label}: verification must be an object")
    limits = document.get("limits")
    if isinstance(limits, dict):
        if type(limits.get("maxMegabytes")) is not int or type(limits.get("requiresStop")) is not bool \
                or not isinstance(limits.get("cannotPreserve"), list):
            report.error(f"{label}: limits needs maxMegabytes (integer), requiresStop (boolean), cannotPreserve (array)")
    elif "limits" in document:
        report.error(f"{label}: limits must be an object")
    retention = document.get("retention")
    if isinstance(retention, dict):
        if type(retention.get("afterRemovalDays")) is not int:
            report.error(f"{label}: retention.afterRemovalDays must be an integer")
    elif "retention" in document:
        report.error(f"{label}: retention must be an object")


def declares_data(directory, files, suitability):
    spec = directory / "authoring" / "spec.json"
    if spec.is_file():
        try:
            if read_json(spec).get("keepsData") is True:
                return True
        except (OSError, ValueError, AttributeError):
            pass
    if any(p.startswith("interfaces/") and p.endswith(".schema.json") for p in files):
        return True
    return isinstance(suitability, dict) and isinstance(suitability.get("jobs"), list) and "data-store" in suitability["jobs"]


def check_sidecars(directory, manifest, report):
    files = {item["path"] for item in manifest["files"]}
    suitability = None
    if "suitability.json" not in files or not (directory / "suitability.json").is_file():
        report.error("suitability.json missing or not packaged")
    else:
        suitability = load_json(directory / "suitability.json", "suitability.json", report)
        if suitability is not None:
            validate_suitability(suitability, report)
    migration_exists = (directory / "migration.json").is_file()
    if migration_exists:
        document = load_json(directory / "migration.json", "migration.json", report)
        if document is not None:
            validate_migration(document, files, report)
        if "migration.json" not in files:
            report.error("migration.json is not packaged")
    elif declares_data(directory, files, suitability):
        report.error("component declares data (keepsData, *.schema.json or data-store job) but migration.json is missing")


def tool_executable(tool):
    if tool == "python3" and sys.version_info[:2] == (3, 12):
        return Path(sys.executable)
    sys.path.insert(0, str(CLOD))
    from clod.host_tools import host_tool
    return host_tool(tool)


def tool_matches(executable, version):
    for flag in ("--version", "-version"):
        try:
            result = subprocess.run([str(executable), flag], capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if re.search(r"(?<![0-9.])" + re.escape(version.lstrip("v")) + r"(?![0-9])", result.stdout + result.stderr):
            return True
    return False


def count_tests(output):
    run = re.search(r"(?:ℹ|#)\s+tests\s+(\d+)", output)
    passed = re.search(r"(?:ℹ|#)\s+pass\s+(\d+)", output)
    return (int(run.group(1)), int(passed.group(1))) if run and passed else None


def check_suites(directory, manifest, run_tests, report, work, allow_network=False):
    """Return (testsRun, testsPassed)."""
    suites = manifest["verification"]
    local = [s for s in suites if s["root"] == "package" and s["scope"] == "required-local"]
    if not local:
        report.error("no package-root required-local suite")
        return 0, 0
    files = {item["path"] for item in manifest["files"]}
    for suite in suites:
        for argument in suite["argv"][1:]:
            if not argument.startswith("-") and "*" not in argument and re.search(r"\.(ts|js|mjs|cjs|py|swift|kt|sh)$", argument) \
                    and argument not in files:
                (report.error if "/" in argument else report.warn)(
                    f"suite {suite['id']!r}: argv names {argument}, which is not a packaged file")
        for tool in suite["tools"]:
            executable = tool_executable(tool)
            if executable is None:
                if suite in local:
                    report.error(f"suite {suite['id']!r}: tool {tool} is not installed")
                else:
                    report.warn(f"suite {suite['id']!r} not runnable here: tool {tool} is not installed")
            elif not tool_matches(executable, suite["tools"][tool]):
                (report.error if suite in local else report.warn)(
                    f"suite {suite['id']!r}: installed {tool} is not version {suite['tools'][tool]}")
    for suite in suites:
        if suite not in local:
            report.warn(f"{suite['scope']} suite {suite['id']!r} not run by this checker")
    lock = directory / "tools.lock.json"
    if lock.is_file():
        locked = load_json(lock, "tools.lock.json", report)
        locked_tools = locked.get("tools", {}) if isinstance(locked, dict) else {}
        for suite in suites:
            for tool, version in suite["tools"].items():
                if locked_tools.get(tool) != version:
                    report.warn(f"suite {suite['id']!r}: tools.lock.json has {tool} {locked_tools.get(tool)!r}, suite declares {version!r}")
    if not run_tests:
        report.warn("tests not run (--no-tests)")
        return 0, 0
    total_run = total_passed = 0
    copy_bytes = sum((directory / item["path"]).stat().st_size for item in manifest["files"])
    for number, suite in enumerate(local):
        package = work / "package" / f"suite{number}"
        home = work / "home" / f"suite{number}"
        held = contextlib.ExitStack()
        try:
            installs = sum(1 for step in suite["prepare"] if Path(step["argv"][0]).name in ("npm", "npx"))
            try:
                # the suite's copy plus each npm install it runs, held against other processes' admissions; then the
                # machine-wide locks its tools need, so heavy native checks of every agent run one at a time
                held.enter_context(space.reserve(work, copy_bytes + installs * space.NPM_INSTALL, purpose=f"check {directory.name} {suite['id']}"))
                for kind in locks.kinds_for(suite):
                    held.enter_context(locks.heavy(kind, purpose=f"check {directory.name} {suite['id']}"))
            except ClodError as error:
                report.error(f"suite {suite['id']!r}: {error.code}: {json.dumps(error.document().get('detail'))}")
                return total_run, total_passed
            (home / "bin").mkdir(parents=True)
            for item in manifest["files"]:
                target = package / item["path"]
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(directory / item["path"], target)
            for tool in suite["tools"]:
                executable = tool_executable(tool)
                if executable is None:
                    return total_run, total_passed
                (home / "bin" / tool).symlink_to(executable.resolve())
            environment = clean_environment(home)
            environment["PATH"] = str(home / "bin") + os.pathsep + os.defpath
            steps = [*suite["prepare"], {"argv": suite["argv"], "cwd": suite["cwd"], "network": suite["network"]}]
            if not allow_network and any(step["network"] for step in steps):
                report.error(f"suite {suite['id']!r}: network preparation requires --allow-network")
                total_run += 1
                continue
            try:
                result = None
                deadline = time.monotonic() + suite["timeoutSeconds"]
                for step in steps:
                    result = subprocess.run(step["argv"], cwd=package / step["cwd"], env=environment,
                                            capture_output=True, text=True, timeout=max(0.001, deadline - time.monotonic()))
                    if result.returncode != 0:
                        break
            except subprocess.TimeoutExpired:
                report.error(f"suite {suite['id']!r}: timed out after {suite['timeoutSeconds']}s")
                total_run += 1
                continue
            except OSError as error:
                report.error(f"suite {suite['id']!r}: cannot start ({error})")
                total_run += 1
                continue
            output = result.stdout + result.stderr
            counts = count_tests(output)
            if counts:
                total_run += counts[0]
                total_passed += counts[1]
            else:
                total_run += 1
                total_passed += int(result.returncode == suite["expectedExit"])
            if result.returncode != suite["expectedExit"]:
                tail = " | ".join(line.strip() for line in output.strip().splitlines()[-6:])
                report.error(f"suite {suite['id']!r} failed with exit {result.returncode}: {tail[:400]}")
            elif counts and counts[0] == 0:
                report.error(f"suite {suite['id']!r}: no tests were discovered")
        finally:
            # each suite's copy and home go when it ends, so a run holds one suite's files at a time
            shutil.rmtree(package, ignore_errors=True)
            shutil.rmtree(home, ignore_errors=True)
            held.close()
    return total_run, total_passed

def check_component(directory, run_tests=True, allow_network=False):
    directory = directory.resolve()
    report = Report()
    result = {"component": directory.name, "ok": False, "errors": report.errors, "warnings": report.warnings,
              "testsRun": 0, "testsPassed": 0}
    if not directory.is_dir():
        report.error(f"not a directory: {directory}")
        return result
    if not TEMP_BASE.is_dir():
        report.error(f"temporary base {TEMP_BASE} is missing")
        return result
    work = Path(tempfile.mkdtemp(prefix="check-component-", dir=TEMP_BASE)).resolve()  # /var is a symlink; clod refuses links
    try:
        disk = walk_disk(directory, report)
        check_secrets(directory, disk, report)
        manifest_path = directory / "component.json"
        manifest = load_json(manifest_path, "component.json", report) if manifest_path.is_file() else None
        if manifest is None:
            if not manifest_path.is_file():
                report.error("component.json missing")
        else:
            outcome = run_clod(["pack", str(directory), "--store", str(work / "store")], work)
            if not outcome.get("ok"):
                error = outcome.get("error", {})
                report.error(f"clod pack refused: {error.get('code')} at {error.get('path')}")
            else:
                ref = outcome["result"]
                inspected = run_clod(["inspect", ref["digest"], "--store", str(work / "store")], work)
                if not inspected.get("ok"):
                    error = inspected.get("error", {})
                    report.error(f"clod inspect refused: {error.get('code')} at {error.get('path')}")
                else:
                    packed = inspected["result"]
                    if packed["identity"]["sourceTreeDigest"] == "0" * 64:
                        report.error("packed sourceTreeDigest is still all zeros")
                    if packed["identity"]["name"] != manifest["identity"]["name"] or packed["identity"]["version"] != manifest["identity"]["version"]:
                        report.error("inspected identity differs from component.json")
                    if packed["files"] != manifest["files"]:
                        report.error("inspected file list differs from component.json")
            if manifest is not None:
                try:
                    check_layout(directory, disk, manifest, report)
                    check_standard_fields(directory, manifest, report)
                    check_documents(directory, manifest, report)
                    check_json_documents(directory, manifest, report)
                    check_artefact_locations(manifest, report)
                    check_interfaces(directory, manifest, report)
                    check_sidecars(directory, manifest, report)
                    result["testsRun"], result["testsPassed"] = check_suites(directory, manifest, run_tests, report, work, allow_network)
                except (KeyError, TypeError, IndexError, AttributeError) as error:
                    report.error(f"manifest is structurally invalid for further checks ({type(error).__name__}: {error})")
    finally:
        shutil.rmtree(work, ignore_errors=True)
    result["ok"] = not report.errors
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("directory", nargs="?")
    parser.add_argument("--no-tests", action="store_true")
    parser.add_argument("--allow-network", action="store_true", help="allow declared network preparation steps")
    parser.add_argument("--all", action="store_true", help="check every component in tools/library.json and print a table")
    arguments = parser.parse_args(argv)
    if arguments.all == bool(arguments.directory):
        parser.error("give exactly one of DIRECTORY or --all")
    if not arguments.all:
        result = check_component(Path(arguments.directory), not arguments.no_tests, arguments.allow_network)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if result["ok"] else 1
    results = [check_component(path, not arguments.no_tests, arguments.allow_network) for path in library.components()]
    width = max([len(r["component"]) for r in results] + [9])
    print(f"{'component'.ljust(width)}  ok     errors  warnings  tests")
    for r in results:
        print(f"{r['component'].ljust(width)}  {'ok' if r['ok'] else 'FAIL':<5}  {len(r['errors']):>6}  {len(r['warnings']):>8}  {r['testsPassed']}/{r['testsRun']}")
    for r in results:
        for message in r["errors"]:
            print(f"  {r['component']}: error: {message}")
    print(f"{sum(r['ok'] for r in results)}/{len(results)} ok")
    return 0 if all(r["ok"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
