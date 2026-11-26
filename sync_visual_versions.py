#!/usr/bin/env python3.12
"""Make a component's visual files name its manifest version, and render the examples again.

Usage: python3.12 -B tools/sync_visual_versions.py COMPONENT_DIR... [--check]
Sets `version` in visual/definition.json and in every example's definition of this package, rewrites
"<name> ... at version X" and "to <title> X" in the descriptions, and renders each example to its outputs with clod's diagram generator.
With --check nothing is written and the exit status is 1 when a file names another version.
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import scaffold_component  # noqa: E402


def sync(directory: Path, check: bool) -> list[str]:
    manifest = json.loads((directory / "component.json").read_text(encoding="utf-8"))
    name, version = manifest["identity"]["name"], manifest["identity"]["version"]
    visual = manifest["visual"]
    stale = []

    def rewrite(path: Path, update):
        text = path.read_text(encoding="utf-8")
        new = update(text)
        if new != text:
            stale.append(str(path.relative_to(directory)))
            if not check:
                path.write_text(new, encoding="utf-8")

    def fix_json(text):
        document = json.loads(text)

        def walk(node):
            if isinstance(node, dict):
                if node.get("package") == name and "version" in node:
                    node["version"] = version
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)
        walk(document)
        return json.dumps(document, indent=2, ensure_ascii=False) + "\n"

    rewrite(directory / visual["definition"], fix_json)
    for example in visual["examples"]:
        rewrite(directory / example, fix_json)
    short = name.rsplit("/", 1)[1]
    title = json.loads((directory / visual["definition"]).read_text(encoding="utf-8"))["name"]

    def fix_description(text):
        text = re.sub(rf"(\b{re.escape(short)}\b[^.\n]*? at version )\d+\.\d+\.\d+", rf"\g<1>{version}", text)
        return re.sub(rf"(\bto {re.escape(title)} )\d+\.\d+\.\d+", rf"\g<1>{version}", text)
    for description in visual["descriptions"]:
        rewrite(directory / description, fix_description)
    if not check and stale:
        for example in visual["examples"]:
            prefix = Path(example).stem
            outputs = [o for o in visual["outputs"] if Path(o).stem == prefix]
            if outputs:
                scaffold_component.render(directory / example, directory / Path(outputs[0]).parent)
                rendered = directory / Path(outputs[0]).parent
                if prefix != "connected":
                    for suffix in (".svg", ".png"):
                        made = rendered / f"connected{suffix}"
                        if made.exists():
                            made.replace(rendered / f"{prefix}{suffix}")
    return stale


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("components", nargs="+", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    failed = False
    for directory in args.components:
        stale = sync(directory.resolve(), args.check)
        if stale:
            failed = True
            print(f"{directory.name}: {'stale' if args.check else 'updated'} {', '.join(stale)}")
    return 1 if failed and args.check else 0


if __name__ == "__main__":
    sys.exit(main())
