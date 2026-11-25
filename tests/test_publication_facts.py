"""Catalogue publication facts are bound to the validated package archive."""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import publish_catalogue as publisher


class PublicationFactsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(dir=publisher.TEMP)
        cls.store = Path(cls.temp.name).resolve() / "store"
        cls.directory = publisher.ROOT / "components" / "theme"
        _, bundle, _, _, _ = publisher.load_aepm()
        cls.ref = bundle.pack(cls.directory, cls.store)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_publication_uses_archive_identity_and_foundations(self):
        entry, version, _ = publisher.component_entry("theme", self.directory, self.ref, "theme.tar", self.store)
        manifest, _ = publisher.verified_package(self.ref, self.store)
        self.assertEqual(entry["name"], manifest["identity"]["name"])
        self.assertEqual(version, manifest["identity"]["version"])
        self.assertEqual(entry["digest"], "sha256:" + self.ref["digest"])
        self.assertEqual(entry["bundle"]["digest"], entry["digest"])
        self.assertEqual(entry["foundations"]["licence"], manifest["identity"]["licence"])
        for field in ("platforms", "classes", "implements", "upstream", "compatibility", "maintenance", "files", "artefacts"):
            self.assertEqual(entry["foundations"][field], manifest[field])
        self.assertEqual(set(entry["targets"]["client"]), set(manifest["platforms"]) - {"server"})

    def test_outer_identity_and_digest_cannot_substitute_another_archive(self):
        for field, value in (("name", "ourapps-ae/not-theme"), ("digest", "0" * 64)):
            ref = {**self.ref, field: value}
            with self.subTest(field=field), self.assertRaises(Exception):
                publisher.verified_package(ref, self.store)
        with self.assertRaisesRegex(ValueError, "identity"):
            publisher.component_entry("not-theme", self.directory, self.ref, "theme.tar", self.store)

    def test_archive_bytes_are_checked(self):
        path = self.store / "objects" / (self.ref["digest"] + ".tar")
        data = path.read_bytes()
        damaged = bytearray(data)
        damaged[len(damaged) // 2] ^= 1
        try:
            path.write_bytes(damaged)
            with self.assertRaises(Exception):
                publisher.verified_package(self.ref, self.store)
        finally:
            path.write_bytes(data)

    def test_defaults_use_key_presence_and_optional_ports_are_preserved(self):
        manifest = {"collect": [
            {"id": str(i), "type": "boolean", "grammar": "boolean", "required": False, "secret": False, **value}
            for i, value in enumerate(({}, {"default": False}, {"default": 0}, {"default": ""}))]}
        settings = publisher.settings_of(manifest)
        self.assertEqual([setting["hasDefault"] for setting in settings], [False, True, True, True])
        self.assertEqual([setting.get("default", "absent") for setting in settings], ["absent", False, 0, ""])
        ports = [{"id": "example", "type": "ae/example", "version": "1.0.0", "optional": True, "path": "x.json"}]
        payloads = {"x.json": b'{"openrpc": "1.3.2", "info": {"title": "ae/example", "version": "1.0.0", "description": " An example. "}}'}
        self.assertTrue(publisher.ports(ports, payloads, True)[0]["optional"])
        self.assertNotIn("optional", publisher.ports(ports, payloads)[0])
        self.assertEqual(publisher.ports(ports, payloads)[0]["description"], "An example.")

    def test_the_page_comes_from_the_contract_without_the_facilities_line_or_generated_setting_list(self):
        contract = (
            "# Example\n\n## Purpose and facilities\nWhat it is.\n\nFacilities: `x` (y).\n\n## User inputs\nWhat `decks` means.\n\n"
            "- `decks` (integer; optional; default `1`)\n- `decks`: explained by the author\n\n## Artefacts and dependencies\nFiles.\n\n"
            "## Integration\nCall it.\n\n## Scenarios and presentation\nA table.\n\n## Updates and removal\nPatches replace files.\n")
        manifest = {"documents": {"contract": "CONTRACT.md"}, "collect": [{"id": "decks"}]}
        self.assertEqual(publisher.page_description(manifest, {"CONTRACT.md": contract.encode()}),
                         "What it is.\n\n## Examples\n\nA table.\n\n## Settings\n\nWhat `decks` means.\n\n- `decks`: explained by the author\n\n"
                         "## For developers: how an application uses it\n\nCall it.\n\n## Updates\n\nPatches replace files.")


if __name__ == "__main__":
    unittest.main()
