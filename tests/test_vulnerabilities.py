import io
import os
from pathlib import Path
import sys
import unittest
import urllib.error

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import vulnerabilities  # noqa: E402

KNOWN = {"pkg:npm/undici@7.29.1": [{"id": "GHSA-aaaa-bbbb-cccc"}], "pkg:maven/io.netty/netty-handler@4.1.93.Final": [{"id": "GHSA-dddd-eeee-ffff"}]}


class FakeOsv:
    """querybatch as OSV answers it: one result per query; a purl without a namespace where one is required is a 400 for the batch."""

    def __init__(self, known=KNOWN, pages=None, refuse=()):
        self.known, self.pages, self.refuse, self.calls = known, pages or {}, set(refuse), []

    def __call__(self, url, body):
        self.calls.append(len(body["queries"]))
        if len(body["queries"]) > vulnerabilities.BATCH:
            raise AssertionError("more queries than OSV accepts in one batch")
        results = []
        for query in body["queries"]:
            purl = query["package"]["purl"]
            if purl in self.refuse:
                raise urllib.error.HTTPError(url, 400, "namespace is required", {}, io.BytesIO(b"{}"))
            token = query.get("page_token")
            if purl in self.pages and token is None:
                results.append({"vulns": self.pages[purl][0], "next_page_token": "2"})
            elif purl in self.pages:
                results.append({"vulns": self.pages[purl][1]})
            else:
                results.append({"vulns": self.known.get(purl, [])} if purl in self.known else {})
        return {"results": results}


def manifest(purls, waivers=()):
    return {"upstream": [{"purl": purl} for purl in purls], "vulnerabilityWaivers": [{"id": key, "reason": "not reachable"} for key in waivers]}


class LiveOsv(unittest.TestCase):
    """A scoped npm package, its scope written both ways, against api.osv.dev."""

    @unittest.skipUnless(os.environ.get("OSV_LIVE"), "set OSV_LIVE=1 to query api.osv.dev")
    def test_osv_answers_an_npm_scope_in_either_form(self):
        for purl in ("pkg:npm/%40babel/traverse@7.22.0", "pkg:npm/@babel/traverse@7.22.0"):
            with self.subTest(purl=purl):
                self.assertEqual(vulnerabilities.advisories([purl]), {"GHSA-67hx-6x53-jw92": [purl]})
        self.assertEqual(vulnerabilities.advisories(["pkg:npm/%40babel/traverse@7.23.2", "pkg:npm/@babel/traverse@7.23.2"]), {})


class Vulnerabilities(unittest.TestCase):
    def test_advisories_map_to_the_purls_they_affect(self):
        found = vulnerabilities.advisories(["pkg:npm/zod@4.6.6", *KNOWN], FakeOsv())
        self.assertEqual(found, {"GHSA-aaaa-bbbb-cccc": ["pkg:npm/undici@7.29.1"], "GHSA-dddd-eeee-ffff": ["pkg:maven/io.netty/netty-handler@4.1.93.Final"]})

    def test_every_page_is_read(self):
        osv = FakeOsv(pages={"pkg:npm/a@1.0.0": ([{"id": "GHSA-1111-1111-1111"}], [{"id": "GHSA-2222-2222-2222"}])})
        self.assertEqual(sorted(vulnerabilities.advisories(["pkg:npm/a@1.0.0"], osv)), ["GHSA-1111-1111-1111", "GHSA-2222-2222-2222"])

    def test_batches_stay_within_the_limit(self):
        osv = FakeOsv(known={})
        vulnerabilities.advisories([f"pkg:npm/p{n}@1.0.0" for n in range(2500)], osv)
        self.assertEqual(osv.calls, [1000, 1000, 500])

    def test_a_purl_osv_refuses_makes_the_result_unknown(self):
        osv = FakeOsv(refuse={"pkg:swift/client-sdk-swift@2.19.0"})
        with self.assertRaises(vulnerabilities.Unqueryable) as raised:
            vulnerabilities.advisories(["pkg:npm/zod@4.6.6", "pkg:swift/client-sdk-swift@2.19.0", "pkg:npm/undici@7.29.1"], osv)
        self.assertEqual(raised.exception.purls, ["pkg:swift/client-sdk-swift@2.19.0"])

    def test_a_waiver_allows_only_the_advisory_it_names(self):
        found, refused, unused = vulnerabilities.check(manifest(KNOWN, ["GHSA-aaaa-bbbb-cccc"]), FakeOsv())
        self.assertEqual(list(refused), ["GHSA-dddd-eeee-ffff"])
        self.assertEqual(unused, [])

    def test_a_waiver_for_an_advisory_not_reported_is_listed(self):
        _, refused, unused = vulnerabilities.check(manifest(["pkg:npm/zod@4.6.6"], ["GHSA-9999-9999-9999"]), FakeOsv())
        self.assertEqual((refused, unused), ({}, ["GHSA-9999-9999-9999"]))

    def test_no_upstream_code_means_nothing_to_report(self):
        osv = FakeOsv()
        self.assertEqual(vulnerabilities.check(manifest([]), osv), ({}, {}, []))
        self.assertEqual(osv.calls, [])


if __name__ == "__main__":
    unittest.main()
