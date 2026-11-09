"""Immutable pre-change contracts for the native build and unrelated 4D tests."""

from pathlib import Path
import re
import subprocess
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[2]
ORIGINAL = "c2855e5e81b936611bc06a4f5579ccaf2d9c8579"


def original(path):
    return subprocess.check_output(
        ["git", "show", f"{ORIGINAL}:{path}"], cwd=ROOT
    )


def load(path):
    # BaseLoader keeps the Actions "on" key a string rather than YAML 1.1 true.
    return yaml.load((ROOT / path).read_text(), Loader=yaml.BaseLoader)


class RepositoryContractTests(unittest.TestCase):
    def test_unrelated_test_workflow_only_adds_checkout_ref_and_safe_quoting(self):
        path = ".github/workflows/test.yml"
        expected = yaml.load(original(path), Loader=yaml.BaseLoader)
        for step in expected["jobs"]["test"]["steps"]:
            if step.get("uses", "").startswith("actions/checkout@"):
                step.setdefault("with", {})["ref"] = "${{ github.sha }}"
            if step.get("name") == "Parse 4D version from .4DProject":
                step["run"] = step["run"].replace(
                    ">> $GITHUB_OUTPUT", '>> "$GITHUB_OUTPUT"'
                )
            elif step.get("name") == "Run tests (macOS)":
                step["run"] = step["run"].replace(
                    "--project=$(pwd)/butteraugli/butteraugli-test/Project/butteraugli.4DProject",
                    '--project="$(pwd)/butteraugli/butteraugli-test/Project/butteraugli.4DProject"',
                )
        self.assertEqual(load(path), expected)

    def test_cmake_preserves_original_native_contract_except_marketing_version(self):
        path = "butteraugli/CMakeLists.txt"
        pattern = r'(MACOSX_BUNDLE_SHORT_VERSION_STRING\s+")[^"]+(")'
        current = (ROOT / path).read_text()
        baseline = original(path).decode()
        self.assertEqual(len(re.findall(pattern, current)), 1)
        normalize = lambda text: re.sub(pattern, r"\g<1>MARKETING_VERSION\2", text)
        self.assertEqual(normalize(current), normalize(baseline))

    def test_4d_project_and_dependency_declarations_preserved(self):
        self.assertEqual((ROOT / ".gitmodules").read_bytes(), original(".gitmodules"))
        dependencies = ("deps/4D-Plugin-SDK", "deps/butteraugli")
        baseline = subprocess.check_output(
            ["git", "ls-tree", ORIGINAL, *dependencies], cwd=ROOT, text=True,
        )
        current = subprocess.check_output(
            ["git", "ls-tree", "HEAD", *dependencies], cwd=ROOT, text=True,
        )
        self.assertEqual(current, baseline)
        files = subprocess.check_output(
            ["git", "ls-tree", "-r", "--name-only", ORIGINAL,
             "butteraugli/butteraugli-test"], cwd=ROOT, text=True,
        ).splitlines()
        for path in files:
            if "/Plugins/" not in path:
                with self.subTest(path=path):
                    self.assertEqual((ROOT / path).read_bytes(), original(path))

    def test_exact_manual_workflow_interfaces(self):
        bump = load(".github/workflows/bump-version.yml")
        build = load(".github/workflows/release.yml")
        self.assertEqual(bump["name"], "Bump")
        self.assertEqual(build["name"], "Build")
        self.assertEqual(set(bump["on"]), {"workflow_dispatch"})
        self.assertEqual(set(build["on"]), {"workflow_dispatch"})
        self.assertIn(build["on"]["workflow_dispatch"], ("", None, {}))
        dispatch = bump["on"]["workflow_dispatch"]
        self.assertEqual(set(dispatch), {"inputs"})
        self.assertEqual(set(dispatch["inputs"]), {"mode"})
        mode = dispatch["inputs"]["mode"]
        self.assertEqual(mode["type"], "choice")
        self.assertEqual(mode["required"], "true")
        self.assertEqual(mode["options"], ["patch", "minor", "major"])
        self.assertEqual(mode.get("default"), "patch")

    def test_all_checkouts_use_the_selected_immutable_sha(self):
        count = 0
        for path in (".github/workflows/bump-version.yml",
                     ".github/workflows/release.yml", ".github/workflows/test.yml"):
            for job in load(path)["jobs"].values():
                for step in job.get("steps", []):
                    if step.get("uses", "").startswith("actions/checkout@"):
                        count += 1
                        self.assertEqual(step["with"]["ref"], "${{ github.sha }}")
        self.assertEqual(count, 5)

    def test_constant_concurrency_never_cancels_publication(self):
        concurrency = load(".github/workflows/release.yml")["concurrency"]
        self.assertEqual(set(concurrency), {"group", "cancel-in-progress"})
        self.assertTrue(concurrency["group"].strip())
        self.assertNotIn("${{", concurrency["group"])
        self.assertEqual(concurrency["cancel-in-progress"], "false")

    def test_actual_windows_then_macos_topology_and_native_settings(self):
        path = ".github/workflows/release.yml"
        release = load(path)
        baseline = yaml.load(original(path), Loader=yaml.BaseLoader)
        jobs = release["jobs"]
        self.assertEqual(set(jobs), {"version", "build-windows", "build-macos"})
        self.assertEqual(jobs["build-windows"]["needs"], "version")
        self.assertEqual(jobs["build-macos"]["needs"], ["version", "build-windows"])
        expected_env = dict(baseline["env"])
        if "PYTHONDONTWRITEBYTECODE" in release["env"]:
            expected_env["PYTHONDONTWRITEBYTECODE"] = "1"
        self.assertEqual(release["env"], expected_env)
        for job_name in ("build-windows", "build-macos"):
            job = jobs[job_name]
            old = baseline["jobs"][job_name]
            self.assertEqual(job["runs-on"], old["runs-on"])
            self.assertNotIn("if", job)
            checkout = next(s for s in job["steps"] if s.get("uses", "").startswith("actions/checkout@"))
            self.assertEqual(checkout["with"]["submodules"], "recursive")
            self.assertEqual(checkout["with"]["fetch-depth"], "0")
            for step in job["steps"]:
                self.assertNotIn("if", step)
        unchanged = (
            ("build-windows", "upload windows binaries"),
            ("build-macos", "download windows binaries"),
            ("build-macos", "merge Windows binaries into the bundle"),
        )
        for job, name in unchanged:
            current = next(s for s in jobs[job]["steps"] if s.get("name") == name)
            previous = next(s for s in baseline["jobs"][job]["steps"] if s.get("name") == name)
            self.assertEqual(current, previous)
        mac = {s.get("name"): s for s in jobs["build-macos"]["steps"]}
        order = list(mac)
        for first, second in zip(
            ("build macOS plugin (universal)", "verify universal binary",
             "merge Windows binaries into the bundle", "codesign", "package, notarize & staple"),
            ("verify universal binary", "merge Windows binaries into the bundle",
             "codesign", "package, notarize & staple", "create GitHub Release"),
        ):
            self.assertLess(order.index(first), order.index(second))
        signing = mac["codesign"]["run"]
        for flag in ("--deep", "--timestamp", "--force", "--options=runtime", "--sign", "--verify", "--strict"):
            self.assertIn(flag, signing)
        packaging = mac["package, notarize & staple"]["run"]
        for token in ("ditto -c -k --keepParent", "hdiutil create -format UDBZ",
                      "notarytool submit", "--wait", "stapler staple"):
            self.assertIn(token, packaging)

    def test_no_invented_default_branch_gate(self):
        for path in (".github/workflows/bump-version.yml", ".github/workflows/release.yml"):
            text = (ROOT / path).read_text()
            for forbidden in ("default_branch", "refs/heads/main", "refs/heads/master"):
                self.assertNotIn(forbidden, text)
            for job in load(path)["jobs"].values():
                self.assertNotIn("if", job)
                for step in job["steps"]:
                    self.assertNotIn("if", step)


if __name__ == "__main__":
    unittest.main()
