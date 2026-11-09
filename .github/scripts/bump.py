#!/usr/bin/env python3
"""Commit source metadata once, then push only that direct child."""
import argparse
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

sys.dont_write_bytecode = True
import source_version

TRAILER = "Co-authored-by: Copilot App <223556219+Copilot@users.noreply.github.com>"


def git(*args):
    return subprocess.check_output(["git", *args], text=True).strip()


def clean_selected(base):
    if git("status", "--porcelain", "--untracked-files=all"):
        raise ValueError("Bump requires a clean checkout")
    if git("diff", "--cached", "--name-only", base):
        raise ValueError("Bump index differs from selected SHA")
    for row in git("ls-tree", "-r", base).splitlines():
        metadata, path = row.split("\t", 1)
        mode, kind, expected = metadata.split()
        if kind != "blob":
            raise ValueError(f"unsupported source tree entry: {path}")
        file = Path(path)
        if mode == "120000":
            actual = subprocess.check_output(
                ["git", "hash-object", "--stdin"], input=os.readlink(file).encode()
            ).decode().strip()
        else:
            if not file.is_file() or file.is_symlink():
                raise ValueError(f"missing or replaced tracked input: {path}")
            actual = git("hash-object", "--no-filters", "--", path)
            executable = bool(file.stat().st_mode & stat.S_IXUSR)
            if executable != (mode == "100755"):
                raise ValueError(f"tracked input mode changed: {path}")
        if actual != expected:
            raise ValueError(f"tracked input bytes changed: {path}")


def run(mode):
    base = os.environ["GITHUB_SHA"]
    ref = os.environ["GITHUB_REF"]
    if not re.fullmatch(r"[0-9a-f]{40}", base) or not ref.startswith("refs/heads/"):
        raise ValueError("Bump requires an immutable SHA and selected branch")
    git("check-ref-format", ref)
    if git("rev-parse", "HEAD") != base:
        raise ValueError("checkout does not match selected SHA")
    clean_selected(base)
    advertised = git("ls-remote", "--refs", "origin", ref).split()
    if advertised != [base, ref]:
        raise ValueError("selected branch no longer advertises selected SHA")
    hook = Path(__file__).resolve().parent / "bump-hooks" / "pre-push"
    if not hook.is_file() or not os.access(hook, os.X_OK):
        raise ValueError("executable pre-push guard is missing")
    guard_bytes = hook.read_bytes()
    version = source_version.bump(mode)
    changed = git("diff", "--name-only").splitlines()
    if not changed or not set(changed) <= set(source_version.SOURCE_PATHS):
        raise ValueError("generated diff is empty or outside source allowlist")
    git("add", "--", *changed)
    git("-c", "user.name=github-actions[bot]",
        "-c", "user.email=github-actions[bot]@users.noreply.github.com",
        "commit", "-m", f"chore: bump version to {version}\n\n{TRAILER}")
    new = git("rev-parse", "HEAD")
    if git("rev-list", "--parents", "-n", "1", new).split() != [new, base]:
        raise ValueError("Bump must create exactly one direct child")
    if git("status", "--porcelain", "--untracked-files=all"):
        raise ValueError("generated commit left unexpected changes")
    if git("ls-remote", "--refs", "origin", ref).split() != [base, ref]:
        raise ValueError("selected branch advanced before push")
    if not hook.is_file() or not os.access(hook, os.X_OK) or hook.read_bytes() != guard_bytes:
        raise ValueError("executable pre-push guard changed before push")
    env = {**os.environ, "BUMP_BASE": base, "BUMP_REF": ref}
    subprocess.run(
        ["git", "-c", "push.followTags=false", "-c",
         f"core.hooksPath={hook.parent}", "push", "--no-follow-tags",
         "origin", f"HEAD:{ref}"], env=env, check=True,
    )
    if git("ls-remote", "--refs", "origin", ref).split() != [new, ref]:
        raise ValueError("remote branch did not retain generated commit")
    print(version)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("patch", "minor", "major"), required=True)
    args = parser.parse_args()
    try:
        run(args.mode)
    except (ValueError, KeyError, OSError, subprocess.CalledProcessError) as error:
        print(f"Bump failed: {error}", file=sys.stderr)
        sys.exit(1)
