"""The library: the folders under components/, recipes/ and classes/ that library.json lists. The tools read, check,
regenerate, bundle and publish only these; any other folder there is not part of the library. CLOD_LIBRARY names
another list in the same format, which every tool then reads instead."""

import json
import os
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent.parent
LIST = Path(os.environ.get("CLOD_LIBRARY") or Path(__file__).resolve().parent / "library.json").resolve()
KINDS = ("components", "recipes", "classes")
FOLDER = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*")


class LibraryError(Exception):
    pass


def names(kind: str, path: Path = LIST) -> list[str]:
    """The folder names of one kind, as listed: unique, sorted, `<name>-class` for classes."""
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise LibraryError(f"{path}: cannot read ({error})") from error
    if type(document) is not dict or document.get("format") != 1 or set(document) != {"format", *KINDS}:
        raise LibraryError(f"{path}: expected {{format: 1, components, recipes, classes}}")
    listed = document[kind]
    if (type(listed) is not list or any(type(item) is not str or not FOLDER.fullmatch(item) for item in listed)
            or listed != sorted(set(listed)) or (kind == "classes" and any(not item.endswith("-class") for item in listed))):
        raise LibraryError(f"{path}: {kind} must be sorted, unique folder names" + (" ending in -class" if kind == "classes" else ""))
    return listed


def folders(kind: str, base: Path | None = None) -> list[Path]:
    """The listed folders of one kind under `base` (by default the workspace's components/, recipes/ or classes/); each
    must exist."""
    base = Path(base) if base is not None else ROOT / kind
    result = [base / name for name in names(kind)]
    missing = [str(path) for path in result if not path.is_dir()]
    if missing:
        raise LibraryError(f"listed in {LIST} but missing: " + ", ".join(missing))
    return result


def components(base: Path | None = None) -> list[Path]:
    return folders("components", base)


def recipes(base: Path | None = None) -> list[Path]:
    return folders("recipes", base)


def classes(base: Path | None = None) -> list[Path]:
    return folders("classes", base)
