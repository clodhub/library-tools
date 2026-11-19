"""Component authoring preserves the licence documents supplied by the author."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import check_component as checker
import scaffold_component as scaffold


class LicencePreservationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve() / "example"
        self.root.mkdir()
        self.licence = "Permission is hereby granted, free of charge, to any person obtaining a copy.\n"
        self.notice = "Copyright Example Authors\n"
        (self.root / "LICENCE").write_text(self.licence)
        (self.root / "NOTICE").write_text(self.notice)
        self.spec = {
            "name": "example", "version": "1.0.0", "description": "Example library",
            "facilities": [{"id": "example", "description": "Example library"}],
            "suites": [{"id": "test", "argv": ["node", "--test", "tests/example.mjs"]}],
            "licence": "MIT", "licenceFile": "LICENCE", "noticeFile": "NOTICE",
        }
        example = json.loads((Path(__file__).resolve().parents[2] / "aepm/examples/counter/component.json").read_text())
        self.spec.update(supportedPlatforms=example["platforms"], templates=[])
        for field in ("toolchains", "classes", "implements", "upstream", "compatibility", "maintenance"):
            self.spec[field] = example[field]

    def load(self):
        path = self.root / "spec.json"
        path.write_text(json.dumps(self.spec))
        return scaffold.load_spec(path, self.root)

    def test_licence_and_notices_belong_to_the_package_author(self):
        spec = self.load()
        manifest, _ = scaffold.build_manifest(spec, {"LICENCE": False, "NOTICE": False}, [])
        self.assertEqual(manifest["identity"]["licence"], "MIT")
        self.assertEqual(manifest["documents"]["licenceNotice"], "LICENCE")
        self.assertEqual((self.root / "LICENCE").read_text(), self.licence)
        self.assertEqual((self.root / "NOTICE").read_text(), self.notice)

    def test_checker_does_not_replace_the_authors_licence(self):
        for name in ("INSTRUCTIONS.md", "SOURCE.md", "CONTRACT.md", "tools.lock.json"):
            (self.root / name).write_text("Package documentation\n")
        manifest = {"identity": {"licence": "MIT"}, "documents": {"licenceNotice": "LICENCE"},
                    "files": [{"path": path.name} for path in self.root.iterdir()]}
        report = checker.Report()
        checker.check_documents(self.root, manifest, report)
        self.assertEqual(report.errors, [])

    def test_licence_paths_cannot_escape_the_package(self):
        self.spec["licenceFile"] = "../outside"
        with self.assertRaises(scaffold.ScaffoldError):
            self.load()

    def test_licence_and_notice_documents_are_required(self):
        for field in ("licence", "licenceFile", "noticeFile"):
            with self.subTest(field=field):
                value = self.spec.pop(field)
                with self.assertRaises(scaffold.ScaffoldError):
                    self.load()
                self.spec[field] = value


if __name__ == "__main__":
    unittest.main()
