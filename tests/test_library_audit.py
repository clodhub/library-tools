"""Release monitoring distinguishes current releases, updates and failed queries."""
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import audit_library as audit


class LibraryAuditTests(unittest.TestCase):
    def test_github_release_source_is_explicit(self):
        self.assertEqual(audit.release_endpoint("https://github.com/example/library/releases"),
                         "https://api.github.com/repos/example/library/releases/latest")
        self.assertIsNone(audit.release_endpoint("https://not-github.example/library"))
        self.assertIsNone(audit.release_endpoint("file:///tmp/release"))

    def test_versions_are_compared_numerically(self):
        self.assertEqual(audit.compare_release("1.9.0", "v1.10.0"), "update-available")
        self.assertEqual(audit.compare_release("1.9.0", "v1.9.0"), "current")
        self.assertEqual(audit.compare_release("1.10.0", "v1.9.0"), "pinned-ahead")
        self.assertEqual(audit.compare_release("snapshot-abc", "v1.9.0"), "different-release")

    def test_connection_failure_is_not_reported_as_current(self):
        def unavailable(_):
            raise OSError("test connection failure")
        result = audit.check_release("example", "1.0.0", "https://github.com/example/library", unavailable)
        self.assertEqual(result["status"], "unavailable")

    def test_scoped_npm_source_uses_exact_package_name(self):
        seen = []
        def reader(address):
            seen.append(address)
            return {"version": "2.0.0"}
        result = audit.upstream_update({"purl": "pkg:npm/%40example/library@1.0.0", "name": "@example/library", "version": "1.0.0"}, reader)
        self.assertEqual(seen, ["https://registry.npmjs.org/@example/library/latest"])
        self.assertEqual(result["status"], "update-available")


if __name__ == "__main__":
    unittest.main()
