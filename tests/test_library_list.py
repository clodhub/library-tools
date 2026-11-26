"""CLOD_LIBRARY names another library list, which every tool reads instead of tools/library.json."""
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import library


class LibraryListTests(unittest.TestCase):
    def tearDown(self):
        os.environ.pop("CLOD_LIBRARY", None)
        importlib.reload(library)

    def load(self, document):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        path = Path(temp.name) / "other.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        os.environ["CLOD_LIBRARY"] = str(path)
        importlib.reload(library)
        return path

    def test_the_default_list_is_tools_library_json(self):
        os.environ.pop("CLOD_LIBRARY", None)
        importlib.reload(library)
        self.assertEqual(library.LIST, (Path(__file__).resolve().parents[1] / "library.json").resolve())
        self.assertIn("theme", library.names("components"))

    def test_another_list_replaces_it(self):
        path = self.load({"format": 1, "components": ["fair-shuffle", "translations"], "recipes": [], "classes": ["fair-shuffle-class"]})
        self.assertEqual(library.LIST, path.resolve())
        self.assertEqual(library.names("components"), ["fair-shuffle", "translations"])
        self.assertEqual([p.name for p in library.components()], ["fair-shuffle", "translations"])
        self.assertEqual([p.name for p in library.classes()], ["fair-shuffle-class"])

    def test_a_missing_folder_names_the_list(self):
        path = self.load({"format": 1, "components": ["no-such-component"], "recipes": [], "classes": []})
        with self.assertRaisesRegex(library.LibraryError, str(path.resolve())):
            library.components()


if __name__ == "__main__":
    unittest.main()
