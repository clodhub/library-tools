"""A new component's first scaffold computes the fields classes/generate.py keeps and writes the documents its classes give it:
card-deck, rebuilt from its spec without them, comes out byte for byte as published."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import library  # noqa: E402
import scaffold_component  # noqa: E402

SOURCE = library.ROOT / "components" / "card-deck"


class FirstScaffoldTests(unittest.TestCase):
    def test_a_spec_without_generated_fields_gives_the_same_package(self):
        tracked = [path for path in subprocess.check_output(["git", "-C", str(SOURCE), "ls-files", "-z"], text=True).split("\0") if path]
        generated = [path for path in tracked if path in ("component.json", "PROVENANCE.md", "interfaces/card-deck.json",
                                                          "interfaces/required-random-source.json") or path.startswith("classes/")]
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "card-deck"
            for relative in set(tracked) - set(generated):
                (target / relative).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(SOURCE / relative, target / relative)
            subprocess.run(["git", "init", "-q", str(target)], check=True)
            subprocess.run(["git", "-C", str(target), "remote", "add", "origin", "https://github.com/clodhub/card-deck"], check=True)
            spec_path = target / "authoring/spec.json"
            spec = json.loads(spec_path.read_text())
            for field in scaffold_component.GENERATED:
                del spec[field]
            spec["implements"] = [{"name": item["name"]} for item in spec["implements"]]
            spec_path.write_text(json.dumps(spec, indent=2) + "\n")
            _, notes, _ = scaffold_component.scaffold(target, spec_path, False, False)
            self.assertTrue(any(note.startswith("new component:") for note in notes), notes)
            for relative in generated:
                self.assertEqual((target / relative).read_bytes(), (SOURCE / relative).read_bytes(), relative)

    def test_a_partial_spec_is_refused(self):
        spec = scaffold_component.load_spec(SOURCE / "authoring/spec.json", SOURCE)
        del spec["toolchains"]
        with self.assertRaisesRegex(scaffold_component.ScaffoldError, "missing spec.toolchains"):
            scaffold_component.build_manifest(spec, {}, [])


if __name__ == "__main__":
    unittest.main()
