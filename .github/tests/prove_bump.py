#!/usr/bin/env python3
"""Prove a genuine guarded bump from an exact migration commit, entirely offline."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
TRAILER = "Co-authored-by: Copilot App <223556219+Copilot@users.noreply.github.com>"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("migration_sha")
    args = parser.parse_args()
    migration = args.migration_sha
    env = {
        **os.environ, "PYTHONDONTWRITEBYTECODE": "1",
        "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "safe.bareRepository",
        "GIT_CONFIG_VALUE_0": "explicit",
    }
    for key in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME",
                "GIT_COMMITTER_EMAIL"):
        env.pop(key, None)

    def run(*command, cwd=ROOT, capture=False, extra=None):
        result = subprocess.run(command, cwd=cwd, env={**env, **(extra or {})},
                                text=True, check=True,
                                stdout=subprocess.PIPE if capture else None)
        return result.stdout.strip() if capture else None

    if run("git", "rev-parse", "HEAD", capture=True) != migration:
        raise ValueError("proof must start from exact final migration HEAD")
    assert run("git", "config", "--get", "safe.bareRepository", capture=True) == "explicit"
    if run("git", "status", "--porcelain", capture=True):
        raise ValueError("migration checkout must be clean")
    with tempfile.TemporaryDirectory(prefix="butteraugli-genuine-bump-") as directory:
        root = Path(directory)
        work, bare = root / "work", root / "origin.git"
        run("git", "init", "--bare", "-q", str(bare))
        run("git", "clone", "--no-local", "--no-hardlinks", "--no-tags",
            "--no-checkout", "--quiet", str(ROOT), str(work))
        run("git", "remote", "set-url", "origin", str(bare), cwd=work)
        run("git", "checkout", "--detach", "--quiet", migration, cwd=work)
        run("git", "-c", "push.followTags=false", "push", "--no-follow-tags",
            "--quiet", "origin", "HEAD:refs/heads/proof", cwd=work)
        run(sys.executable, ".github/scripts/bump.py", "--mode", "patch",
            cwd=work, extra={"GITHUB_SHA": migration, "GITHUB_REF": "refs/heads/proof"})
        bumped = run("git", "rev-parse", "HEAD", cwd=work, capture=True)
        advertised = run("git", "--git-dir", str(bare), "rev-parse",
                         "refs/heads/proof", capture=True)
        assert advertised == bumped
        assert run("git", "rev-list", "--parents", "-n", "1", bumped,
                   cwd=work, capture=True).split() == [bumped, migration]
        changed = run("git", "diff", "--name-only", migration, bumped,
                      cwd=work, capture=True).splitlines()
        assert set(changed) == {"VERSION", "butteraugli/CMakeLists.txt"}, changed
        assert run("git", "log", "-1", "--format=%B", cwd=work,
                   capture=True).endswith(TRAILER)
        assert not run("git", "tag", "--list", cwd=work, capture=True)
        assert not run("git", "--git-dir", str(bare), "tag", "--list", capture=True)
        assert not run("git", "status", "--porcelain", cwd=work, capture=True)
        run(sys.executable, "-m", "unittest", "discover", "-s", ".github/tests",
            "-v", cwd=work)
        run("actionlint", cwd=work)
        assert not run("git", "status", "--porcelain", cwd=work, capture=True)
        assert run("git", "rev-parse", "HEAD", cwd=work, capture=True) == bumped
        assert run("git", "--git-dir", str(bare), "rev-parse",
                   "refs/heads/proof", capture=True) == bumped
        print(f"MIGRATION_SHA={migration}")
        print(f"GENUINE_BUMP_SHA={bumped}")
        print(f"SOURCE_PATHS={','.join(changed)}")
        print("SAFE_BARE_REPOSITORY=explicit")
        print("CLEAN_LOCAL_EQUALS_BARE=true; DIRECT_PARENT=true; LOCAL_REMOTE_TAGS=0")
    assert not root.exists()
    print("Disposable proof checkout and bare origin removed")


if __name__ == "__main__":
    main()
