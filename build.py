#!/usr/bin/env python3
"""Merge selected paths from several zip archives into one output zip.

Sources are processed in order. When two sources provide the same JSON file
(.json or .mcmeta) their contents are merged; anything else is overwritten by
the later source. Both cases are reported.

Usage:
    python build.py [--no-sync] output.zip

sources.json is read from this script's folder. Its "sources" keys name zips
in packs/; "extra_files" paths are resolved against this folder.

Unless --no-sync is given, `fetch.py sync` runs first so every zip in packs/
matches the pinned version in upstream.lock.json.

"sources" maps each source zip (a filename in packs/) to a list of paths
inside it. A path may be a single entry or a folder (everything under it is
taken). An empty list or "*" takes the whole archive. A path prefixed with
"!" is excluded, and exclusions win over inclusions.

"extra_files" lists loose files to add after every archive, so they win any
conflict.

    {
        "sources": {
            "base.zip": ["*", "!README.md"],
            "overlay.zip": ["assets/minecraft/textures/block/", "pack.png"]
        },
        "extra_files": ["./pack.mcmeta", "./pack.png"]
    }
"""

import json
import os
import subprocess
import sys
import zipfile

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(BASE, "sources.json")
PACKS = os.path.join(BASE, "packs")


def _unique_keys(pairs):
    """Reject duplicate keys, which json would otherwise silently collapse."""
    seen = {}
    for key, value in pairs:
        if key in seen:
            raise ValueError(f"duplicate key: {key}")
        seen[key] = value
    return seen


def _matches(name, prefix):
    return name == prefix or name.startswith(prefix + "/")


def _selected(names, paths):
    """(names to take, patterns that matched nothing)."""
    included, excluded, unused = [], [], []
    for path in paths:
        excluding = path.startswith("!")
        prefix = (path[1:] if excluding else path).strip("/")
        if excluding:
            excluded.append(prefix)
        else:
            included.append(prefix)
        if prefix != "*" and not any(_matches(name, prefix) for name in names):
            unused.append(path)

    take_all = not included or "*" in included
    picked = [
        name
        for name in names
        if (take_all or any(_matches(name, p) for p in included))
        and not any(_matches(name, p) for p in excluded)
    ]
    return picked, unused


def _deep_merge(old, new):
    if isinstance(old, dict) and isinstance(new, dict):
        merged = dict(old)
        for key, value in new.items():
            merged[key] = _deep_merge(old[key], value) if key in old else value
        return merged
    if isinstance(old, list) and isinstance(new, list):
        return old + [item for item in new if item not in old]
    return new


def _merge_json(name, old, new):
    """Combined JSON bytes, or None if the entry isn't mergeable JSON."""
    if not name.endswith((".json", ".mcmeta")):
        return None
    try:
        merged = _deep_merge(json.loads(old), json.loads(new))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return json.dumps(merged, indent=2).encode()


def validate(sources, extra_files):
    """Human-readable problems that would make a merge fail or silently do nothing."""
    if not isinstance(sources, dict):
        return ['"sources" must be an object mapping zip path -> list of paths']
    if not isinstance(extra_files, list) or not all(
        isinstance(p, str) for p in extra_files
    ):
        return ['"extra_files" must be a list of strings']

    problems = []
    for archive, paths in sources.items():
        if isinstance(paths, str):
            problems.append(f'{archive}: paths must be a list, e.g. ["{paths}"]')
        elif not isinstance(paths, list) or not all(isinstance(p, str) for p in paths):
            problems.append(f"{archive}: paths must be a list of strings")
        elif not os.path.exists(os.path.join(PACKS, archive)):
            problems.append(f"packs/{archive}: file not found (run fetch.py sync)")
        elif not zipfile.is_zipfile(os.path.join(PACKS, archive)):
            problems.append(f"packs/{archive}: not a zip archive")

    for path in extra_files:
        if not os.path.isfile(os.path.join(BASE, path)):
            problems.append(f"{path}: file not found")
    return problems


def _add(entries, merged, conflicts, name, source, data):
    if name in entries:
        previous, old = entries[name]
        combined = _merge_json(name, old, data)
        if combined is None:
            conflicts.append((name, previous, source))
        else:
            data = combined
            merged.append((name, previous, source))
    entries[name] = (source, data)


def merge(sources, extra_files, output):
    """sources: ordered mapping of zip path -> list of paths inside it."""
    entries = {}
    merged = []
    conflicts = []
    unused = []

    for archive, paths in sources.items():
        with zipfile.ZipFile(os.path.join(PACKS, archive)) as zf:
            picked, unmatched = _selected(zf.namelist(), paths)
            unused += [(archive, path) for path in unmatched]
            for name in picked:
                if name.endswith("/"):
                    continue
                _add(entries, merged, conflicts, name, archive, zf.read(name))

    for path in extra_files:
        with open(os.path.join(BASE, path), "rb") as f:
            data = f.read()
        name = os.path.normpath(path).replace(os.sep, "/")
        _add(entries, merged, conflicts, name, path, data)

    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as out:
        for name, (_, data) in entries.items():
            out.writestr(name, data)

    return entries, merged, conflicts, unused


def sync_packs():
    """Run 'fetch.py sync' so packs/ matches upstream.lock.json."""
    fetch = os.path.join(BASE, "fetch.py")
    if not os.path.isfile(fetch):
        sys.exit("error: fetch.py not found next to build.py; pass --no-sync")
    try:
        subprocess.run([sys.executable, fetch, "sync"], check=True)
    except subprocess.CalledProcessError:
        sys.exit("error: fetch.py sync failed; fix the sources or pass --no-sync")


def main():
    args = sys.argv[1:]
    if any(a in ("-h", "--help") for a in args):
        sys.exit(__doc__)
    no_sync = "--no-sync" in args
    if no_sync:
        args.remove("--no-sync")
    if len(args) != 1:
        sys.exit(__doc__)

    output = args[0]
    if not no_sync:
        sync_packs()
    try:
        with open(CONFIG) as f:
            config = json.load(f, object_pairs_hook=_unique_keys)
    except OSError as e:
        sys.exit(f"error: cannot read {CONFIG}: {e.strerror}")
    except json.JSONDecodeError as e:
        sys.exit(f"error: {CONFIG} is not valid JSON: {e}")
    except ValueError as e:
        sys.exit(f"error: {CONFIG}: {e}")

    if not isinstance(config, dict):
        sys.exit('error: top level must be an object with "sources" and "extra_files"')

    sources = config.get("sources", {})
    extra_files = config.get("extra_files", [])

    problems = validate(sources, extra_files)
    if problems:
        sys.exit("\n".join(f"error: {p}" for p in problems))

    try:
        entries, merged, conflicts, unused = merge(sources, extra_files, output)
    except OSError as e:
        sys.exit(f"error: {e}")

    for archive, path in unused:
        print(f"warning: {archive}: '{path}' matched nothing")

    for name, first, second in merged:
        print(f"merged: {name}\n  {first} + {second}")

    for name, loser, winner in conflicts:
        print(f"conflict: {name}\n  {loser} -> overwritten by {winner}")

    print(
        f"\n{len(entries)} entries written to {output} "
        f"({len(merged)} merged, {len(conflicts)} conflicts, {len(unused)} unused paths)"
    )


if __name__ == "__main__":
    main()
