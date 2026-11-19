"""Entry-module, artefact-location and provider rules of tools/check_component.py and tools/sync_required_interfaces.py.

Run: PYTHONPATH=aepm python3.12 -B -m unittest discover -s tools/tests
"""
import json
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import check_component as C  # noqa: E402
import sync_required_interfaces as S  # noqa: E402


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


class EntryModuleTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.package = self.root / "deck"
        write(self.package / "implementation/web/index.ts", 'import "./deck.css";\nexport * from "./deck.ts";\nexport class Deck { static create() {} }\n')
        write(self.package / "implementation/web/deck.ts", "export function shuffle() {}\nexport type Card = string;\n")
        write(self.package / "implementation/web/deck.css", "body {}\n")
        write(self.package / "implementation/apple/Sources/Deck.swift",
              "public struct Deck {}\npublic func shuffle() {}\nfunc hidden() {}\nextension Deck { public static func make() {} }\n")
        write(self.package / "implementation/android/src/Deck.kt",
              "class Deck {\n    fun shuffle() {}\n    private fun hidden() {}\n    internal fun inner() {}\n}\n")
        self.manifest = {"kind": "component", "identity": {"name": "ourapps-ae/deck"}, "artefacts": [
            {"source": "implementation/" + path, "destination": "components/deck/" + path}
            for path in ("web/index.ts", "web/deck.ts", "web/deck.css", "apple/Sources/Deck.swift", "android/src/Deck.kt")]}

    def document(self, modules, methods):
        write(self.package / "interfaces/deck.json", json.dumps({
            "openrpc": "1.3.2", "info": {"title": "ae/deck", "version": "1.0.0"}, "x-ourapps-modules": modules,
            "methods": [{"name": name, "params": [], "result": {"name": "r", "schema": {}}, "tags": [{"name": tag} for tag in tags]}
                        for name, tags in methods]}))

    def errors(self):
        report = C.Report()
        C.check_entry_modules(self.package, self.manifest, ["interfaces/deck.json"], report)
        return report.errors

    def test_declared_entries_export_and_declare_their_methods(self):
        all_tags = ["typescript", "swift", "kotlin", "command"]
        self.document({"web": "web/index.ts", "apple": "apple/Sources", "android": "android/src"},
                      [("shuffle", all_tags), ("Deck.create", all_tags)])
        self.assertEqual(self.errors(), [])

    def test_missing_names_and_hidden_declarations(self):
        self.document({"web": "web/index.ts", "apple": "apple/Sources", "android": "android/src"},
                      [("hidden", ["swift", "kotlin"]), ("inner", ["kotlin"]), ("absent", ["typescript"])])
        self.assertEqual(self.errors(), [
            "interfaces/deck.json: method hidden tagged swift is not a public name in apple/Sources",
            "interfaces/deck.json: method hidden tagged kotlin is not a public name in android/src",
            "interfaces/deck.json: method inner tagged kotlin is not a public name in android/src",
            "interfaces/deck.json: web entry module web/index.ts does not export absent (method tagged typescript)"])

    def test_entry_paths_must_be_installed(self):
        self.document({"web": "web/main.ts", "apple": "apple/Missing", "phone": "x"}, [])
        self.assertEqual(self.errors(), [
            "interfaces/deck.json: web entry module web/main.ts is not an installed file of the package (components/deck/web/main.ts)",
            "interfaces/deck.json: apple folder apple/Missing has no installed file (components/deck/apple/Missing/)",
            "interfaces/deck.json: x-ourapps-modules platform 'phone' is not one of ['android', 'apple', 'desktop', 'server', 'web']"])
        write(self.package / "interfaces/deck.json", json.dumps({"openrpc": "1.3.2"}))
        self.assertEqual(self.errors(), ["interfaces/deck.json: x-ourapps-modules must be an object mapping platform to path"])

    def test_recipe_entries_are_relative_to_the_application_root(self):
        self.manifest = {"kind": "recipe", "identity": {"name": "ourapps-ae/game"},
                         "artefacts": [{"source": "implementation/web/index.ts", "destination": "web/index.ts"},
                                       {"source": "implementation/web/deck.ts", "destination": "web/deck.ts"}]}
        self.document({"server": "web/index.ts"}, [("shuffle", ["typescript"])])
        self.assertEqual(self.errors(), [])

    def test_required_copies_are_not_checked_against_the_consumer(self):
        self.document({"web": "web/elsewhere.ts", "apple": "apple/Missing"}, [("absent", ["typescript", "swift"])])
        report = C.Report()
        C.check_entry_modules(self.package, self.manifest, ["interfaces/deck.json"], report, set())
        self.assertEqual(report.errors, [])
        write(self.package / "interfaces/deck.json", json.dumps({"openrpc": "1.3.2"}))
        C.check_entry_modules(self.package, self.manifest, ["interfaces/deck.json"], report, set())
        self.assertEqual(report.errors, ["interfaces/deck.json: x-ourapps-modules must be an object mapping platform to path"])

    def test_json_schema_documents_may_carry_modules(self):
        import shutil
        import subprocess
        path = self.root / "settings.schema.json"
        path.write_text(json.dumps({"$schema": "https://json-schema.org/draft/2020-12/schema", "title": "ae/deck-configuration",
                                    "x-version": "1.0.0", "x-ourapps-modules": {}, "type": "object"}))
        result = subprocess.run([shutil.which("node"), str(C.VALIDATOR), str(path)], capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_port_ids(self):
        manifest = {"provides": [{"id": "game-adapter", "type": "ae/game-adapter"}],
                    "requires": [{"id": "wrapped-game-adapter", "type": "ae/game-adapter"}, {"id": "seating", "type": "ae/seating"}]}
        self.assertEqual([C.expected_port_id(manifest, key, port) for key in ("provides", "requires") for port in manifest[key]],
                         ["game-adapter", "wrapped-game-adapter", "seating"])
        self.assertEqual(C.expected_port_id({"provides": []}, "requires", {"id": "x", "type": "ae/game-adapter"}), "game-adapter")

    def test_artefact_locations(self):
        report = C.Report()
        self.manifest["artefacts"].append({"source": "x", "destination": "components/other/x"})
        C.check_artefact_locations(self.manifest, report)
        self.assertEqual(report.errors, ["artefact x installs at components/other/x, outside components/deck/"])

    def test_shared_class_is_independent_of_provider_documentation(self):
        from aepm.model import canonical
        components = self.root / "components"
        descriptor = {"name": "ourapps-ae/deck-class", "version": "1.0.0", "provides": [
            {"type": "ae/deck", "version": "1.0.0", "path": "classes/deck/interface.json"}],
            "inventory": [{"path": "classes/deck/interface.json", "digest": hashlib.sha256(b"{}").hexdigest()}]}
        entry = {"reference": {"name": descriptor["name"], "version": descriptor["version"],
                                "digest": hashlib.sha256(canonical(descriptor)).hexdigest()}, "descriptor": descriptor}
        for name, text in (("a", "{}"), ("b", '{"description": "Provider-specific documentation"}')):
            write(components / name / "interfaces/deck.json", text)
            write(components / name / "classes/deck/interface.json", "{}")
            write(components / name / "component.json", json.dumps({"provides": [
                {"id": "deck", "type": "ae/deck", "version": "1.0.0", "path": "interfaces/deck.json"}], "requires": [], "classes": [entry]}))
        self.assertEqual(S.class_conflicts(components), [])
        write(components / "user/component.json", json.dumps({"provides": [], "classes": [], "requires": [
             {"id": "deck", "type": "ae/deck", "version": "1.0.0", "path": "interfaces/required-deck.json"}]}))
        self.assertEqual(S.required_copies(components, "user"), {"interfaces/required-deck.json": b"{}"})
        (components / "a/classes/deck/interface.json").write_text('{"tampered": true}')
        self.assertEqual(S.class_conflicts(components), ["a: class inventory digest differs for classes/deck/interface.json"])
        manifest = json.loads((components / "b/component.json").read_text())
        manifest["classes"][0]["descriptor"]["provides"] = []
        manifest["classes"][0]["reference"]["digest"] = hashlib.sha256(canonical(manifest["classes"][0]["descriptor"])).hexdigest()
        (components / "b/component.json").write_text(json.dumps(manifest))
        self.assertIn("conflicting class ourapps-ae/deck-class 1.0.0: a, b", S.class_conflicts(components))


if __name__ == "__main__":
    unittest.main()
