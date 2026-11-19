import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import publish_catalogue as publisher  # noqa: E402


def answer(assets, code=0):
    return subprocess.CompletedProcess([], code, json.dumps({"assets": assets}), "")


class UnverifiedAssets(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        folder = Path(self.temporary.name)
        self.files, self.digests = {}, {}
        for suffix in publisher.REQUIRED_ASSETS:
            self.files[suffix] = folder / f"a.{suffix}"
            self.files[suffix].write_bytes(suffix.encode())
            self.digests[f"thing-1.0.0.{suffix}"] = "sha256:" + __import__("hashlib").sha256(suffix.encode()).hexdigest()

    def tearDown(self):
        self.temporary.cleanup()

    def check(self, view, files=None):
        with mock.patch.object(publisher, "gh", return_value=view):
            return publisher.unverified_assets("thing", "v1.0.0", "thing-1.0.0", self.files if files is None else files)

    def test_every_asset_matching_is_verified(self):
        self.assertEqual(self.check(answer([{"name": n, "digest": d} for n, d in self.digests.items()])), [])

    def test_a_different_or_missing_asset_keeps_the_store(self):
        assets = [{"name": n, "digest": d} for n, d in self.digests.items()]
        changed = [dict(item, digest="sha256:" + "0" * 64) if item["name"].endswith(".review.json") else item for item in assets]
        self.assertEqual(self.check(answer(changed)), ["thing v1.0.0: thing-1.0.0.review.json"])
        missing = [item for item in assets if not item["name"].endswith(".review.json")]
        self.assertEqual(self.check(answer(missing)), ["thing v1.0.0: thing-1.0.0.review.json"])

    def test_required_evidence_absent_from_the_publication_keeps_the_store(self):
        files = {key: value for key, value in self.files.items() if key != "guardian.dsse.json"}
        assets = [{"name": n, "digest": d} for n, d in self.digests.items()]
        self.assertEqual(self.check(answer(assets), files), ["thing v1.0.0: thing-1.0.0.guardian.dsse.json not among the published files"])

    def test_an_asset_without_a_digest_is_not_verified(self):
        self.assertEqual(len(self.check(answer([{"name": n} for n in self.digests]))), len(publisher.REQUIRED_ASSETS))

    def test_an_unreadable_release_is_not_verified(self):
        self.assertEqual(self.check(answer([], code=1)), ["thing v1.0.0: release not readable"])


if __name__ == "__main__":
    unittest.main()
