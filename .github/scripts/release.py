#!/usr/bin/env python3
"""Fail-closed, create-only publication of the selected source commit."""

import argparse
import json
import os
from pathlib import Path
import plistlib
import re
import stat
import subprocess
import sys
from urllib.parse import quote
import xml.etree.ElementTree as ET

sys.dont_write_bytecode = True
from source_version import read_version, validate_bundle


BUNDLE = "butteraugli/butteraugli-test/Plugins/butteraugli.bundle"
CONTENTS = BUNDLE + "/Contents/"
GENERATED = frozenset(CONTENTS + name for name in (
    "Info.plist", "MacOS/butteraugli", "Resources/constants.xlf",
    "Resources/manifest.json", "Windows64/butteraugli.4DX",
    "_CodeSignature/CodeResources",
)) | frozenset(
    "butteraugli/butteraugli-test/Plugins/butteraugli/Contents/" + name
    for name in ("Windows64/butteraugli.4DX", "Resources/constants.xlf",
                 "Resources/manifest.json")
)


class ReleaseError(Exception):
    pass


def require(condition, message):
    if not condition:
        raise ReleaseError(message)


def run(root, *args, data=None):
    result = subprocess.run(args, cwd=root, input=data, capture_output=True)
    require(result.returncode == 0,
            f"{args[0]} failed: {result.stderr.decode(errors='replace').strip()}")
    return result.stdout


def git(root, *args, data=None):
    return run(root, "git", *args, data=data)


def generated(path, version):
    return (path in GENERATED
            or path.startswith("butteraugli/cmake-build/")
            or path == "winbuild/butteraugli.4DX"
            or path in (f"butteraugli-{version}.zip", f"butteraugli-{version}.dmg"))


def snapshot(root, sha, version, final=False):
    require(git(root, "rev-parse", "HEAD").decode().strip() == sha,
            "HEAD does not match selected GITHUB_SHA")
    flags = git(root, "ls-files", "-v", "-z").split(b"\0")
    require(all(not entry or (entry[:1] != b"S" and not entry[:1].islower())
                for entry in flags), "Hidden index flags (skip-worktree/assume-unchanged)")
    tree = {}
    for entry in git(root, "ls-tree", "-r", "-z", sha).split(b"\0"):
        if entry:
            metadata, path = entry.split(b"\t", 1)
            mode, kind, oid = metadata.decode().split()
            tree[os.fsdecode(path)] = (mode, kind, oid)
    index = {}
    for entry in git(root, "ls-files", "--stage", "-z").split(b"\0"):
        if entry:
            metadata, path = entry.split(b"\t", 1)
            mode, oid, stage = metadata.decode().split()
            require(stage == "0", "Unmerged index")
            index[os.fsdecode(path)] = (mode, oid)
    # Inspect the index independently: a staged change reversed in the worktree
    # must not disappear behind a clean worktree comparison.
    raw = git(root, "diff", "--cached", "--raw", "--no-renames", "-z", sha, "--")
    fields = raw.split(b"\0")
    for offset in range(0, len(fields) - 1, 2):
        path = os.fsdecode(fields[offset + 1])
        require(final and path in GENERATED, f"Staged input differs: {path}")
    for path in tree.keys() | index.keys():
        if final and path in GENERATED:
            continue
        expected = tree.get(path)
        require(expected is not None and index.get(path) == (expected[0], expected[2]),
                f"Index differs from selected tree: {path}")
        file = root / path
        require(file.exists() or file.is_symlink(), f"Deleted tracked input: {path}")
        mode = file.lstat().st_mode
        if expected[1] == "commit":
            require(stat.S_ISDIR(mode), f"Invalid submodule: {path}")
            require(git(file, "rev-parse", "HEAD").decode().strip() == expected[2],
                    f"Submodule HEAD differs: {path}")
            require(not git(file, "status", "--porcelain", "--untracked-files=all"),
                    f"Dirty submodule: {path}")
            continue
        if expected[0] == "120000":
            require(stat.S_ISLNK(mode), f"Changed tracked file type: {path}")
            oid = git(root, "hash-object", "--stdin",
                      data=os.fsencode(os.readlink(file))).decode().strip()
        else:
            require(stat.S_ISREG(mode), f"Changed tracked file type: {path}")
            actual_mode = "100755" if mode & 0o111 else "100644"
            require(actual_mode == expected[0], f"Changed tracked mode: {path}")
            oid = git(root, "hash-object", "--no-filters", "--", path).decode().strip()
        require(oid == expected[2], f"Tracked input differs from selected tree: {path}")
    others = git(root, "ls-files", "--others", "--exclude-standard", "-z")
    ignored = git(root, "ls-files", "--others", "--ignored", "--exclude-standard", "-z")
    for encoded in others.split(b"\0"):
        if encoded:
            path = os.fsdecode(encoded)
            require(final and generated(path, version), f"Unexpected untracked file: {path}")
    for encoded in ignored.split(b"\0"):
        if encoded:
            path = os.fsdecode(encoded)
            require(final and generated(path, version),
                    f"Unexpected ignored file: {path}")


def json_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "Duplicate JSON object key")
        result[key] = value
    return result


def invalid_constant(value):
    raise ReleaseError(f"Invalid JSON constant: {value}")


class GitHub:
    def __init__(self, root, repository):
        self.root, self.repository = root, repository

    def api(self, method, endpoint, payload=None, asset=None):
        command = ["gh", "api", "--method", method, "--hostname",
                   "uploads.github.com" if asset else "github.com", endpoint,
                   "--header", "Accept: application/vnd.github+json",
                   "--header", "X-GitHub-Api-Version: 2022-11-28"]
        data = None
        if payload is not None:
            command += ["--input", "-"]
            data = json.dumps(payload).encode()
        if asset is not None:
            command += ["--header", "Content-Type: application/octet-stream",
                        "--input", str(asset)]
        output = run(self.root, *command, data=data)
        try:
            return json.loads(output, object_pairs_hook=json_object,
                              parse_constant=invalid_constant)
        except (ValueError, UnicodeError) as error:
            raise ReleaseError(f"Malformed JSON from {method} {endpoint}") from error


def numeric_id(value):
    return type(value) is int and value > 0


def remote_ref(root, tag):
    ref = f"refs/tags/{tag}"
    output = git(root, "ls-remote", "--refs", "origin", ref).decode().strip()
    if not output:
        return None
    lines = output.splitlines()
    require(len(lines) == 1, "Malformed remote tag response")
    fields = lines[0].split()
    require(len(fields) == 2 and fields[1] == ref
            and re.fullmatch(r"[0-9a-f]{40}", fields[0]),
            "Malformed exact remote tag response")
    return fields[0]


def available(api, tag):
    ids, occupied = set(), False
    page = 1
    while True:
        releases = api.api("GET", f"/repos/{api.repository}/releases?per_page=100&page={page}")
        require(isinstance(releases, list) and len(releases) <= 100,
                f"Malformed releases page {page}")
        for release in releases:
            require(isinstance(release, dict) and numeric_id(release.get("id"))
                    and isinstance(release.get("tag_name"), str)
                    and bool(release["tag_name"]) and type(release.get("draft")) is bool,
                    f"Malformed release entry on page {page}")
            require(release["id"] not in ids, "Duplicate release ID in pagination")
            ids.add(release["id"])
            occupied |= release["tag_name"] == tag
        if not releases:
            break
        page += 1
    require(not occupied, f"{tag} already has a release (including drafts)")
    require(remote_ref(api.root, tag) is None, f"{tag} already exists remotely")
    return ids


def publish(api, version, sha):
    tag = f"v{version}"
    paths = [api.root / f"butteraugli-{version}.{suffix}" for suffix in ("zip", "dmg")]
    for path in paths:
        require(path.is_file() and not path.is_symlink() and path.stat().st_size > 0,
                f"Missing, empty or nonregular release asset: {path.name}")
    validate_bundle(api.root / BUNDLE, api.root)
    snapshot(api.root, sha, version, final=True)
    existing_ids = available(api, tag)
    ref = api.api("POST", f"/repos/{api.repository}/git/refs",
                  {"ref": f"refs/tags/{tag}", "sha": sha})
    require(isinstance(ref, dict) and ref.get("ref") == f"refs/tags/{tag}"
            and isinstance(ref.get("object"), dict) and ref["object"].get("sha") == sha,
            "New tag response differs from selected ref/SHA")
    require(remote_ref(api.root, tag) == sha, "New remote tag differs from selected SHA")
    print(f"Created tag {tag} at {sha}; retained on later failure", flush=True)
    release = api.api("POST", f"/repos/{api.repository}/releases", {
        "tag_name": tag, "name": tag, "target_commitish": sha,
        "draft": False, "prerelease": False, "body": "", "generate_release_notes": False,
    })
    require(isinstance(release, dict) and numeric_id(release.get("id"))
            and release["id"] not in existing_ids, "Malformed or reused new release ID")
    require(release.get("tag_name") == tag and release.get("name") == tag
            and release.get("target_commitish") == sha
            and release.get("draft") is False and release.get("prerelease") is False,
            "New release response differs from requested release")
    release_id = release["id"]
    print(f"Created release ID {release_id} for {tag}; retained on later failure", flush=True)
    asset_ids = set()
    for path in paths:
        result = api.api(
            "POST", f"/repos/{api.repository}/releases/{release_id}/assets?name="
            + quote(path.name, safe=""), asset=path,
        )
        require(isinstance(result, dict) and numeric_id(result.get("id"))
                and result["id"] not in asset_ids and result.get("name") == path.name
                and type(result.get("size")) is int and result["size"] == path.stat().st_size
                and result.get("state") == "uploaded",
                f"Malformed upload response for {path.name}")
        asset_ids.add(result["id"])
    print(f"Published {tag}: release ID {release_id}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("preflight", "publish"))
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args(argv)
    try:
        root = args.root.resolve()
        sha = os.environ.get("GITHUB_SHA", "")
        repository = os.environ.get("GITHUB_REPOSITORY", "")
        require(re.fullmatch(r"[0-9a-f]{40}", sha), "Invalid selected GITHUB_SHA")
        require(re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository)
                and all(part not in (".", "..") for part in repository.split("/")),
                "Invalid GITHUB_REPOSITORY")
        require(os.environ.get("GH_TOKEN", "").strip(), "Explicit GH_TOKEN is required")
        require(git(root, "rev-parse", "HEAD").decode().strip() == sha,
                "HEAD does not match selected GITHUB_SHA")
        version = read_version(root)
        require(re.fullmatch(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)",
                             version), "Noncanonical source version")
        snapshot(root, sha, version, final=args.command == "publish")
        api = GitHub(root, repository)
        if args.command == "preflight":
            available(api, f"v{version}")
            if os.environ.get("GITHUB_OUTPUT"):
                with open(os.environ["GITHUB_OUTPUT"], "a") as stream:
                    stream.write(f"version={version}\ntag=v{version}\n")
            print(f"Preflight passed: v{version} at {sha}")
        else:
            publish(api, version, sha)
    except (ReleaseError, ValueError, OSError, UnicodeError, ET.ParseError,
            plistlib.InvalidFileException) as error:
        print(f"release: {error}. Stopped without retry, update, deletion or rollback. "
              "Any remote tag, release and uploaded assets are retained; "
              "manual recovery is required for partial publication.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
