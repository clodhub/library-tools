# Library tools

Run Python 3.12 with bytecode disabled and the clod checkout on the module search path:

```sh
export PYTHONPATH="$PWD/clod"
python3.12 -B tools/check_component.py --all --allow-network
python3.12 -B -m unittest discover -s tools/tests
```

`CLOD_CHECKOUT` names another clod checkout for every tool here (set `PYTHONPATH` to the same folder). `CLOD_LIBRARY` names another list in the format of `library.json`, which every tool here and in `classes/` then reads instead; a second recipe keeps its own list this way until it joins the library. With it, the classes checks write their proofs to a `proof/` folder beside that list instead of `classes/proof/`. `CLOD_UPSTREAM_CACHE` names another folder for the fetched upstream sources the metadata generator keeps (default `classes/upstream/`).

`library.json` lists the library: the folders under `components/`, `recipes/` and `classes/` that the tools check, regenerate, bundle, validate, audit and publish (`library.py` reads it). Any other folder there is not part of the library and is never read or written. Add a package to the list to bring it in.

The component checker packs and inspects each archive, checks independent class inventories and public entry modules, and runs required local suites in clean copies. Each suite's copy and home folder are removed when the suite ends. Network preparation requires `--allow-network`.

## Authoring

`scaffold_component.py` reads a component's `authoring/spec.json`. `sourceRevision` is the revision of the source the component was made from, written into `identity.sourceRevision` and `SOURCE.md`. Implementation folder names are in `platforms`; supported application targets are in `supportedPlatforms`. Class references, source records, compatibility and maintenance declarations are explicit. A new component's spec leaves out every field `classes/generate.py` keeps (`supportedPlatforms`, `toolchains`, `classes`, `upstream`, `compatibility`, `maintenance`, `release`, `images`, `vulnerabilityWaivers`) and names its class in `implements` (`[{"name": "pkg:clod/com.clodhub/<name>-class"}]`); its first scaffold computes them with generate.py's own code, and the next `generate.py` run writes them into the spec. Every scaffold first writes any missing document the listed classes give the component (required copies, and the provided documents and `*.schema.json` files of the class it implements); `sync_required_interfaces.py` keeps them current and `check_component.py` compares them. `templates` lists source files the installer renders. Licence and notice files are supplied by the author and preserved.

```sh
python3.12 -B tools/scaffold_component.py components/NAME --spec components/NAME/authoring/spec.json --render
python3.12 -B tools/sync_required_interfaces.py --check
python3.12 -B classes/check_scaffolder.py
```

## Catalogue publication checks

`CLODHUB` names the catalogue checkout (clodhub/clodhub, `clodhub/` in the workspace), which contains the shared publication validators. Archive identity, licences, source records, class descriptors and contents are read from digest-checked archives.

```sh
python3.12 -B tools/validate_catalogue.py --catalogue "$CLODHUB"
```

This command packs and validates every component and recipe publication in `library.json` without network or database writes.

Run `publish_catalogue.py`, and any `git push` to clodhub, with `GITHUB_TOKEN` and `GH_TOKEN` unset, so `gh` uses its keyring login. `publish_catalogue.py` uses the same projection helpers and signs reviews only after their required checks pass. It follows the catalogue's "Catalogue publish API":
- `classes/categories.json` once;
- then, per package:
  - each example image from the verified archive, with a 480-pixel-wide WebP thumbnail;
  - each class it carries, once per class version;
  - its entry (component format 4, recipe format 3; a class body is format 3) with `release`, `images`, `vulnerabilityWaivers` and `quality`. A component entry's `page.description` is the archive's CONTRACT.md: its purpose, then its scenarios as Examples, user inputs as Settings, and integration, infrastructure, verification and updates. Each interface carries its document's description and each setting its default.

`--entries-only` publishes every listed component version again as an entry in the current format, after a format change. The archive comes from the version's GitHub release, checked against the catalogue's digest, and the description from `authoring/spec.json` at the release tag. Nothing is signed, reviewed or uploaded, and the catalogue keeps each version's quality.

Each package's GitHub repository is `clodhub/<name>`, `<name>` being the last segment of its package name (`pkg:clod/com.clodhub/ourpoker-recipe` is `clodhub/ourpoker-recipe`). Quality is `reviewed`, certified when the guardian key is the one Level N's published policy names. After a recipe is published, every component version it pins (slot preferred components and job candidates) is set to `integration-tested`. It prints its store's path. The store is removed only when every package published and every required file (archive, bill of materials, release and guardian attestations, review report, and evidence when present) is on its GitHub release with the same sha256; otherwise, after a dry run, or with `--keep-store`, it is kept. The review policies (public keys and trusted package digests) are copied first to `publish-records/<run>/` beside it, which is kept; the review owner's private key signs nothing published and is removed with the store. Catalogue pages label incoming metadata as publisher-declared; signed execution evidence is verified by clod under an owner's policy.

## Build reports

```sh
python3.12 -B tools/publish_evidence.py --key ch_... [tools/evidence/REPORT.json ...]
```

`tools/evidence/` holds one JSON report per agent build of a recipe (format 2, kind `recipe-build`):
- the recipe's package name, version and digest, and the clod repository and commit (field `clod`);
- the agent and model, the prompt, conditions and answers;
- measured time, tokens and cost;
- per-target results, problems and the agent's rating.

The tool sends them with a curator's key to the catalogue, which shows them on the recipe page.

## Known vulnerabilities

```sh
python3.12 -B tools/vulnerabilities.py components/NAME [...]
```

Every upstream purl in a package manifest is queried at OSV (`api.osv.dev/v1/querybatch`, at most 1,000 queries per request, every result page read), as written; npm scopes are written `%40scope`, and OSV answers that and `@scope` alike (`OSV_LIVE=1` runs the test that asks it). It prints each advisory with its severity and summary and exits 1 when any is not waived. `publish_catalogue.py` runs the same check on every package before it signs or uploads anything. It refuses the run when an advisory is not waived in the manifest's `vulnerabilityWaivers`, when a waiver names an advisory OSV no longer reports, or when OSV refuses a purl (its vulnerabilities would be unknown). A waiver names the id OSV reports.

## Ownership and update audit

```sh
python3.12 -B tools/audit_library.py
python3.12 -B tools/audit_library.py --check-updates
python3.12 -B tools/audit_library.py --store STORE --policy POLICY
```

The audit emits one data record per package. Offline checks validate package records without contacting providers. Release checks query public GitHub release and npm endpoints, distinguish updates from unavailable results, and identify sources requiring a manual check. Scheduling is external to this command.

With `--store` and `--policy`, the audit verifies the current archive's signed review and reports its actual successful verification time. Without that evidence the time is null.
