#!/usr/bin/env python3
"""Merge selected paths from several zip archives into one output zip.

Sources are processed in order. When two sources provide the same JSON file
(.json or .mcmeta) their contents are merged; anything else is overwritten by
the later source. Both cases are reported.

Usage:
    python build.py [--no-sync] output.zip

sources.json is read from this script's folder. Its "sources" keys name zips
in packs/; "extra_files" paths are resolved against this folder; "transforms"
patch individual files as they come out of a source archive.

Unless --no-sync is given, `fetch.py sync` runs first so every zip in packs/
matches the pinned version in upstream.lock.json.

"sources" maps each source zip (a filename in packs/) to a list of paths
inside it. A path may be a single entry or a folder (everything under it is
taken). An empty list or "*" takes the whole archive. A path prefixed with
"!" is excluded, and exclusions win over inclusions. A path (with or
without the "!") may start with "**/" to match its remainder as a basename
at any depth, e.g. "!**/.DS_Store" drops that file wherever it turns up in
the archive — for a single top-level directory (like a Mac zip's
"__MACOSX/"), a plain "!__MACOSX" already excludes the whole subtree without
needing "**/".

"extra_files" lists loose files to add after every archive, so they win any
conflict.

"transforms" patches one file as it comes out of a specific source archive,
so upstream packing mistakes (a typo'd filename, a needlessly huge texture,
a handful of bad lines in a text file) can be fixed without hand-editing a
copy of the file into the repo. Each entry needs "source" (a sources.json
key) and "path" (the file inside that archive), plus at least one of:
    "rename": <new path>          move the file within the merge
    "resize": [width, height]     re-encode the image at this size (needs
                                   ImageMagick's `magick` or `convert` on
                                   PATH)
    "patch": [{"find": <regex>, "replace": <string>}, ...]
                                   run each find/replace over the file's text
                                   (UTF-8), in order, via re.sub — "find" is
                                   a regular expression (use \\b for whole-
                                   word matches), "replace" may be "" to
                                   delete a match
A transform's file is taken regardless of that source's own include/exclude
paths, and is excluded from the normal merge under its original name (so a
rename never leaves the old, broken name behind too). It still participates
in later-source-wins like any other entry. build.py warns (without failing
the build) if a transform's path is missing from its source, or if a patch
rule's "find" matches nothing — both usually mean the upstream pack changed
and the transform needs updating.

    {
        "sources": {
            "base.zip": ["*", "!README.md"],
            "overlay.zip": ["assets/minecraft/textures/block/", "pack.png"]
        },
        "transforms": [
            {"source": "overlay.zip", "path": "a/typo d name.png",
             "rename": "a/typo_name.png"},
            {"source": "base.zip", "path": "a/huge_sprite.png",
             "resize": [512, 512]},
            {"source": "overlay.zip", "path": "a/data.properties",
             "patch": [{"find": "\\bold_id\\b", "replace": "new_id"},
                       {"find": "\\bredundant_id\\b", "replace": ""}]}
        ],
        "extra_files": ["./pack.mcmeta", "./pack.png"]
    }
"""

import json
import os
import re
import shutil
import subprocess
import sys
import zipfile

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(BASE, "sources.json")
PACKS = os.path.join(BASE, "packs")

_MAGICK = None


def _find_magick():
    """Path to an ImageMagick CLI (v7 'magick' preferred, v6 'convert' as fallback)."""
    global _MAGICK
    if _MAGICK is None:
        _MAGICK = next(
            (exe for exe in ("magick", "convert") if shutil.which(exe)), ""
        )
        if not _MAGICK:
            sys.exit(
                "error: a 'resize' transform needs ImageMagick "
                "('magick' or 'convert') on PATH"
            )
    return _MAGICK


def _resize_png(data, width, height):
    """Re-encode PNG bytes at width x height with a high-quality filter."""
    exe = _find_magick()
    args = [exe, "png:-", "-filter", "Lanczos", "-resize", f"{width}x{height}!",
            "-strip", "png:-"]
    result = subprocess.run(args, input=data, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE)
    if result.returncode != 0:
        raise RuntimeError(
            f"resize to {width}x{height} failed: "
            f"{result.stderr.decode(errors='replace').strip()}"
        )
    return result.stdout


def _apply_patch(data, rules):
    """(patched bytes, list of rules whose "find" matched nothing)."""
    text = data.decode("utf-8")
    misses = []
    for rule in rules:
        text, count = re.subn(rule["find"], rule["replace"], text)
        if count == 0:
            misses.append(rule["find"])
    return text.encode("utf-8"), misses


def _unique_keys(pairs):
    """Reject duplicate keys, which json would otherwise silently collapse."""
    seen = {}
    for key, value in pairs:
        if key in seen:
            raise ValueError(f"duplicate key: {key}")
        seen[key] = value
    return seen


def _matches(name, prefix):
    if prefix.startswith("**/"):
        basename = prefix[3:]
        return name == basename or name.endswith("/" + basename)
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


def validate(sources, transforms, extra_files):
    """Human-readable problems that would make a merge fail or silently do nothing."""
    if not isinstance(sources, dict):
        return ['"sources" must be an object mapping zip path -> list of paths']
    if not isinstance(transforms, list):
        return ['"transforms" must be a list of objects']
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

    for i, transform in enumerate(transforms):
        label = f"transforms[{i}]"
        if not isinstance(transform, dict):
            problems.append(f"{label}: must be an object")
            continue
        source = transform.get("source")
        path = transform.get("path")
        if not isinstance(source, str) or not source:
            problems.append(f'{label}: "source" must be a non-empty string')
        elif source not in sources:
            problems.append(f'{label}: "source" \'{source}\' is not a key in "sources"')
        if not isinstance(path, str) or not path:
            problems.append(f'{label}: "path" must be a non-empty string')
        rename = transform.get("rename")
        resize = transform.get("resize")
        patch = transform.get("patch")
        if rename is None and resize is None and patch is None:
            problems.append(f'{label}: needs "rename", "resize", "patch", or a combination')
        if rename is not None and not isinstance(rename, str):
            problems.append(f'{label}: "rename" must be a string')
        if resize is not None and (
            not isinstance(resize, list) or len(resize) != 2
            or not all(isinstance(n, int) and n > 0 for n in resize)
        ):
            problems.append(f'{label}: "resize" must be [width, height] of positive integers')
        if patch is not None:
            if not isinstance(patch, list) or not patch:
                problems.append(f'{label}: "patch" must be a non-empty list of {{find, replace}}')
            else:
                for j, rule in enumerate(patch):
                    rlabel = f"{label}.patch[{j}]"
                    if not isinstance(rule, dict):
                        problems.append(f"{rlabel}: must be an object")
                        continue
                    find = rule.get("find")
                    replace = rule.get("replace")
                    if not isinstance(find, str) or not find:
                        problems.append(f'{rlabel}: "find" must be a non-empty string')
                    else:
                        try:
                            re.compile(find)
                        except re.error as e:
                            problems.append(f'{rlabel}: "find" is not a valid regex: {e}')
                    if not isinstance(replace, str):
                        problems.append(f'{rlabel}: "replace" must be a string')

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


def _describe(transform):
    parts = []
    if "resize" in transform:
        parts.append(f"resized to {transform['resize'][0]}x{transform['resize'][1]}")
    if "patch" in transform:
        n = len(transform["patch"])
        parts.append(f"patched ({n} rule{'s' if n != 1 else ''})")
    if "rename" in transform:
        parts.append(f"renamed from {transform['path']}")
    return ", ".join(parts)


def merge(sources, transforms, extra_files, output):
    """sources: ordered mapping of zip path -> list of paths inside it."""
    entries = {}
    merged = []
    conflicts = []
    unused = []
    transformed = []
    missing_transforms = []
    patch_misses = []

    by_source = {}
    for transform in transforms:
        by_source.setdefault(transform["source"], {})[transform["path"]] = transform

    for archive, paths in sources.items():
        archive_transforms = by_source.pop(archive, {})
        with zipfile.ZipFile(os.path.join(PACKS, archive)) as zf:
            picked, unmatched = _selected(zf.namelist(), paths)
            unused += [(archive, path) for path in unmatched]
            for name in picked:
                if name.endswith("/") or name in archive_transforms:
                    continue
                _add(entries, merged, conflicts, name, archive, zf.read(name))

            for path, transform in archive_transforms.items():
                if path not in zf.namelist():
                    missing_transforms.append((archive, path))
                    continue
                data = zf.read(path)
                if "resize" in transform:
                    width, height = transform["resize"]
                    data = _resize_png(data, width, height)
                if "patch" in transform:
                    data, misses = _apply_patch(data, transform["patch"])
                    patch_misses += [(archive, path, find) for find in misses]
                target = transform.get("rename", path)
                _add(entries, merged, conflicts, target, archive, data)
                transformed.append((target, archive, _describe(transform)))

    # Any archive named only in transforms (no entry in "sources") never ran its loop.
    for archive, remaining in by_source.items():
        missing_transforms += [(archive, path) for path in remaining]

    for path in extra_files:
        with open(os.path.join(BASE, path), "rb") as f:
            data = f.read()
        name = os.path.normpath(path).replace(os.sep, "/")
        _add(entries, merged, conflicts, name, path, data)

    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as out:
        for name, (_, data) in entries.items():
            out.writestr(name, data)

    return entries, merged, conflicts, unused, transformed, missing_transforms, patch_misses


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
    transforms = config.get("transforms", [])
    extra_files = config.get("extra_files", [])

    problems = validate(sources, transforms, extra_files)
    if problems:
        sys.exit("\n".join(f"error: {p}" for p in problems))

    try:
        entries, merged, conflicts, unused, transformed, missing_transforms, patch_misses = merge(
            sources, transforms, extra_files, output
        )
    except OSError as e:
        sys.exit(f"error: {e}")

    for archive, path in unused:
        print(f"warning: {archive}: '{path}' matched nothing")

    for archive, path in missing_transforms:
        print(f"warning: transform on {archive}: '{path}' not found in archive")

    for archive, path, find in patch_misses:
        print(f"warning: patch on {archive}: '{path}': find /{find}/ matched nothing")

    for name, first, second in merged:
        print(f"merged: {name}\n  {first} + {second}")

    for name, loser, winner in conflicts:
        print(f"conflict: {name}\n  {loser} -> overwritten by {winner}")

    for name, archive, description in transformed:
        print(f"transform: {name}\n  {description} ({archive})")

    print(
        f"\n{len(entries)} entries written to {output} "
        f"({len(merged)} merged, {len(conflicts)} conflicts, {len(unused)} unused paths, "
        f"{len(transformed)} transformed, {len(patch_misses)} patch misses)"
    )


if __name__ == "__main__":
    main()
