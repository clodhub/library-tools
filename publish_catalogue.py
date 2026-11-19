#!/usr/bin/env python3.12
"""Pack every component and the recipe, sign and review each, release it on GitHub, and publish it to ourapps.app.

usage: PYTHONPATH=aepm python3.12 -B tools/publish_catalogue.py --key oa_... --release-key PEM --guardian-key PEM
                                                               [--only name ...] [--dry-run]

All packages are packed into one temporary store, so each review finds its dependencies. For each package:
the Level N release attestation (`--release-key`); `aepm review --trusted --allow-network` under a policy that pins
the Level N keys and trusts the package and its dependencies; publishing stops unless required-local passed; the
guardian verification summary (`--guardian-key`); the CycloneDX 1.6 bill of materials. A GitHub release `v<version>`
on ourapps-ae/<repo> (gh, keyring login) carries `<name>-<version>.tar`, `.cdx.json`, `.release.dsse.json`,
`.guardian.dsse.json`, `.review.json` and, when the review report names retained documents (execution evidence, contexts,
authorizations), `.evidence.tar` holding them at their store-relative paths; an existing release gets the assets it lacks, after its archive is checked
to be the same bytes. Then POST to /api/dev/components or /api/dev/recipes with the API key.
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMP = Path("/private/var/folders/yk/8tzc9b_1713dlhdp5vzkgg100000gn/T/opencode")
PY = "/Users/george/.pyenv/versions/3.12.8/bin/python3.12"
ORIGIN = "https://ourapps.app"
ORG = "ourapps-ae"

GROUPS = {
    "delivery": ["web-host", "apple-host", "android-host", "windows-desktop", "server-host", "ourapps-release", "store-update"],
    "rooms": ["realtime-rooms", "room-directory", "guest-identity", "presence", "invitations", "private-views", "display-mode", "video-calls", "preferences"],
    "game-framework": ["turn-engine", "game-clock", "seating", "fair-shuffle", "card-deck", "computer-players", "chip-ledger", "level-schedule", "tournament", "round-history", "rules-settings"],
    "poker": ["poker-hand-ranking", "poker-odds", "holdem-rules", "poker-strategy"],
    "presentation": ["theme", "theme-high-contrast", "controls", "table-layout", "card-graphics", "chip-graphics", "sound-cues", "guided-tour", "translations"],
}


def group_of(name):
    for group, names in GROUPS.items():
        if name in names:
            return group
    return "other"


def run(argv, **kw):
    return subprocess.run(argv, check=True, capture_output=True, text=True, **kw)


def load_aepm():
    sys.path.insert(0, str(ROOT / "aepm"))
    from aepm import bom, bundle, model, trust, verification
    return bom, bundle, model, trust, verification


def public_key(private_key):
    return subprocess.run(["node", "-e", "const c=require('node:crypto');process.stdout.write(c.createPublicKey("
                           "require('fs').readFileSync(process.argv[1])).export({type:'spki',format:'pem'}))", str(private_key)],
                          capture_output=True, text=True, check=True).stdout


def pack_all(directories):
    """Pack every package into one store; return the store and name -> ref."""
    _, bundle, _, _, _ = load_aepm()
    store = Path(tempfile.mkdtemp(prefix="publish-", dir=TEMP)).resolve() / "store"
    return store, {name: bundle.pack(directory, store) for name, directory in directories.items()}


def sign_and_review(store, ref, release_key, guardian_key, work):
    """Release attestation, trusted review, guardian summary and bill of materials for one stored archive."""
    bom, bundle, model, trust, verification = load_aepm()
    binding = trust.review_bindings(ref, store)
    for item in binding["packages"]:
        dependency = {key: item[key] for key in ("name", "version", "digest")}
        if dependency != ref and not (store / "statements" / item["digest"] / "release.dsse.json").exists():
            raise SystemExit(f"{ref['name']}: dependency {item['name']} {item['version']} is not packed in this run")
    owner = work / "review-owner.pem"
    if not owner.exists():
        subprocess.run(["node", "-e", "const c=require('node:crypto');require('fs').writeFileSync(process.argv[1],"
                        "c.generateKeyPairSync('ed25519').privateKey.export({type:'pkcs8',format:'pem'}),{mode:0o600})", str(owner)], check=True)
    policy = work / f"policy-{ref['digest']}.json"
    policy.write_bytes(model.canonical({"format": 1, "keys": {"release": [{"publicKey": public_key(release_key)}],
                                                               "guardian": [{"publicKey": public_key(guardian_key)}],
                                                               "owner": [{"publicKey": public_key(owner)}]},
                                        "trustedExecution": sorted(item["digest"] for item in binding["packages"])}))
    report = verification.review_package(ref, store, policy, True, allow_network=True)
    if report["summary"]["required-local"] != "passed":
        failed = [(s["id"], s["status"], s["reason"]) for s in report["suites"] if s["scope"] == "required-local" and s["status"] != "passed"]
        raise SystemExit(f"{ref['name']} {ref['version']}: required-local did not pass, not published: {failed}")
    trust.publish_review(store, ref, report, guardian_key, policy=policy)
    trust.verify_package(ref, store, policy)
    report_digest = model.digest(model.canonical(report))
    bom_path = work / f"{ref['digest']}.cdx.json"
    bom_path.write_bytes(model.canonical(bom.bill_of_materials(ref, store)))
    statements = store / "statements" / ref["digest"]
    files = {"tar": store / "objects" / (ref["digest"] + ".tar"), "cdx.json": bom_path,
             "release.dsse.json": statements / "release.dsse.json", "guardian.dsse.json": statements / "guardian.dsse.json",
             "review.json": store / "reports" / (report_digest + ".json")}
    from aepm.evidence import pack_evidence
    evidence = pack_evidence(store, report)  # refuses a report that names a document missing from the store
    if evidence is not None:
        files["evidence.tar"] = work / f"{ref['digest']}.evidence.tar"
        files["evidence.tar"].write_bytes(evidence)
    return files, report["summary"]


def gh(*argv):
    env = {k: v for k, v in os.environ.items() if k != "GITHUB_TOKEN"}
    return subprocess.run(["gh", *argv], capture_output=True, text=True, env=env)


def release(repo, tag, base, files, digest, title, work):
    """files: suffix -> path. Create the release, or add the assets an existing release of the same archive lacks."""
    staged = {}
    for suffix, source in files.items():
        target = work / "assets" / repo / f"{base}.{suffix}"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        staged[target.name] = target
    view = gh("release", "view", tag, "--repo", f"{ORG}/{repo}", "--json", "assets")
    if view.returncode != 0:
        done = gh("release", "create", tag, *map(str, staged.values()), "--repo", f"{ORG}/{repo}", "--target", "main", "--title", title,
                  "--notes", f"{title}. `{base}.tar` is the aepm package (its sha256 is {digest}); `.cdx.json` its CycloneDX 1.6 bill of "
                             "materials; `.release.dsse.json` and `.guardian.dsse.json` the Level N release and guardian attestations "
                             "(DSSE, in-toto); `.review.json` the review report the guardian attestation names; `.evidence.tar`, when present, "
                             "the documents that report names, at their store-relative paths.")
        if done.returncode != 0:
            raise SystemExit(f"release failed for {repo}: {done.stderr}")
        return "created"
    present = {asset["name"] for asset in json.loads(view.stdout)["assets"]}
    archive = f"{base}.tar"
    if archive in present:
        check = work / "existing" / repo
        check.mkdir(parents=True, exist_ok=True)
        got = gh("release", "download", tag, "--repo", f"{ORG}/{repo}", "--pattern", archive, "--dir", str(check), "--clobber")
        if got.returncode != 0 or hashlib.sha256((check / archive).read_bytes()).hexdigest() != digest:
            raise SystemExit(f"{repo} {tag}: the released archive differs from this pack; publish a new version")
    missing = [name for name in staged if name not in present]
    if not missing:
        return "exists"
    # The guardian attestation, the review report it names and that report's evidence are one set: a fresh review gives new
    # bytes for all three, so when any is missing all three are replaced together.
    review_set = {f"{base}.{suffix}" for suffix in ("guardian.dsse.json", "review.json", "evidence.tar")} & set(staged)
    upload = sorted(set(missing) | (review_set if review_set & set(missing) else set()))
    done = gh("release", "upload", tag, *[str(staged[name]) for name in upload], "--repo", f"{ORG}/{repo}", "--clobber")
    if done.returncode != 0:
        raise SystemExit(f"asset upload failed for {repo}: {done.stderr}")
    return f"uploaded {len(upload)} assets"


REQUIRED_ASSETS = ("tar", "cdx.json", "release.dsse.json", "guardian.dsse.json", "review.json")


def unverified_assets(repo, tag, base, files):
    """The release assets of files whose sha256 on GitHub is missing or differs from the local bytes, and every required
    asset (the archive, bill of materials, both attestations and the review report) this publication lacks."""
    wrong = [f"{repo} {tag}: {base}.{suffix} not among the published files" for suffix in REQUIRED_ASSETS if suffix not in files]
    view = gh("release", "view", tag, "--repo", f"{ORG}/{repo}", "--json", "assets")
    if view.returncode != 0:
        return wrong + [f"{repo} {tag}: release not readable"]
    remote = {asset["name"]: asset.get("digest") for asset in json.loads(view.stdout)["assets"]}
    for suffix, source in files.items():
        name = f"{base}.{suffix}"
        if remote.get(name) != "sha256:" + hashlib.sha256(Path(source).read_bytes()).hexdigest():
            wrong.append(f"{repo} {tag}: {name}")
    return wrong


def post(path, body, key):
    request = urllib.request.Request(ORIGIN + path, data=json.dumps(body).encode(), method="POST", headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json", "User-Agent": "aepm-publish/1.0 (+https://ourapps.app)"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        raw = error.read().decode("utf8", "replace")
        try:
            return error.code, json.loads(raw)
        except ValueError:
            return error.code, {"error": raw[:300]}


def load(path):
    return json.loads(Path(path).read_text())


def ports(items, allow_optional=False):
    out = []
    for p in items:
        item = {"id": p["id"], "type": p["type"], "version": p["version"]}
        if allow_optional and p.get("optional") is True:
            item["optional"] = True
        out.append(item)
    return out


def migration_summary(document):
    if document is None:
        return None
    m = document
    exported, imported = m.get("export", []), m.get("import", [])
    formats = sorted({f["format"] for f in exported + imported if isinstance(f, dict) and "format" in f})
    return {
        "stores": [{"id": s["id"], "schema": {"name": s["schema"]["name"], "version": s["schema"]["version"]}} for s in m.get("stores", [])],
        "formats": formats,
        "portable": True,
        "requiresStop": m.get("limits", {}).get("requiresStop") is True,
        "retentionDays": m.get("retention", {}).get("afterRemovalDays"),
    }


def verified_package(ref, store):
    """Read publication facts only from the digest-checked archive."""
    _, bundle, _, _, _ = load_aepm()
    archive = Path(store) / "objects" / f"{ref['digest']}.tar"
    return bundle.read_archive(ref, archive.read_bytes())


def foundation_facts(manifest):
    """Publisher declarations copied from the validated package manifest."""
    return {"licence": manifest["identity"]["licence"], **{
        field: manifest[field] for field in
        ("platforms", "classes", "implements", "upstream", "compatibility", "maintenance", "files", "artefacts")}}


def recipe_entry(ref, store, repo):
    manifest, payloads = verified_package(ref, store)
    if manifest["kind"] != "recipe" or manifest["identity"]["name"] != ref["name"]:
        raise ValueError("recipe publication identity does not match the archive")
    data = json.loads(payloads["recipe.json"])
    if (data["name"], data["version"]) != (ref["name"], ref["version"]):
        raise ValueError("recipe publication identity does not match recipe.json")
    svg = payloads[data["page"]["diagram"]].decode()
    digest = "sha256:" + ref["digest"]
    short = ref["name"].split("/", 1)[1]
    asset = f"{short}-{ref['version']}.tar"
    return {
        "format": 1, "recipe": data, "digest": digest,
        "source": {"repository": f"{ORG}/{repo}", "tag": f"v{ref['version']}"},
        "bundle": {"asset": asset, "digest": digest},
        "page": {"description": payloads[data["page"]["description"]].decode(),
                 "diagram": svg if len(svg) < 200000 else None, "screenshots": []},
        "foundations": foundation_facts(manifest),
    }


def component_entry(name, directory, ref, asset, store):
    manifest, payloads = verified_package(ref, store)
    if manifest["kind"] != "component" or manifest["identity"]["name"] != f"{ORG}/{name}":
        raise ValueError("component publication identity does not match the archive")
    digest = "sha256:" + ref["digest"]
    version = manifest["identity"]["version"]
    spec_path = directory / "authoring" / "spec.json"
    description = load(spec_path).get("description") if spec_path.exists() else None
    description = description or manifest["facilities"][0]["description"]
    client = sorted(set(manifest["platforms"]) - {"server"})
    server = ["hosting-provider", "own-hardware"] if name in ("server-host", "realtime-rooms", "room-directory") else []
    group = group_of(name)
    entry = {
        "format": 1,
        "name": manifest["identity"]["name"],
        "version": version,
        "digest": digest,
        "description": description,
        "category": group,
        "tags": sorted({group, *name.split("-")} - {"of", "and"})[:10],
        "targets": {"client": client, "server": server},
        "guardians": ["Level N Ltd (guardian key 08775f20)"],
        "provides": ports(manifest["provides"]),
        "requires": ports(manifest["requires"], True),
        "dependencies": [{"name": d["name"], "version": d["version"]} for d in manifest["dependencies"]],
        "suitability": json.loads(payloads["suitability.json"]),
        "migration": migration_summary(json.loads(payloads["migration.json"]) if "migration.json" in payloads else None),
        "source": {"repository": f"{ORG}/{name}", "tag": f"v{version}"},
        "bundle": {"asset": asset, "digest": digest},
        "settings": settings_of(manifest),
        "interfaceDigests": interface_digests(payloads, manifest),
        "interfaceModules": interface_modules(payloads, manifest),
        "foundations": foundation_facts(manifest),
    }
    return entry, version


def settings_of(manifest):
    """What the catalogue's fit rule reads of each setting."""
    return [{"id": c["id"], "type": c["type"], "grammar": c["grammar"], "required": bool(c["required"]),
             "secret": bool(c["secret"]), "hasDefault": "default" in c} for c in manifest["collect"]]


def interface_digests(payloads, manifest):
    """sha256 of each provided interface document's bytes, keyed type@version."""
    return {f"{p['type']}@{p['version']}": "sha256:" + hashlib.sha256(payloads[p["path"]]).hexdigest()
            for p in manifest["provides"]}


def interface_modules(payloads, manifest):
    """Each provided document's x-ourapps-modules, with whether the component installs that entry."""
    short = manifest["identity"]["name"].split("/")[1]
    destinations = [a["destination"] for a in manifest["artefacts"]]
    result = {}
    for p in manifest["provides"]:
        modules = json.loads(payloads[p["path"]]).get("x-ourapps-modules")
        if not isinstance(modules, dict):
            continue
        entry = {}
        for platform, path in modules.items():
            if not isinstance(path, str):
                entry[platform] = {"path": None, "installed": False}
                continue
            target = f"components/{short}/{path}"
            installed = any(d == target for d in destinations) if platform not in ("apple", "android") \
                else any(d.startswith(target.rstrip("/") + "/") for d in destinations)
            entry[platform] = {"path": path, "installed": installed}
        result[f"{p['type']}@{p['version']}"] = entry
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--key", required=True)
    parser.add_argument("--release-key", type=Path, required=True)
    parser.add_argument("--guardian-key", type=Path, required=True)
    parser.add_argument("--only", nargs="*", default=[])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--keep-store", action="store_true", help="keep the run's store even when every asset is verified on GitHub")
    args = parser.parse_args()
    names = sorted(p.name for p in (ROOT / "components").iterdir() if (p / "component.json").is_file())
    directories = {name: ROOT / f"components/{name}" for name in names}
    directories["ourpoker"] = ROOT / "recipes/ourpoker"
    sys.path.insert(0, str(ROOT / "aepm"))
    from aepm import ledger, space
    packaged = sum((directory / item["path"]).stat().st_size for directory in directories.values()
                   for item in json.loads((directory / "component.json").read_text())["files"])
    # archives, statements, reports, bills of materials, evidence and the staged release assets: about three copies of
    # the packaged bytes; each review check's own copy is admitted by aepm
    admission = space.reserve(TEMP, 3 * packaged, purpose="catalogue publish store")
    admission.__enter__()
    store, refs = pack_all(directories)
    work = store.parent
    # registered as kept: it holds the only local copy of each signed statement until GitHub holds them
    ledger.register(work, "work", purpose="catalogue publish store", keep=True)
    print(f"store {store}", flush=True)
    _, _, _, trust, _ = load_aepm()
    for ref in refs.values():
        trust.publish_release(store, ref, args.release_key)
    results, published = [], []
    for name in names + ["ourpoker"]:
        if args.only and name not in args.only:
            continue
        recipe = name == "ourpoker"
        directory = directories[name]
        repo = "ourpoker-recipe" if recipe else name
        ref = refs[name]
        digest = "sha256:" + ref["digest"]
        files, summary = sign_and_review(store, ref, args.release_key, args.guardian_key, work)
        version = ref["version"]
        if recipe:
            body = recipe_entry(ref, store, repo)
            asset = body["bundle"]["asset"]
            path = "/api/dev/recipes"
        else:
            asset = f"{name}-{version}.tar"
            body, version = component_entry(name, directory, ref, asset, store)
            path = "/api/dev/components"
        if args.dry_run:
            print(name, version, digest[:19], summary, len(json.dumps(body)), flush=True)
            continue
        state = release(repo, f"v{version}", asset[:-len(".tar")], files, ref["digest"], f"{name} {version}", work)
        published.append((repo, f"v{version}", asset[:-len(".tar")], files))
        status, answer = post(path, body, args.key)
        results.append((name, version, state, status, answer.get("status") or answer.get("error") or answer))
        print(name, version, state, status, answer.get("status") or answer, flush=True)
    bad = [r for r in results if r[3] not in (200, 201)]
    print(f"published {len(results) - len(bad)} of {len(results)}")
    # The store holds the only local copy of each signed statement and report; it goes only when GitHub holds every one of them
    # with the same sha256.
    unverified = [item for entry in published for item in unverified_assets(*entry)]
    if bad or unverified or args.keep_store or args.dry_run or not published:
        for item in unverified:
            print(f"not on GitHub with the same sha256: {item}")
        print(f"store kept: {store}")
    else:
        # The review policies (public keys and the trusted package digests each review ran under) are kept; the review owner's
        # private key signs nothing that is published, and is removed with the store.
        records = TEMP / "publish-records" / work.name
        records.mkdir(parents=True)
        for path in sorted(work.glob("policy-*.json")):
            if path.is_symlink() or not path.is_file():
                raise SystemExit(f"{path} is not a regular file; store kept: {store}")
            (records / path.name).write_bytes(path.read_bytes())
            if hashlib.sha256((records / path.name).read_bytes()).hexdigest() != hashlib.sha256(path.read_bytes()).hexdigest():
                raise SystemExit(f"copy of {path} differs; store kept: {store}")
        ledger.register(records, "work", purpose="catalogue publish review policies", keep=True)
        print(f"every asset verified on GitHub by sha256; review policies kept in {records}; store removed: {store}")
        shutil.rmtree(work)
        ledger.forget(work)
    admission.__exit__(None, None, None)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
