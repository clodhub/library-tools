"""Components take their provided documents and schemas from the listed class they implement; the secrets scan skips binary
files but still names secret-bearing files."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import library  # noqa: E402
from check_component import Report, check_secrets  # noqa: E402
from sync_required_interfaces import class_documents, implemented_copies  # noqa: E402

COMPONENTS = library.ROOT / "components"
CLASSES = library.ROOT / "classes"


class ClassDocumentTests(unittest.TestCase):
    def test_a_component_takes_its_provided_document_from_its_class(self):
        copies = implemented_copies([COMPONENTS / "card-deck"], "card-deck", library.classes())
        self.assertEqual(copies, {"interfaces/card-deck.json": (CLASSES / "card-deck-class/card-deck.json").read_bytes()})

    def test_the_library_holds_its_classes_documents(self):
        for relative, data in implemented_copies(library.components(), class_folders=library.classes()).items():
            name, path = relative.split("/", 1)
            self.assertEqual((COMPONENTS / name / path).read_bytes(), data, relative)

    def test_a_class_schema_goes_into_interfaces(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary) / "staff-access-class"
            folder.mkdir()
            (folder / "class.json").write_text(json.dumps({"name": "pkg:clod/com.clodhub/staff-access-class", "provides": [
                {"type": "com.clodhub/staff-access", "version": "1.0.0", "path": "classes/staff-access-class/staff-access.json"}]}))
            (folder / "staff-access.json").write_text("{}\n")
            (folder / "staff-attempt.schema.json").write_text('{"type": "object"}\n')
            component = {"provides": [{"id": "staff-access", "type": "com.clodhub/staff-access", "version": "1.0.0"}],
                         "requires": [{"id": "random-source", "type": "com.clodhub/random-source", "version": "1.0.0"}],
                         "implements": [{"name": "pkg:clod/com.clodhub/staff-access-class"}]}
            copies = class_documents(component, [folder, CLASSES / "fair-shuffle-class"])
            self.assertEqual(sorted(copies), ["interfaces/required-random-source.json", "interfaces/staff-access.json",
                                              "interfaces/staff-attempt.schema.json"])
            self.assertEqual(copies["interfaces/staff-attempt.schema.json"], b'{"type": "object"}\n')


class SecretScanTests(unittest.TestCase):
    def test_binary_files_are_not_read_as_text_and_key_files_are_named(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "font.ttf").write_bytes(b"\0\1\0\0" + b"AKIA" + b"A" * 16)
            (directory / "notes.txt").write_text("AKIA" + "A" * 16)
            (directory / "release.p12").write_bytes(b"\0\1")
            report = Report()
            check_secrets(directory, ["font.ttf", "notes.txt", "release.p12"], report)
            self.assertEqual(report.errors, ["secret pattern (AWS access key) in notes.txt", "secret-bearing file name: release.p12"])


if __name__ == "__main__":
    unittest.main()
