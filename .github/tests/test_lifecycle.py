import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
TRAILER = "Co-authored-by: Copilot App <223556219+Copilot@users.noreply.github.com>"


class Lifecycle(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.work = self.path / "work"
        self.work.mkdir()
        self.remote = self.path / "origin.git"
        self.env = {
            **os.environ, "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull, "GIT_TERMINAL_PROMPT": "0",
        }
        for key in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME",
                    "GIT_COMMITTER_EMAIL", "GITHUB_SHA", "GITHUB_REF"):
            self.env.pop(key, None)
        self.git("init", "-q", "-b", "selected")
        for name in ("VERSION", "butteraugli/CMakeLists.txt", "butteraugli/Info.plist.in"):
            target = self.work / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / name, target)
        shutil.copytree(ROOT / ".github/scripts", self.work / ".github/scripts",
                        ignore=shutil.ignore_patterns("__pycache__"))
        self.git("add", ".")
        self.git("-c", "user.name=fixture", "-c", "user.email=fixture@example.com",
                 "commit", "-qm", "source fixture")
        self.base = self.git("rev-parse", "HEAD")
        self.git("init", "--bare", "-q", str(self.remote))
        self.git("remote", "add", "origin", str(self.remote))
        self.git("push", "-q", "origin", "HEAD:refs/heads/selected")
        self.env.update(GITHUB_SHA=self.base, GITHUB_REF="refs/heads/selected")

    def git(self, *args):
        return subprocess.check_output(["git", *args], cwd=self.work,
                                       env=self.env, text=True, stderr=subprocess.PIPE).strip()

    def bare(self, *args):
        return subprocess.check_output(
            ["git", "--git-dir", str(self.remote), *args],
            env=self.env, text=True, stderr=subprocess.PIPE,
        ).strip()

    def bump(self, success=True):
        result = subprocess.run(
            [sys.executable, ".github/scripts/bump.py", "--mode", "patch"],
            cwd=self.work, env=self.env, text=True, capture_output=True,
        )
        self.assertEqual(result.returncode == 0, success, result.stdout + result.stderr)
        return result

    def child(self, parent, message):
        tree = self.git("rev-parse", f"{self.base}^{{tree}}")
        return self.git("-c", "user.name=fixture", "-c", "user.email=fixture@example.com",
                        "commit-tree", tree, "-p", parent, "-m", message)

    def test_detached_no_identity_genuine_source_bump_no_tags(self):
        self.git("checkout", "--detach", "-q", self.base)
        self.git("config", "push.followTags", "true")
        self.git("-c", "user.name=fixture", "-c", "user.email=fixture@example.com",
                 "tag", "-a", "must-not-follow", "-m", "annotated")
        self.bump()
        new = self.git("rev-parse", "HEAD")
        self.assertEqual(self.git("rev-list", "--parents", "-n", "1", new).split(),
                         [new, self.base])
        self.assertEqual(self.bare("rev-parse", "refs/heads/selected"), new)
        changed = self.git("diff", "--name-only", self.base, new).splitlines()
        self.assertEqual(set(changed), {"VERSION", "butteraugli/CMakeLists.txt"})
        self.assertTrue(self.git("log", "-1", "--format=%B").endswith(TRAILER))
        self.assertEqual(self.git("status", "--porcelain"), "")
        self.assertEqual(self.bare("tag", "--list"), "")

    def test_empty_advertisement_and_multiple_updates_fail(self):
        child = self.child(self.base, "child")
        self.git("checkout", "--detach", "-q", child)
        env = {**self.env, "BUMP_BASE": self.base, "BUMP_REF": "refs/heads/selected"}
        valid = f"HEAD {child} refs/heads/selected {self.base}\n"
        for data in ("", valid + valid, valid.replace("HEAD", child, 1),
                     valid.replace("refs/heads/selected", "refs/heads/other"),
                     valid.replace(self.base, "0" * 40)):
            with self.subTest(data=data):
                result = subprocess.run(
                    [str(self.work / ".github/scripts/bump-hooks/pre-push")],
                    cwd=self.work, env=env, input=data, text=True, capture_output=True,
                )
                self.assertNotEqual(result.returncode, 0)

    def test_actual_advertisement_races_advance_rewind_delete_same_result(self):
        original = self.base
        older = self.child(original, "older")
        base = self.child(older, "base")
        new = self.child(base, "new")
        advance = self.child(base, "advance")
        self.git("checkout", "--detach", "-q", new)
        self.git("push", "-q", "origin", f"{new}:refs/heads/object-seed")
        self.git("push", "-q", "origin", f"{advance}:refs/heads/advance-seed")
        for label, remote_value in (("advance", advance), ("rewind", older),
                                    ("delete", None), ("same-result", new)):
            with self.subTest(race=label):
                self.bare("update-ref", "refs/heads/selected", base)
                self.assertEqual(self.git("ls-remote", "--refs", "origin",
                                         "refs/heads/selected").split(), [base, "refs/heads/selected"])
                if remote_value:
                    self.bare("update-ref", "refs/heads/selected", remote_value)
                else:
                    self.bare("update-ref", "-d", "refs/heads/selected")
                result = subprocess.run(
                    ["git", "-c", "push.followTags=false", "-c",
                     f"core.hooksPath={self.work / '.github/scripts/bump-hooks'}",
                     "push", "--no-follow-tags", "origin", "HEAD:refs/heads/selected"],
                    cwd=self.work, env={**self.env, "BUMP_BASE": base,
                                        "BUMP_REF": "refs/heads/selected"},
                    text=True, capture_output=True,
                )
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("pre-push:", result.stderr)
                advertised = self.git("ls-remote", "--refs", "origin", "refs/heads/selected").split()
                self.assertEqual(advertised, [] if remote_value is None else
                                 [remote_value, "refs/heads/selected"])

    def test_merge_or_multiple_commits_rejected(self):
        first = self.child(self.base, "one")
        second = self.child(first, "two")
        self.git("checkout", "--detach", "-q", second)
        result = subprocess.run(
            [str(self.work / ".github/scripts/bump-hooks/pre-push")],
            cwd=self.work, env={**self.env, "BUMP_BASE": self.base,
                                "BUMP_REF": "refs/heads/selected"},
            input=f"HEAD {second} refs/heads/selected {self.base}\n",
            text=True, capture_output=True,
        )
        self.assertNotEqual(result.returncode, 0)

    def test_merge_commit_with_selected_parent_is_rejected(self):
        other = self.child(self.base, "other")
        tree = self.git("rev-parse", f"{self.base}^{{tree}}")
        merge = self.git("-c", "user.name=fixture", "-c", "user.email=fixture@example.com",
                         "commit-tree", tree, "-p", self.base, "-p", other, "-m", "merge")
        self.git("checkout", "--detach", "-q", merge)
        result = subprocess.run(
            [str(self.work / ".github/scripts/bump-hooks/pre-push")],
            cwd=self.work, env={**self.env, "BUMP_BASE": self.base,
                                "BUMP_REF": "refs/heads/selected"},
            input=f"HEAD {merge} refs/heads/selected {self.base}\n",
            text=True, capture_output=True,
        )
        self.assertNotEqual(result.returncode, 0)

    def test_dirty_and_hidden_source_fail_before_writes(self):
        for flag in (None, "--assume-unchanged", "--skip-worktree"):
            with self.subTest(flag=flag):
                path = self.work / "butteraugli/CMakeLists.txt"
                before = path.read_bytes()
                if flag:
                    self.git("update-index", flag, "butteraugli/CMakeLists.txt")
                path.write_bytes(before + b"\n# dirty input\n")
                self.bump(success=False)
                self.assertEqual(self.git("rev-parse", "HEAD"), self.base)
                self.assertEqual(self.bare("rev-parse", "refs/heads/selected"), self.base)
                path.write_bytes(before)
                if flag:
                    self.git("update-index", "--no-assume-unchanged", "--no-skip-worktree",
                             "butteraugli/CMakeLists.txt")

    def test_moved_branch_fails_before_source_writes(self):
        self.bare("update-ref", "-d", "refs/heads/selected")
        before = (self.work / "VERSION").read_bytes()
        self.bump(success=False)
        self.assertEqual((self.work / "VERSION").read_bytes(), before)
        self.assertEqual(self.git("rev-parse", "HEAD"), self.base)

    def test_staged_then_reversed_source_fails_before_writes(self):
        source = self.work / "butteraugli/CMakeLists.txt"
        original = source.read_bytes()
        source.write_bytes(original + b"\n# staged input\n")
        self.git("add", "butteraugli/CMakeLists.txt")
        source.write_bytes(original)
        version = (self.work / "VERSION").read_bytes()
        self.bump(success=False)
        self.assertEqual((self.work / "VERSION").read_bytes(), version)
        self.assertEqual(self.git("rev-parse", "HEAD"), self.base)
        self.assertEqual(self.bare("rev-parse", "refs/heads/selected"), self.base)

    def test_tag_selection_fails_before_writes(self):
        self.env["GITHUB_REF"] = "refs/tags/not-a-branch"
        before = (self.work / "VERSION").read_bytes()
        self.bump(success=False)
        self.assertEqual((self.work / "VERSION").read_bytes(), before)
        self.assertEqual(self.git("rev-parse", "HEAD"), self.base)

    def test_guard_losing_executable_bit_after_last_read_blocks_push(self):
        wrapper_dir = self.path / "bin"
        wrapper_dir.mkdir()
        real_git = shutil.which("git")
        counter = self.path / "reads"
        hook = self.work / ".github/scripts/bump-hooks/pre-push"
        wrapper = wrapper_dir / "git"
        wrapper.write_text(
            f"#!{sys.executable}\n"
            "import os, pathlib, sys\n"
            f"counter = pathlib.Path({str(counter)!r})\n"
            "if len(sys.argv) > 1 and sys.argv[1] == 'ls-remote':\n"
            "    count = int(counter.read_text()) + 1 if counter.exists() else 1\n"
            "    counter.write_text(str(count))\n"
            "    if count == 2:\n"
            f"        pathlib.Path({str(hook)!r}).chmod(0o644)\n"
            f"os.execv({real_git!r}, [{real_git!r}, *sys.argv[1:]])\n"
        )
        wrapper.chmod(0o755)
        self.env["PATH"] = str(wrapper_dir) + os.pathsep + self.env["PATH"]
        result = self.bump(success=False)
        self.assertIn("guard changed before push", result.stderr)
        self.assertEqual(self.bare("rev-parse", "refs/heads/selected"), self.base)


if __name__ == "__main__":
    unittest.main()
