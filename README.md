# Library tools

Run Python 3.12 with bytecode disabled and the aepm checkout on the module search path:

```sh
export PYTHONPATH="$PWD/aepm"
python3.12 -B tools/check_component.py --all --allow-network
python3.12 -B -m unittest discover -s tools/tests
```

The component checker packs and inspects each archive, checks independent class inventories and public entry modules, and runs required local suites in clean copies. Each suite's copy and home folder are removed when the suite ends. Network preparation requires `--allow-network`.

## Authoring

`scaffold_component.py` reads a component's `authoring/spec.json`. Implementation folder names are in `platforms`; supported application targets are in `supportedPlatforms`. Class references, source records, compatibility and maintenance declarations are explicit. `templates` lists source files the installer renders. Licence and notice files are supplied by the author and preserved.

```sh
python3.12 -B tools/scaffold_component.py components/NAME --spec components/NAME/authoring/spec.json --render
python3.12 -B tools/sync_required_interfaces.py --check
python3.12 -B classes/check_scaffolder.py
```

## Catalogue publication checks

The catalogue checkout contains the shared publication validators. Archive identity, licences, source records, class descriptors and contents are read from digest-checked archives.

```sh
python3.12 -B tools/validate_catalogue.py --catalogue "$OURAPPS"
```

This command packs and validates all component and recipe publications without network or database writes. `publish_catalogue.py` uses the same projection helpers and signs reviews only after their required checks pass. It prints its store's path. The store is removed only when every package published and every required file (archive, bill of materials, release and guardian attestations, review report, and evidence when present) is on its GitHub release with the same sha256; otherwise, after a dry run, or with `--keep-store`, it is kept. The review policies (public keys and trusted package digests) are copied first to `publish-records/<run>/` beside it, which is kept; the review owner's private key signs nothing published and is removed with the store. Catalogue pages label incoming metadata as publisher-declared; signed execution evidence is verified by aepm under an owner's policy.

## Ownership and update audit

```sh
python3.12 -B tools/audit_library.py
python3.12 -B tools/audit_library.py --check-updates
python3.12 -B tools/audit_library.py --store STORE --policy POLICY
```

The audit emits one data record per package. Offline checks validate package records without contacting providers. Release checks query public GitHub release and npm endpoints, distinguish updates from unavailable results, and identify sources requiring a manual check. Scheduling is external to this command.

With `--store` and `--policy`, the audit verifies the current archive's signed review and reports its actual successful verification time. Without that evidence the time is null.
