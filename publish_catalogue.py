#!/usr/bin/env python3.12
"""Pack every component and the recipe, sign and review each, release it on GitHub, and publish it to ourapps.app.

usage: PYTHONPATH=aepm python3.12 -B tools/publish_catalogue.py --key oa_... --release-key PEM --guardian-key PEM
                                                               [--only name ...] [--dry-run] [--entries-only]

All packages are packed into one temporary store, so each review finds its dependencies. For each package:
the Level N release attestation (`--release-key`); `aepm review --trusted --allow-network` under a policy that pins
the Level N keys and trusts the package and its dependencies; publishing stops unless required-local passed; the
guardian verification summary (`--guardian-key`); the CycloneDX 1.6 bill of materials. A GitHub release `v<version>`
on ourapps-ae/<repo> (gh, keyring login) carries `<name>-<version>.tar`, `.cdx.json`, `.release.dsse.json`,
`.guardian.dsse.json`, `.review.json` and, when the review report names retained documents (execution evidence, contexts,
authorizations), `.evidence.tar` holding them at their store-relative paths; an existing release gets the assets it lacks, after its archive is checked
to be the same bytes. Then, with the API key (the catalogue's "Catalogue publish API"): classes/categories.json once, and per
package its example images with a 480-pixel-wide WebP thumbnail each, the class it implements (once per class version) and its
entry (component format 3, recipe format 2) with its release channel, images, vulnerability waivers and quality. Quality is `reviewed` (certified when the
guardian key is Level N's) and, once the recipe is published, `integration-tested` for every component version the recipe pins.
A component entry's page text comes from the archive's CONTRACT.md, its interface descriptions from the interface documents.
With --entries-only, every listed version of each component is published again as an entry in the current format, from the
archive on its GitHub release (checked against the catalogue's digest): nothing is signed, reviewed or uploaded, and the
catalogue keeps each version's quality.
AEPM_CHECKOUT names the aepm checkout to use (default: the workspace's aepm/).
"""
import argparse
import hashlib
import io
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
AEPM = Path(os.environ.get("AEPM_CHECKOUT", ROOT / "aepm"))
TEMP = Path("/private/var/folders/yk/8tzc9b_1713dlhdp5vzkgg100000gn/T/opencode")
PY = "/Users/george/.pyenv/versions/3.12.8/bin/python3.12"
ORIGIN = "https://ourapps.app"
ORG = "ourapps-ae"

def run(argv, **kw):
    return subprocess.run(argv, check=True, capture_output=True, text=True, **kw)


def load_aepm():
    sys.path.insert(0, str(AEPM))
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


def vulnerability_refusals(refs, store):
    """One line per package that may not be published: a known advisory against its upstream code that its manifest does
    not waive, or an upstream purl OSV cannot check. Checked before anything is signed or uploaded."""
    import vulnerabilities
    lines = []
    for name, ref in refs.items():
        manifest, _ = verified_package(ref, store)
        try:
            _, refused, unused = vulnerabilities.check(manifest)
        except vulnerabilities.Unqueryable as error:
            lines.append(f"{name} {ref['version']}: vulnerabilities unknown, {error}")
            continue
        for key, affected in refused.items():
            lines.append(f"{name} {ref['version']}: {key} affects {', '.join(affected)}; not waived")
        for key in unused:
            lines.append(f"{name} {ref['version']}: waiver {key} names no advisory OSV reports")
    return lines


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
    return send("POST", path, json.dumps(body).encode(), "application/json", key)


def send(method, path, data, content_type, key):
    request = urllib.request.Request(ORIGIN + path, data=data, method=method, headers={"Authorization": f"Bearer {key}", "Content-Type": content_type, "User-Agent": "aepm-publish/1.0 (+https://ourapps.app)"})
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


def interface_description(payloads, port):
    """What the interface document says the interface is: OpenRPC info.description, else its top-level description."""
    document = json.loads(payloads[port["path"]])
    text = (document.get("info") or {}).get("description") or document.get("description") or ""
    return text.strip() if isinstance(text, str) else ""


def ports(items, payloads, allow_optional=False):
    out = []
    for p in items:
        item = {"id": p["id"], "type": p["type"], "version": p["version"], "description": interface_description(payloads, p)}
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


IMAGE_TYPES = {".png": "image/png", ".webp": "image/webp"}


def thumbnail(data):
    """A WebP 480 pixels wide (an image narrower than that keeps its width), keeping the aspect ratio."""
    from PIL import Image
    with Image.open(io.BytesIO(data)) as image:
        image.load()
        width = min(480, image.width)
        height = max(1, round(image.height * width / image.width))
        small = image.convert("RGBA" if "A" in image.getbands() or image.mode == "P" else "RGB")
        if width != image.width:
            small = small.resize((width, height), Image.LANCZOS)
        out = io.BytesIO()
        small.save(out, "WEBP", quality=82, method=6)
        return out.getvalue()


def images_of(manifest, payloads):
    """The entry's images and the uploads they need: (entries, [(sha256 hex, bytes, content type)]), from the verified archive."""
    entries, uploads = [], []
    for image in manifest.get("images", []):
        data = payloads[image["path"]]
        small = thumbnail(data)
        main, thumb = hashlib.sha256(data).hexdigest(), hashlib.sha256(small).hexdigest()
        uploads += [(main, data, IMAGE_TYPES[Path(image["path"]).suffix.lower()]), (thumb, small, "image/webp")]
        entries.append({"digest": "sha256:" + main, "thumbnail": "sha256:" + thumb, "caption": image["caption"],
                        "platform": image["platform"], "width": image["width"], "height": image["height"]})
    return entries, uploads


def class_bodies(manifest):
    """The class publication body of each class descriptor the manifest carries."""
    return [{"format": 2, "class": item["descriptor"]} for item in manifest["classes"]]


def certified(guardian_key):
    """Whether the guardian key is Level N's, as the published policy names it."""
    policy = load(ROOT / "tools/standards/levelnpolicy.json")
    mine = public_key(guardian_key).strip()
    return any(item["publicKey"].strip() == mine for item in policy["keys"]["guardian"])


def pinned_components(recipe):
    """name -> version of every component a recipe.json pins: each slot's preferred component and each job candidate."""
    pins = {slot["preferred"]["name"]: slot["preferred"]["version"] for slot in recipe["components"]}
    slots = {slot["id"]: slot for slot in recipe["components"]}
    for job in recipe.get("jobs", []):
        for candidate in job["candidates"]:
            pins.setdefault(candidate["component"], candidate.get("version") or slots[job["slot"]]["preferred"]["version"])
    return pins


def foundation_facts(manifest):
    """Publisher declarations copied from the validated package manifest."""
    return {"licence": manifest["identity"]["licence"], **{
        field: manifest[field] for field in
        ("platforms", "classes", "implements", "upstream", "compatibility", "maintenance", "files", "artefacts")}}


def recipe_entry(ref, store, repo, quality=None):
    """The format 2 recipe entry and the image uploads it needs."""
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
    images, uploads = images_of(manifest, payloads)
    return {
        "format": 2, "recipe": data, "digest": digest,
        "source": {"repository": f"{ORG}/{repo}", "tag": f"v{ref['version']}"},
        "bundle": {"asset": asset, "digest": digest},
        "page": {"description": payloads[data["page"]["description"]].decode(),
                 "diagram": svg if len(svg) < 200000 else None},
        "foundations": foundation_facts(manifest),
        "release": manifest["release"], "images": images, "vulnerabilityWaivers": manifest["vulnerabilityWaivers"],
        "quality": quality,
    }, uploads


def component_entry(name, directory, ref, asset, store, quality=None, spec=None):
    """The format 3 component entry, its version and the image uploads it needs. Its listing category is its class's. The
    description is authoring/spec.json's (`spec`, else the working tree's), or the manifest's first facility."""
    manifest, payloads = verified_package(ref, store)
    if manifest["kind"] != "component" or manifest["identity"]["name"] != f"{ORG}/{name}":
        raise ValueError("component publication identity does not match the archive")
    digest = "sha256:" + ref["digest"]
    version = manifest["identity"]["version"]
    spec_path = directory / "authoring" / "spec.json"
    if spec is None and spec_path.exists():
        spec = load(spec_path)
    description = (spec or {}).get("description")
    description = description or manifest["facilities"][0]["description"]
    client = sorted(set(manifest["platforms"]) - {"server"})
    server = ["hosting-provider", "own-hardware"] if name in ("server-host", "realtime-rooms", "room-directory") else []
    categories = {segment for item in manifest["classes"] for segment in item["descriptor"]["category"].split("/")}
    images, uploads = images_of(manifest, payloads)
    entry = {
        "format": 3,
        "name": manifest["identity"]["name"],
        "version": version,
        "digest": digest,
        "description": description,
        "page": {"description": page_description(manifest, payloads)},
        "tags": sorted({*categories, *name.split("-")} - {"of", "and"})[:10],
        "targets": {"client": client, "server": server},
        "guardians": ["Level N Ltd (guardian key 08775f20)"],
        "provides": ports(manifest["provides"], payloads),
        "requires": ports(manifest["requires"], payloads, True),
        "dependencies": [{"name": d["name"], "version": d["version"]} for d in manifest["dependencies"]],
        "suitability": json.loads(payloads["suitability.json"]),
        "migration": migration_summary(json.loads(payloads["migration.json"]) if "migration.json" in payloads else None),
        "source": {"repository": f"{ORG}/{name}", "tag": f"v{version}"},
        "bundle": {"asset": asset, "digest": digest},
        "settings": settings_of(manifest),
        "interfaceDigests": interface_digests(payloads, manifest),
        "interfaceModules": interface_modules(payloads, manifest),
        "foundations": foundation_facts(manifest),
        "release": manifest["release"], "images": images, "vulnerabilityWaivers": manifest["vulnerabilityWaivers"],
        "quality": quality,
    }
    return entry, version, uploads


def settings_of(manifest):
    """What the catalogue's fit rule reads of each setting, and the default aepm writes when a recipe leaves it out."""
    return [{"id": c["id"], "type": c["type"], "grammar": c["grammar"], "required": bool(c["required"]),
             "secret": bool(c["secret"]), "hasDefault": "default" in c, **({"default": c["default"]} if "default" in c else {})}
            for c in manifest["collect"]]


# The component page from CONTRACT.md: its purpose first, then these sections under the page's headings.
PAGE_SECTIONS = (("Scenarios and presentation", "Examples"), ("User inputs", "Settings"),
                 ("Integration", "For developers: how an application uses it"), ("Infrastructure", "Accounts and services"),
                 ("Verification", "How it is checked"), ("Updates and removal", "Updates"))


def contract_sections(text):
    """CONTRACT.md's level-2 sections, heading to body."""
    sections, heading, lines = {}, None, []
    for line in text.splitlines():
        if line.startswith("## "):
            if heading is not None:
                sections[heading] = "\n".join(lines).strip()
            heading, lines = line[3:].strip(), []
        elif heading is not None:
            lines.append(line)
    if heading is not None:
        sections[heading] = "\n".join(lines).strip()
    return sections


def page_description(manifest, payloads):
    """The page text from the verified archive's contract: the Facilities line and the generated setting list are left out,
    because the page shows the description and the settings table."""
    sections = contract_sections(payloads[manifest["documents"]["contract"]].decode())
    purpose = "\n".join(line for line in sections.get("Purpose and facilities", "").splitlines() if not line.startswith("Facilities: ")).strip()
    parts = [purpose]
    for source, heading in PAGE_SECTIONS:
        body = sections.get(source, "")
        if source == "User inputs":
            ids = {c["id"] for c in manifest["collect"]}
            body = "\n".join(line for line in body.splitlines()
                             if not any(line.startswith(f"- `{i}` (") for i in ids)).strip()
        if body:
            parts.append(f"## {heading}\n\n{body}")
    return "\n\n".join(part for part in parts if part)


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


def released_digest(repo, version, asset):
    """The sha256 GitHub reports for a release asset, or None."""
    view = gh("release", "view", f"v{version}", "--repo", f"{ORG}/{repo}", "--json", "assets")
    if view.returncode != 0:
        return None
    return next((a.get("digest") for a in json.loads(view.stdout)["assets"] if a["name"] == asset), None)


def get(path):
    with urllib.request.urlopen(urllib.request.Request(ORIGIN + path, headers={"User-Agent": "aepm-publish/1.0 (+https://ourapps.app)"})) as response:
        return json.loads(response.read())


def republish_entries(args):
    """Every listed version of each component, published again as an entry in the current format. The archive is the one
    on the version's GitHub release, checked against the catalogue's digest, and the description is spec.json at the
    release tag. No signing, review or upload; quality is left out, so the catalogue keeps what it recorded. Images and
    classes are sent first, as for a publication."""
    names = sorted(p.name for p in (ROOT / "components").iterdir() if (p / "component.json").is_file())
    names = [name for name in names if not args.only or name in args.only]
    work = Path(tempfile.mkdtemp(prefix="republish-", dir=TEMP)).resolve()
    store = work / "store"
    (store / "objects").mkdir(parents=True)
    print(f"store {store}", flush=True)
    failed, done, sent_classes = 0, 0, set()
    try:
        for name in names:
            directory = ROOT / "components" / name
            for listed in get(f"/api/components/{name}")["versions"]:
                version, digest = listed["version"], listed["digest"]
                asset = f"{name}-{version}.tar"
                fetched = gh("release", "download", f"v{version}", "--repo", f"{ORG}/{name}", "--pattern", asset, "--dir", str(work), "--clobber")
                data = (work / asset).read_bytes() if fetched.returncode == 0 else b""
                if "sha256:" + hashlib.sha256(data).hexdigest() != digest:
                    print(name, version, "refused: the GitHub release does not hold the archive the catalogue lists", flush=True)
                    failed += 1
                    continue
                ref = {"name": f"{ORG}/{name}", "version": version, "digest": digest.removeprefix("sha256:")}
                (store / "objects" / f"{ref['digest']}.tar").write_bytes(data)
                shown = subprocess.run(["git", "-C", str(directory), "show", f"v{version}:authoring/spec.json"], capture_output=True, text=True)
                spec = json.loads(shown.stdout) if shown.returncode == 0 else {}
                body, _, uploads = component_entry(name, directory, ref, asset, store, None, spec)
                manifest, _ = verified_package(ref, store)
                if args.dry_run:
                    print(name, version, len(json.dumps(body)), "bytes", flush=True)
                    continue
                problem = None
                for hexdigest, image, content_type in uploads:
                    status, answer = send("PUT", f"/api/dev/images/{hexdigest}", image, content_type, args.key)
                    if status not in (200, 201):
                        problem = f"image {hexdigest[:12]}: {answer.get('error') or answer}"
                        break
                for body_of_class in class_bodies(manifest):
                    key = (body_of_class["class"]["name"], body_of_class["class"]["version"])
                    if problem or key in sent_classes:
                        continue
                    status, answer = post("/api/dev/classes", body_of_class, args.key)
                    if status not in (200, 201):
                        problem = f"class {key[0]} {key[1]}: {answer.get('error') or answer}"
                    sent_classes.add(key)
                if problem is None:
                    status, answer = post("/api/dev/components", body, args.key)
                    if status not in (200, 201):
                        problem = answer.get("error") or answer
                print(name, version, problem or answer.get("status"), flush=True)
                failed += problem is not None
                done += problem is None
    finally:
        if not args.keep_store:
            shutil.rmtree(work, ignore_errors=True)
    print(f"republished {done}, failed {failed}")
    sys.exit(1 if failed else 0)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--key", required=True)
    parser.add_argument("--release-key", type=Path, required=True)
    parser.add_argument("--guardian-key", type=Path, required=True)
    parser.add_argument("--only", nargs="*", default=[])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--keep-store", action="store_true", help="keep the run's store even when every asset is verified on GitHub")
    parser.add_argument("--entries-only", action="store_true",
                        help="publish the component entries again in the current entry format for versions whose GitHub release holds "
                             "the same archive: no signing, review or upload, and the stored quality stays")
    args = parser.parse_args()
    if args.entries_only:
        return republish_entries(args)
    names = sorted(p.name for p in (ROOT / "components").iterdir() if (p / "component.json").is_file())
    directories = {name: ROOT / f"components/{name}" for name in names}
    directories["ourpoker"] = ROOT / "recipes/ourpoker"
    sys.path.insert(0, str(AEPM))
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
    refusals = vulnerability_refusals({name: refs[name] for name in names + ["ourpoker"] if not args.only or name in args.only}, store)
    if refusals:
        for line in refusals:
            print(line)
        print(f"store kept: {store}")
        sys.exit(1)
    for ref in refs.values():
        trust.publish_release(store, ref, args.release_key)
    quality = {"level": "reviewed", "certified": certified(args.guardian_key), "liveTested": None}
    categories = load(ROOT / "classes/categories.json")
    from aepm.foundations import validate_categories
    validate_categories(categories, sorted({item["descriptor"]["category"] for ref in refs.values()
                                            for item in verified_package(ref, store)[0]["classes"]}))
    if not args.dry_run:
        status, answer = post("/api/dev/categories", categories, args.key)
        print("categories", status, answer.get("status") or answer, flush=True)
        if status not in (200, 201):
            print(f"store kept: {store}")
            sys.exit(1)
    results, published, classes_sent, versions = [], [], set(), {}
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
            body, uploads = recipe_entry(ref, store, repo, quality)
            asset = body["bundle"]["asset"]
            path = "/api/dev/recipes"
        else:
            asset = f"{name}-{version}.tar"
            body, version, uploads = component_entry(name, directory, ref, asset, store, quality)
            path = "/api/dev/components"
        manifest, _ = verified_package(ref, store)
        if args.dry_run:
            print(name, version, digest[:19], summary, len(json.dumps(body)), f"{len(uploads)} image files", flush=True)
            continue
        state = release(repo, f"v{version}", asset[:-len(".tar")], files, ref["digest"], f"{name} {version}", work)
        published.append((repo, f"v{version}", asset[:-len(".tar")], files))
        failed = None
        for hexdigest, data, content_type in uploads:
            status, answer = send("PUT", f"/api/dev/images/{hexdigest}", data, content_type, args.key)
            if status not in (200, 201):
                failed = (status, f"image {hexdigest[:12]}: {answer.get('error') or answer}")
                break
        for body_of_class in class_bodies(manifest):
            key = (body_of_class["class"]["name"], body_of_class["class"]["version"])
            if failed or key in classes_sent:
                continue
            status, answer = post("/api/dev/classes", body_of_class, args.key)
            if status not in (200, 201):
                failed = (status, f"class {key[0]} {key[1]}: {answer.get('error') or answer}")
            else:
                classes_sent.add(key)
        status, answer = failed or post(path, body, args.key)
        if status in (200, 201):
            versions[manifest["identity"]["name"]] = version
        results.append((name, version, state, status, answer.get("status") or answer.get("error") or answer if isinstance(answer, dict) else answer))
        print(name, version, state, status, answer.get("status") or answer if isinstance(answer, dict) else answer, flush=True)
    recipe_name = f"{ORG}/ourpoker"
    if not args.dry_run and recipe_name in versions:
        recipe_data = json.loads(verified_package(refs["ourpoker"], store)[1]["recipe.json"])
        for component, pinned in sorted(pinned_components(recipe_data).items()):
            if versions.get(component) != pinned:
                print(f"{component} {pinned}: pinned by the recipe but not published in this run; quality unchanged")
                continue
            status, answer = post(f"/api/dev/components/{component.split('/', 1)[1]}/{pinned}/quality",
                                  {**quality, "level": "integration-tested"}, args.key)
            if status != 200:
                results.append((component.split("/", 1)[1], pinned, "quality", status, answer.get("error") or answer))
            print(component, pinned, "integration-tested", status, flush=True)
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
