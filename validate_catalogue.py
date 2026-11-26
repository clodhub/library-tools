#!/usr/bin/env python3.12
"""Pack the library (tools/library.json) and validate its archive-derived catalogue publications without publishing.

Usage: PYTHONPATH=clod python3.12 -B tools/validate_catalogue.py --catalogue CATALOGUE_CHECKOUT
"""
import argparse
import json
from pathlib import Path
import subprocess
import tempfile

import publish_catalogue as publisher


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--catalogue", required=True, type=Path)
    args = parser.parse_args()
    _, bundle, _, _, _ = publisher.load_clod()
    with tempfile.TemporaryDirectory(prefix="catalogue-validation-", dir=publisher.TEMP) as temp:
        store = Path(temp).resolve() / "store"
        components = []
        for directory in publisher.library.components():
            ref = bundle.pack(directory, store)
            asset = f"{directory.name}-{ref['version']}.tar"
            components.append(publisher.component_entry(directory.name, directory, ref, asset, store)[0])
        recipes = [publisher.recipe_entry(bundle.pack(directory, store), store)[0] for directory in publisher.library.recipes()]
        body = {"components": components, "recipes": recipes}
        command = ["node", str(publisher.ROOT / "tools/standards/validate-publications.mjs"), str(args.catalogue.resolve()), "-"]
        return subprocess.run(command, input=json.dumps(body), text=True, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
