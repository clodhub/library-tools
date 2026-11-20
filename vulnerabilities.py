"""Known vulnerabilities in a package's upstream code, from OSV (https://osv.dev).

Every upstream record of a package manifest carries the exact purl of what the package bundles or downloads. Those purls are
queried with OSV's batch API; an advisory that affects one of them refuses publication unless the manifest's
`vulnerabilityWaivers` names it with a reason.

    python3.12 -B tools/vulnerabilities.py components/NAME [...]     (prints the advisories; exit 1 when any is not waived)
"""
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

OSV = "https://api.osv.dev/v1"
BATCH = 1000  # OSV's limit of queries per batch request


def post(url, body, timeout=60):
    request = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"content-type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


def get(url, timeout=60):
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read())


def purls(manifest):
    """The exact purls of a manifest's upstream records, in order, each once."""
    return list(dict.fromkeys(item["purl"] for item in manifest["upstream"]))


class Unqueryable(Exception):
    """OSV refuses these purls, so their vulnerabilities are unknown."""

    def __init__(self, purls):
        self.purls = purls
        super().__init__("OSV refuses " + ", ".join(purls))


def refused_purls(purl_list, send=post):
    """The purls OSV refuses as queries (for example a Swift purl without its repository namespace), found by halving."""
    try:
        send(f"{OSV}/querybatch", {"queries": [{"package": {"purl": purl}} for purl in purl_list]})
        return []
    except urllib.error.HTTPError as error:
        if error.code != 400:
            raise
    if len(purl_list) == 1:
        return list(purl_list)
    middle = len(purl_list) // 2
    return refused_purls(purl_list[:middle], send) + refused_purls(purl_list[middle:], send)


def advisories(purl_list, send=post):
    """advisory id -> sorted purls it affects. Every page of every query is read. A purl OSV refuses raises Unqueryable
    (its vulnerabilities would be unknown); any other failed request raises."""
    try:
        return _advisories(purl_list, send)
    except urllib.error.HTTPError as error:
        if error.code != 400:
            raise
        refused = [purl for start in range(0, len(purl_list), BATCH) for purl in refused_purls(purl_list[start:start + BATCH], send)]
        if not refused:
            raise
        raise Unqueryable(refused) from None


def _advisories(purl_list, send):
    found = {}
    pending = [(purl, None) for purl in purl_list]
    while pending:
        batch, pending = pending[:BATCH], pending[BATCH:]
        queries = [{"package": {"purl": purl}, **({"page_token": token} if token else {})} for purl, token in batch]
        results = send(f"{OSV}/querybatch", {"queries": queries})["results"]
        if len(results) != len(batch):
            raise ValueError(f"OSV answered {len(results)} results for {len(batch)} queries")
        for (purl, _), result in zip(batch, results):
            for vuln in result.get("vulns", []):
                found.setdefault(vuln["id"], set()).add(purl)
            if result.get("next_page_token"):
                pending.append((purl, result["next_page_token"]))
    return {key: sorted(value) for key, value in sorted(found.items())}


def unwaived(found, manifest):
    """The advisories in found that the manifest does not waive, and the waivers that name no advisory found. A waiver names
    the id OSV reports (for npm and Maven usually the GHSA id)."""
    waived = {item["id"] for item in manifest["vulnerabilityWaivers"]}
    return {key: value for key, value in found.items() if key not in waived}, sorted(waived - set(found))


def summary(advisory_id, fetch=get):
    """One line for an advisory: id, its aliases, severity and summary."""
    detail = fetch(f"{OSV}/vulns/{advisory_id}")
    severity = ", ".join(f"{item.get('type')} {item.get('score')}" for item in detail.get("severity", [])) or \
        detail.get("database_specific", {}).get("severity") or "severity not stated"
    aliases = ", ".join(detail.get("aliases", []))
    return f"{advisory_id}{' (' + aliases + ')' if aliases else ''}: {severity}; {detail.get('summary') or detail.get('details', '')[:160]}"


def check(manifest, send=post):
    """(found, refused, unused waivers) for a manifest."""
    found = advisories(purls(manifest), send)
    refused, unused = unwaived(found, manifest)
    return found, refused, unused


def main(argv):
    status = 0
    for directory in argv:
        manifest = json.loads((Path(directory) / "component.json").read_text())
        try:
            found, refused, unused = check(manifest)
        except Unqueryable as error:
            print(f"{manifest['identity']['name']}: vulnerabilities unknown, {error}")
            status = 1
            continue
        name = manifest["identity"]["name"]
        print(f"{name} {manifest['identity']['version']}: {len(purls(manifest))} upstream purls, {len(found)} advisories, {len(refused)} not waived")
        for key, affected in refused.items():
            print(f"  {summary(key)}\n    affects {', '.join(affected[:6])}{' and more' if len(affected) > 6 else ''}")
        for key in unused:
            print(f"  waiver {key} names no advisory found")
        status |= bool(refused)
    return status


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
