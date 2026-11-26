#!/usr/bin/env python3.12
"""Scaffold a clod component from an authoring spec and what is on disk.

Usage: python3.12 -B tools/scaffold_component.py components/NAME --spec components/NAME/authoring/spec.json [--render] [--force]
"""

import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True

TOOLS_DIR = Path(__file__).resolve().parent
ROOT = TOOLS_DIR.parent
CLOD = Path(os.environ.get("CLOD_CHECKOUT", ROOT / "clod"))
STANDARD = ROOT / ".opencode" / "knowledge" / "component-standard.md"
GENERATOR = CLOD / "clod" / "diagram" / "generate.py"
DOT_DIR = "/opt/homebrew/bin"
# The GitHub owner of the library's namespace, com.clodhub.
OWNER = "clodhub"
PLATFORMS = ("server", "web", "apple", "android", "desktop")
SPEC_KEYS = {"name", "version", "sourceRevision", "title", "description", "facilities", "provides", "requires", "collect", "dependencies",
             "suites", "tools", "platforms", "text", "agentInstructions", "developerInstructions", "suitability",
               "migration", "keepsData", "infrastructure", "modules", "licence", "licenceFile", "noticeFile",
               "supportedPlatforms", "toolchains", "templates", "classes", "implements", "upstream", "compatibility", "maintenance",
               "release", "images", "vulnerabilityWaivers"}
# The spec fields classes/generate.py keeps; a new component's spec has none of them.
GENERATED = ("supportedPlatforms", "toolchains", "classes", "upstream", "compatibility", "maintenance", "release", "images",
             "vulnerabilityWaivers")
TEXT_KEYS = ("purpose", "userInputs", "integration", "infrastructure", "verification", "scenarios", "updates")
SKIPPED_ANYWHERE = {".DS_Store", "__pycache__"}
UNPACKAGED_TOP = {".git", ".gitignore", ".gitattributes", ".opencode", "authoring", "releases", "component.json", "AGENTS.md"}
DEFAULT_INFRASTRUCTURE = "No deployed service; injected resources and local library functions only."

sys.path.insert(0, str(CLOD))
from clod import model  # noqa: E402  (authority for manifest validation)


class ScaffoldError(Exception):
    pass


def fail(message):
    raise ScaffoldError(message)


def package_name(name):
    """The package name of a library component: pkg:clod/com.clodhub/<name>."""
    return f"{model.PACKAGE_PREFIX}{model.DEFAULT_NAMESPACE}/{name}"


def title(value):
    return value.replace("-", " ").replace("_", " ").capitalize()


def no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            fail(f"duplicate key {key!r}")
        result[key] = value
    return result


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=no_duplicates)
    except (OSError, ValueError) as error:
        fail(f"cannot read {path}: {error}")


def text_of(value, where):
    if type(value) is not str or not value.strip():
        fail(f"spec.{where} must be a nonempty string")
    return value


def list_of(value, where, nonempty=False):
    if type(value) is not list or (nonempty and not value):
        fail(f"spec.{where} must be {'a nonempty ' if nonempty else 'a '}array")
    return value


def load_spec(path, directory):
    spec = read_json(path)
    if type(spec) is not dict:
        fail("spec must be an object")
    unknown = set(spec) - SPEC_KEYS
    if unknown:
        fail(f"unknown spec fields: {sorted(unknown)}")
    name = text_of(spec.get("name"), "name")
    if model.parse_package(package_name(name)) is None or model.kind_of(name) != "component":
        fail("spec.name must be a component name: a letter, then letters, digits and single hyphens, not ending in -class or -recipe")
    if name != directory.name:
        fail(f"spec.name {name!r} differs from directory name {directory.name!r}")
    version = text_of(spec.get("version"), "version")
    if not re.fullmatch(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)", version):
        fail("spec.version must be three integers")
    text_of(spec.get("sourceRevision"), "sourceRevision")
    spec["title"] = text_of(spec.get("title", title(name)), "title")
    text_of(spec.get("description"), "description")
    for item in list_of(spec.get("facilities"), "facilities", True):
        if type(item) is not dict or set(item) != {"id", "description"}:
            fail("spec.facilities items need exactly id and description")
    for key in ("provides", "requires"):
        items = list_of(spec.get(key, []), key)
        for item in items:
            if type(item) is not dict or not {"id", "type", "version"} <= set(item) or set(item) - {"id", "type", "version", "path", "optional"}:
                fail(f"spec.{key} items need id, type, version and optional path (requires may add optional)")
            if "optional" in item and (key != "requires" or type(item["optional"]) is not bool):
                fail(f"spec.{key} item {item['id']!r}: optional must be a boolean and only on requires")
        spec[key] = items
    for key in ("provides", "requires"):
        for item in spec[key]:
            last = item["type"].rsplit("/", 1)[-1]
            wrapped = key == "requires" and any(other["type"] == item["type"] for other in spec["provides"])
            if item["id"] != ("wrapped-" + last if wrapped else last):
                fail(f"spec.{key} item {item['id']!r}: the id must be {('wrapped-' + last) if wrapped else last!r}")
    modules = spec.get("modules", {})
    if type(modules) is not dict or not all(type(k) is str and type(v) is str for k, v in modules.items()) \
            or set(modules) - {"web", "server", "desktop", "apple", "android"}:
        fail("spec.modules maps web, server, desktop, apple or android to an entry path relative to the component root")
    spec["modules"] = modules
    ids = [item["id"] for item in spec["provides"]] + [item["id"] for item in spec["requires"]]
    if len(ids) != len(set(ids)):
        fail("port ids must be unique across provides and requires (the diagram uses one port namespace)")
    spec["collect"] = list_of(spec.get("collect", []), "collect")
    spec["dependencies"] = list_of(spec.get("dependencies", []), "dependencies")
    suites = list_of(spec.get("suites"), "suites", True)
    if type(spec.get("tools", {})) is not dict:
        fail("spec.tools must be an object")
    spec["tools"] = spec.get("tools", {})
    platforms = list_of(spec.get("platforms", []), "platforms")
    for platform in platforms:
        if platform not in PLATFORMS:
            fail(f"unknown platform {platform!r}; expected one of {PLATFORMS}")
    spec["platforms"] = platforms
    from clod.foundations import PLATFORMS as supported_targets
    targets = list_of(spec.get("supportedPlatforms", []), "supportedPlatforms", "supportedPlatforms" in spec)
    if any(type(target) is not str or target not in supported_targets for target in targets) or len(set(targets)) != len(targets):
        fail("spec.supportedPlatforms must name unique supported targets")
    templates = list_of(spec.get("templates"), "templates")
    for path in templates:
        if type(path) is not str or not path.startswith("implementation/") or ".." in Path(path).parts:
            fail("spec.templates must name implementation source paths")
    if len(set(templates)) != len(templates):
        fail("spec.templates must be unique")
    if type(spec.get("text", {})) is not dict or set(spec.get("text", {})) - set(TEXT_KEYS):
        fail(f"spec.text keys must be among {TEXT_KEYS}")
    spec["text"] = spec.get("text", {})
    for item in suites:
        if type(item) is not dict or "id" not in item or "argv" not in item:
            fail("each suite needs at least id and argv")
    spec["suites"] = suites
    infra = spec.get("infrastructure", {})
    if type(infra) is not dict or set(infra) - {"accounts", "deployedReason"}:
        fail("spec.infrastructure allows accounts and deployedReason only")
    spec["infrastructure"] = infra
    try:
        model.licence(spec.get("licence"), "spec.licence")
        for field in ("licenceFile", "noticeFile"):
            relative = text_of(spec.get(field), field)
            candidate = directory / relative
            if Path(relative).is_absolute() or ".." in Path(relative).parts or candidate.is_symlink() \
                    or not candidate.resolve().is_relative_to(directory.resolve()) or not candidate.is_file():
                fail(f"spec.{field} must name a file within the component")
            if not candidate.read_text(encoding="utf-8").strip():
                fail(f"spec.{field} must name a nonempty document")
    except model.ClodError as error:
        fail(str(error))
    return spec


def walk(directory, top_excluded=True):
    """Return (files, warnings): relative POSIX path -> executable, for every packageable file."""
    files, warnings = {}, []
    for current, dirnames, filenames in os.walk(directory):
        relative = Path(current).relative_to(directory)
        at_top = relative == Path(".")
        kept = []
        for entry in sorted(dirnames):
            full = Path(current) / entry
            if full.is_symlink():
                fail(f"symbolic link: {(relative / entry).as_posix()}")
            if entry in SKIPPED_ANYWHERE or (at_top and top_excluded and entry in UNPACKAGED_TOP):
                continue
            if entry == "node_modules":
                warnings.append(f"skipped node_modules at {(relative / entry).as_posix()}")
                continue
            kept.append(entry)
        dirnames[:] = kept
        for entry in sorted(filenames):
            full = Path(current) / entry
            path = (relative / entry).as_posix()
            if full.is_symlink():
                fail(f"symbolic link: {path}")
            if entry in SKIPPED_ANYWHERE or (at_top and top_excluded and entry in UNPACKAGED_TOP):
                continue
            if not full.is_file():
                fail(f"not a regular file: {path}")
            files[path] = bool(full.stat().st_mode & 0o111)
    return files, warnings


class Writer:
    def __init__(self, base, force):
        self.base, self.force, self.log = base, force, []

    def put(self, relative, content, protect=False, newline=True):
        target = self.base / relative
        if protect and target.exists() and not self.force:
            self.log.append(f"kept   {relative}")
            return False
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_symlink():
            fail(f"refusing to write through symbolic link: {relative}")
        target.write_text(content if content.endswith("\n") or not newline else content + "\n", encoding="utf-8")
        self.log.append(f"wrote  {relative}")
        return True


def dump(value):
    return json.dumps(value, indent=2, ensure_ascii=False) + "\n"


def normalise_suites(spec, directory_files):
    suites = []
    for raw in spec["suites"]:
        allowed = {"id", "root", "argv", "cwd", "prepare", "tools", "fixtures", "locks", "network", "timeoutSeconds",
                   "expectedExit", "scope", "requirements"}
        if set(raw) - allowed:
            fail(f"suite {raw['id']!r}: unknown fields {sorted(set(raw) - allowed)}")
        argv = list_of(raw["argv"], f"suites[{raw['id']}].argv", True)
        tools = raw.get("tools")
        if tools is None:
            if argv[0] not in spec["tools"]:
                fail(f"suite {raw['id']!r}: tool {argv[0]!r} is not in spec.tools and the suite declares no tools")
            tools = {argv[0]: spec["tools"][argv[0]]}
        fixtures = raw.get("fixtures")
        if fixtures is None:
            fixtures = sorted(p for p in directory_files if p.startswith("tests/fixtures/"))
            if not fixtures:
                fixtures = sorted(p for p in directory_files if p.startswith("examples/"))[:1]
            if not fixtures:
                fixtures = [a for a in argv if a in directory_files][:1]
        suites.append({
            "id": raw["id"], "root": raw.get("root", "package"), "argv": argv, "cwd": raw.get("cwd", "."),
            "prepare": raw.get("prepare", []), "tools": tools, "fixtures": fixtures,
            "locks": raw.get("locks", ["tools.lock.json"]), "network": raw.get("network", False),
            "timeoutSeconds": raw.get("timeoutSeconds", 120), "expectedExit": raw.get("expectedExit", 0),
            "scope": raw.get("scope", "required-local"), "requirements": raw.get("requirements", [])})
    return suites


def port_name(port_id):
    return title(port_id)


def sample_value(setting):
    if "default" in setting:
        return None
    if setting["secret"]:
        return {"secretRef": "example/" + setting["id"]}
    return {"string": "example", "boolean": False, "integer": 1}[setting["type"]]


def visual_definition(spec):
    name = spec["name"]
    ports = []
    for port in spec["requires"]:
        ports.append({"id": port["id"], "name": port_name(port["id"]), "direction": "input", "kind": "interface",
                      "type": port["type"], "version": port["version"], "required": not port.get("optional", False),
                      "cardinality": "one"})
    for port in spec["provides"]:
        ports.append({"id": port["id"], "name": port_name(port["id"]), "direction": "output", "kind": "interface",
                      "type": port["type"], "version": port["version"]})
    settings = []
    for item in spec["collect"]:
        entry = {"id": item["id"], "name": port_name(item["id"]),
                 "type": "number" if item["type"] == "integer" else item["type"],
                 "required": item["required"], "secret": item["secret"]}
        if "default" in item:
            entry["default"] = item["default"]
        settings.append(entry)
    return {"id": model.package_id(package_name(name)), "name": spec["title"], "kind": "component", "status": "example",
            "package": package_name(name), "version": spec["version"], "ports": ports, "settings": settings}


def connected_example(spec, definition):
    name = spec["name"]
    consumer_ports = []
    for port in spec["provides"]:
        consumer_ports.append({"id": port["id"], "name": port_name(port["id"]), "direction": "input", "kind": "interface",
                               "type": port["type"], "version": port["version"], "required": True, "cardinality": "one"})
    for port in spec["requires"]:
        consumer_ports.append({"id": port["id"], "name": port_name(port["id"]), "direction": "output", "kind": "interface",
                               "type": port["type"], "version": port["version"]})
    configuration = {}
    for item in spec["collect"]:
        value = sample_value(item)
        if value is not None and (item["required"] or item["secret"]):
            configuration[item["id"]] = value
    connections = []
    for port in spec["provides"]:
        connections.append({"source": {"instance": "component", "port": port["id"]},
                            "target": {"instance": "consumer", "port": port["id"]}})
    for port in spec["requires"]:
        connections.append({"source": {"instance": "consumer", "port": port["id"]},
                            "target": {"instance": "component", "port": port["id"]}})
    return {
        "schemaVersion": 1, "id": name + "-connected", "title": f"{spec['title']}: connected consumer",
        "status": "example", "view": "component", "focus": "component",
        "definitions": [definition, {"id": "consumer", "name": "Example application consumer", "kind": "application",
                                     "status": "example", "revision": "0.1.0", "ports": consumer_ports, "settings": []}],
        "instances": [{"id": "component", "name": spec["title"], "definition": definition["id"], "configuration": configuration},
                      {"id": "consumer", "name": "Example consumer", "definition": "consumer", "configuration": {}}],
        "connections": connections}


def load_generator():
    loader = importlib.util.spec_from_file_location("clod_diagram_generate", GENERATOR)
    module = importlib.util.module_from_spec(loader)
    loader.loader.exec_module(module)
    return module


def render(design_path, output_dir):
    environment = {"PATH": DOT_DIR + os.pathsep + os.defpath, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(CLOD),
                   "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "HOME": os.environ.get("HOME", "/")}
    result = subprocess.run([sys.executable, "-B", str(GENERATOR), str(design_path), "--output-prefix", str(output_dir / "connected")],
                            env=environment, capture_output=True, text=True, timeout=120)
    if result.returncode:
        fail("diagram generator failed: " + (result.stderr.strip() or result.stdout.strip()))


def description_md(spec):
    provided = " and ".join(f"{p['type']} version {p['version']}" for p in spec["provides"])
    required = " and ".join(f"{p['type']} version {p['version']}" for p in spec["requires"])
    sentence = f"The {spec['name']} component at version {spec['version']}"
    if provided:
        sentence += f" provides {provided} to an illustrative application consumer"
    if required:
        sentence += (", which in turn supplies " if provided else " requires ") + required + (" from the same consumer" if not provided else "")
    arrows = len(spec["provides"]) + len(spec["requires"])
    sentence += ". "
    if arrows == 1:
        sentence += "The single arrow runs from the named output to its required input. "
    elif arrows:
        sentence += f"The {arrows} arrows each run from a named output to a required input of the same type and version. "
    sentence += ("Setting values shown are placeholders and secrets appear only as references."
                 if spec["collect"] else "No configuration or secret values are displayed.")
    return f"# {spec['title']}\n\n{sentence}\n"


def tool_commands(suites):
    return [f"`{' '.join(s['argv'])}` ({s['id']}, {s['scope']}; tools: {', '.join(f'{k} {v}' for k, v in s['tools'].items())})"
            for s in suites]


def settings_lines(spec):
    lines = []
    for item in spec["collect"]:
        kind = item["type"] if item["type"] != "string" else f"string, {item['grammar']}"
        flags = ["required" if item["required"] else "optional"]
        if item["secret"]:
            flags.append("secret, supplied only by reference")
        if "default" in item:
            value = item["default"]
            flags.append(f"default `{value if type(value) is str else json.dumps(value, ensure_ascii=False)}`")
        lines.append(f"- `{item['id']}` ({kind}; {'; '.join(flags)})")
    return lines


def todo(section):
    return f"TODO: describe {section}."


def contract_md(spec, artefacts, suites):
    t = spec["text"]
    name = spec["name"]
    out = [f"# {spec['title']} component", "", "## Purpose and facilities",
           t.get("purpose") or spec["description"], "",
           "Facilities: " + "; ".join(f"`{f['id']}` ({f['description']})" for f in spec["facilities"]) + ".", "",
           "## User inputs"]
    inputs = settings_lines(spec)
    out += [t.get("userInputs") or ("No installer settings." if not inputs else "The installer collects these settings:")]
    out += (["", *inputs] if inputs else [])
    out += ["", "## Artefacts and dependencies"]
    if artefacts:
        out += [f"`component.json` maps every `implementation/` source into `components/{name}/`:", ""]
        out += [f"- `{a['source']}` -> `{a['destination']}`" for a in artefacts]
    else:
        out += ["The package writes no application files."]
    out += ["", "Dependencies: " + (", ".join(f"`{d['name']}` {d['version']}" for d in spec["dependencies"]) or "none") + ".",
            "`tools.lock.json` pins verification tools" + (": " + ", ".join(f"{k} {v}" for k, v in spec["tools"].items()) if spec["tools"] else "") +
             f". Licence: `{spec['licence']}` (`{spec['licenceFile']}`, `{spec['noticeFile']}`).", "", "## Provided interfaces"]
    out += [f"- `{p['path']}` defines `{p['id']}` ({p['type']}) version {p['version']}." for p in spec["provides"]] or ["None."]
    out += ["", "## Required interfaces"]
    out += [f"- `{p['id']}` ({p['type']}) version {p['version']}, supplied by the recipe or application" + (" (optional; may stay unconnected)." if p.get("optional") else ".") for p in spec["requires"]] or ["None."]
    out += ["", "## Integration", t.get("integration") or todo("integration"), "",
            "## Visual definition and connected examples",
            "`visual/definition.json` maps its ports to the provided and required interfaces. "
            "`visual/examples/connected.json` connects the component to an illustrative application consumer. "
            "Both image formats and a text description are retained under `visual/output/`.", "",
            "## Infrastructure", t.get("infrastructure") or todo("infrastructure"), "",
            "## Verification", t.get("verification") or todo("verification"), ""]
    out += [f"- {line}" for line in tool_commands(suites)]
    out += ["", "## Scenarios and presentation", t.get("scenarios") or todo("scenarios"), "",
            "## Updates and removal", t.get("updates") or todo("updates and removal")]
    return "\n".join(out) + "\n"


def instructions_md(spec, suites):
    name = spec["name"]
    out = [f"# {spec['title']} instructions", "", "## For the agent",
           spec.get("agentInstructions") or todo("what the agent selects, configures and connects"), "",
           f"- Select package `{package_name(name)}` version `{spec['version']}`."]
    settings = settings_lines(spec)
    out += ["- Settings to configure:", *[f"  {line}" for line in settings]] if settings else ["- No installer settings."]
    for port in spec["provides"]:
        out.append(f"- Connect the provided `{port['id']}` ({port['type']} {port['version']}) to the consumer that needs it.")
    for port in spec["requires"]:
        out.append(f"- Supply `{port['id']}` ({port['type']} {port['version']}) from the component that provides it.")
    out.append("- Secrets are given by reference (`secretRef`) only, never by value."
               if any(s["secret"] for s in spec["collect"]) else "- No accounts or keys are needed.")
    out += ["", "## For the developer", spec.get("developerInstructions") or todo("what the developer supplies and how to adapt it"), "",
            "## How to check", "From the component directory:", ""]
    out += [f"- {line}" for line in tool_commands(suites)]
    out += [f"- `python3 -m clod review <digest> --store STORE --policy POLICY --trusted --allow-network` runs the packaged checks."]
    return "\n".join(out) + "\n"


def source_md(spec, implementation):
    out = ["# Source mapping", "", f"Reference revision: `{spec['sourceRevision']}`.", ""]
    out += [f"- `{path}` <- TODO: OurPoker/recipe file and line ranges; what was generalised; what was left behind." for path in implementation]
    if not implementation:
        out.append("- TODO: record the source of each file.")
    return "\n".join(out) + "\n"


def default_suitability(spec):
    return {"format": 1, "jobs": [spec["name"]],
            "profiles": [{"id": "any", "load": {}, "cost": "none", "regions": ["any"], "compliance": []}], "notes": ""}


def agents_md(spec):
    name = spec["name"]
    return (f"# {name}\n\nRepository: `{OWNER}/{name}`. Read `.opencode/knowledge/00-index.md` and `workflow.md` before work.\n"
             f"Licence: {spec['licence']} (`{spec['licenceFile']}`, `{spec['noticeFile']}`).\n"
             "The class contracts and package manifest define the public interfaces and verification checks.\n")


def index_md(spec, implementation):
    name = spec["name"]
    code = ", ".join(f"`{p}`" for p in implementation) or "none yet"
    return (f"# {name} ownership\n\nRepository: https://github.com/{OWNER}/{name}.git.\n"
             f"Current code: {code}.\n"
             "`component.json`, `tools.lock.json` and `visual/` are generated from `authoring/spec.json`. "
             "Licence documents belong to their respective authors and are included as supplied. "
             f"`CONTRACT.md`, `INSTRUCTIONS.md` and `SOURCE.md` are hand-written and kept on regeneration.\n"
            f"Every artefact installs under `components/{name}/`, which clod places at `components/<slot id>/` of the application; "
            f"other code reaches this component only through the entry modules in each interface document's `x-clodhub-modules` "
            f"(paths relative to the component root; set by the scaffolder from `spec.modules`, or from `implementation/<platform>/index.ts` "
            f"and the native source folders).\n")


def workflow_md(spec, suites):
    name = spec["name"]
    commands = "; ".join(f"`{' '.join(s['argv'])}`" for s in suites)
    return ("# Local workflow\n\n"
             f"Repository: `{OWNER}/{name}`. "
             f"Licence: `{spec['licence']}` (`{spec['licenceFile']}`, `{spec['noticeFile']}`).\n"
             f"Suite commands from the package root: {commands}.\n"
             "Pack: `python3 -B -m clod pack . --store STORE`. "
             "Review: `python3 -B -m clod review DIGEST --store STORE --policy POLICY --trusted --allow-network`.\n"
             "Always run Python with `-B`; never write bytecode into the repository.\n")


def build_manifest(spec, files, suites):
    name = spec["name"]
    implementation_files = sorted(p for p in files if p.startswith("implementation/"))
    artefacts = []
    for path in implementation_files:
        parts = path.split("/")
        if len(parts) < 3:
            fail(f"implementation file must sit under implementation/<platform>/: {path}")
        platform = parts[1]
        if platform not in spec["platforms"]:
            fail(f"{path}: platform {platform!r} is not listed in spec.platforms")
        artefacts.append({"source": path, "destination": "/".join(["components", name, platform, *parts[2:]]),
                          "template": path in spec["templates"], "executable": files[path]})
    present = {p.split("/")[1] for p in implementation_files}
    missing = [p for p in spec["platforms"] if p not in present]
    first_interface = next((p["path"] for p in spec["provides"]), None)
    interface_docs = sorted(p for p in files if p.startswith("interfaces/"))
    interface_docs += [p for p in ("suitability.json", "migration.json") if p in files]
    examples = sorted(p for p in files if p.startswith("examples/"))
    ports = {}
    for key in ("provides", "requires"):
        ports[key] = []
        for port in spec[key]:
            path = port.get("path") or (f"interfaces/{port['id']}.json" if key == "provides" else first_interface or (interface_docs[0] if interface_docs else ""))
            entry = {"id": port["id"], "type": port["type"], "version": port["version"], "path": path}
            if port.get("optional"):
                entry["optional"] = True
            ports[key].append(entry)
    infra = spec["infrastructure"]
    manifest = {
        "format": model.MANIFEST_FORMAT, "kind": "component",
        "identity": {"name": package_name(name), "version": spec["version"], "sourceRevision": spec["sourceRevision"],
                      "sourceTreeDigest": "0" * 64, "licence": spec["licence"]},
        "facilities": spec["facilities"],
        "documents": {"contract": "CONTRACT.md", "interfaces": interface_docs, "examples": examples, "licenceNotice": spec["licenceFile"]},
        "collect": spec["collect"],
        "files": [{"path": p, "executable": files[p]} for p in sorted(files)],
        "artefacts": artefacts,
        "dependencies": spec["dependencies"],
        "provides": ports["provides"], "requires": ports["requires"],
        "visual": {"definition": "visual/definition.json", "examples": ["visual/examples/connected.json"],
                   "outputs": ["visual/output/connected.svg", "visual/output/connected.png"],
                   "descriptions": ["visual/output/description.md"]},
        "capabilities": {"hosts": [], "secrets": [], "writes": [a["destination"] for a in artefacts]},
        "infrastructure": {"accounts": infra.get("accounts", []), "costStatus": "unmeasured",
                           "deployedReason": infra.get("deployedReason", DEFAULT_INFRASTRUCTURE)},
        "verification": suites,
        "upgrade": {"migration": "files-only"}}
    if set(spec["templates"]) - {item["source"] for item in artefacts}:
        fail("spec.templates names an absent implementation file")
    manifest["platforms"] = spec.get("supportedPlatforms", [])
    for field in ("toolchains", "classes", "implements", "upstream", "compatibility", "maintenance", "release", "images", "vulnerabilityWaivers"):
        if field in spec:
            manifest[field] = spec[field]
        elif field == "implements" or not first_scaffold(spec):
            fail("missing spec." + field)
    return manifest, missing


def first_scaffold(spec):
    return not any(field in spec for field in GENERATED)


def generated_fields(directory, spec, manifest):
    """A new component's generated fields, computed by classes/generate.py's own code from the classes `implements` names."""
    sys.path.insert(0, str(ROOT / "classes"))
    import generate
    import library
    folders = {generate.read(folder / "class.json")["name"]: folder for folder in library.classes()}
    names = [item.get("name") if type(item) is dict else item for item in list_of(spec["implements"], "implements", True)]
    unknown = [name for name in names if name not in folders]
    if unknown:
        fail(f"spec.implements names classes the library list does not: {unknown}")
    bundles = [generate.class_bundle(folders[name]) for name in names]
    try:
        return generate.metadata(directory, manifest, bundles, [bundle["reference"] for bundle in bundles])
    except (subprocess.CalledProcessError, ValueError, OSError) as error:
        fail(f"generated fields of a new component: {error}")


def entry_modules(spec, directory):
    """x-clodhub-modules: spec.modules, else index.ts of each script platform and the source folder of each native one."""
    if spec["modules"]:
        return dict(spec["modules"])
    modules = {}
    implementation = directory / "implementation"
    for platform in ("web", "server", "desktop"):
        if (implementation / platform / "index.ts").is_file():
            modules[platform] = f"{platform}/index.ts"
    for platform, folder in (("apple", "Sources"), ("android", "src/main/kotlin"), ("android", "src")):
        if platform not in modules and (implementation / platform / folder).is_dir():
            modules[platform] = f"{platform}/{folder}"
    return modules


def write_entry_modules(spec, directory, writer):
    """Set x-clodhub-modules in each provided interface document; documents keep every other field."""
    modules = entry_modules(spec, directory)
    for port in spec["provides"]:
        path = directory / (port.get("path") or f"interfaces/{port['id']}.json")
        if not path.is_file():
            continue
        document = read_json(path)
        if type(document) is not dict or document.get("x-clodhub-modules") == modules:
            continue
        document["x-clodhub-modules"] = modules
        writer.put(path.relative_to(directory).as_posix(), dump(document))


def scaffold(directory, spec_path, do_render, force):
    if not directory.is_dir():
        fail(f"not a directory: {directory}")
    spec = load_spec(spec_path, directory.resolve())
    writer = Writer(directory, force)
    notes = []

    if spec["tools"]:
        writer.put("tools.lock.json", dump({"format": 1, "dependencies": [], "tools": spec["tools"]}))
    write_entry_modules(spec, directory, writer)
    if "suitability" in spec or not (directory / "suitability.json").exists():
        writer.put("suitability.json", dump(spec.get("suitability") or default_suitability(spec)), protect=True)
    if "migration" in spec:
        writer.put("migration.json", dump(spec["migration"]), protect=True)

    definition = visual_definition(spec)
    design = connected_example(spec, definition)
    try:
        load_generator().validate_design(design)
    except Exception as error:
        fail(f"visual design rejected by clod's diagram generator: {error}")
    writer.put("visual/definition.json", dump(definition))
    writer.put("visual/examples/connected.json", dump(design))

    implementation, _ = walk(directory / "implementation") if (directory / "implementation").is_dir() else ({}, [])
    implementation = ["implementation/" + p for p in sorted(implementation)]
    preliminary, _ = walk(directory)
    suites = normalise_suites(spec, preliminary)
    writer.put("CONTRACT.md", contract_md(spec, [{"source": p, "destination": "components/{0}/{1}/{2}".format(
        spec["name"], p.split("/")[1], "/".join(p.split("/")[2:]))} for p in implementation if len(p.split("/")) >= 3], suites), protect=True)
    writer.put("INSTRUCTIONS.md", instructions_md(spec, suites), protect=True)
    writer.put("SOURCE.md", source_md(spec, implementation), protect=True)
    writer.put("AGENTS.md", agents_md(spec), protect=True)
    writer.put(".opencode/knowledge/00-index.md", index_md(spec, implementation), protect=True)
    writer.put(".opencode/knowledge/workflow.md", workflow_md(spec, suites), protect=True)

    if do_render:
        render(directory / "visual/examples/connected.json", directory / "visual/output")
        writer.log.append("wrote  visual/output/connected.svg, visual/output/connected.png")
        writer.put("visual/output/description.md", description_md(spec))
    absent = [p for p in ("connected.svg", "connected.png", "description.md") if not (directory / "visual/output" / p).is_file()]
    if absent:
        fail(f"visual/output/{absent} missing; rerun with --render")

    files, warnings = walk(directory)
    suites = normalise_suites(spec, files)
    manifest, missing_platforms = build_manifest(spec, files, suites)
    if first_scaffold(spec):
        manifest = generated_fields(directory, spec, manifest)
        notes.append("new component: classes/generate.py computed the generated fields; running it next writes them into the spec")
    if missing_platforms:
        notes.append(f"spec.platforms lists platforms with no implementation files: {missing_platforms}")
    notes.extend(warnings)
    try:
        model.validate_component(manifest)
    except model.ClodError as error:
        fail(f"manifest rejected by clod model.validate_component: {error.code} at {error.path}")
    writer.put("component.json", model.canonical(manifest).decode("utf-8"), newline=False)
    return writer.log, notes, manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("directory")
    parser.add_argument("--spec", required=True)
    parser.add_argument("--render", action="store_true", help="render visual/output/{connected.svg,connected.png,description.md}")
    parser.add_argument("--force", action="store_true", help="overwrite hand-written CONTRACT/INSTRUCTIONS/SOURCE/AGENTS/knowledge files")
    arguments = parser.parse_args(argv)
    try:
        log, notes, manifest = scaffold(Path(arguments.directory), Path(arguments.spec), arguments.render, arguments.force)
    except ScaffoldError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print("\n".join(log))
    for note in notes:
        print(f"note:  {note}")
    print(f"scaffolded {arguments.directory}: {len(manifest['files'])} files, {len(manifest['artefacts'])} artefacts")
    return 0


if __name__ == "__main__":
    sys.exit(main())
