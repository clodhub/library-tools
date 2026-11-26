"""Required interface documents come from the listed classes, also when no listed component packages the class."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import library  # noqa: E402
from sync_required_interfaces import required_copies  # noqa: E402

COMPONENTS = library.ROOT / "components"
CLASSES = library.ROOT / "classes"


class RequiredCopiesTests(unittest.TestCase):
    def test_a_listed_class_is_the_source_without_its_component(self):
        copies = required_copies([COMPONENTS / "card-deck"], "card-deck", [CLASSES / "fair-shuffle-class"])
        self.assertEqual(copies, {"interfaces/required-random-source.json": (CLASSES / "fair-shuffle-class/random-source.json").read_bytes()})

    def test_nothing_is_required_without_a_source(self):
        self.assertEqual(required_copies([COMPONENTS / "card-deck"], "card-deck"), {})

    def test_the_library_gets_the_same_copies_either_way(self):
        components, classes = library.components(), library.classes()
        self.assertEqual(required_copies(components, class_folders=classes), required_copies(components))


if __name__ == "__main__":
    unittest.main()
