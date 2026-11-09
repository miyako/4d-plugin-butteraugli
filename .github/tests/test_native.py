"""Execute the release workflow's native steps with a closed, fake toolchain."""

import json
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import sys
import unittest
import uuid

import yaml


ROOT = Path(__file__).resolve().parents[2]
BUNDLE = Path("butteraugli/butteraugli-test/Plugins/butteraugli.bundle")
WINDOWS = Path("butteraugli/butteraugli-test/Plugins/butteraugli")
PWSH = shutil.which("pwsh")
BASH = shutil.which("bash")


def workflow():
    return yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text())


def run_text(job, name, version):
    steps = workflow()["jobs"][job]["steps"]
    text = next(step["run"] for step in steps if step.get("name") == name)
    values = {
        "env.PRODUCT_NAME": "butteraugli",
        "env.MACOS_DEPLOYMENT_TARGET": workflow()["env"]["MACOS_DEPLOYMENT_TARGET"],
        "needs.version.outputs.version": version,
    }

    def replace(match):
        key = match[1].strip()
        if key not in values:
            raise AssertionError(f"unexpected workflow expression: {key}")
        return values[key]

    return re.sub(r"\$\{\{(.*?)\}\}", replace, text)


# Every external command visible to the workflow is this dispatcher. No command
# forwards to an installed native tool, even when a test introduces a new call.
STUB = r'''
import json, os, pathlib, plistlib, shutil, sys
p = pathlib.Path
root = p(os.environ["FIXTURE_ROOT"])
name = p(sys.argv[0]).name
args = sys.argv[1:]
with (root / "calls.jsonl").open("a") as log:
    log.write(json.dumps([name] + args) + "\n")
def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
def fail(key):
    if os.environ.get(key) == "1":
        sys.exit(7)
if name == "cmake":
    build = "--build" in args
    if not build:
        contents = root / os.environ["OUTPUT_CONTENTS"]
        generated = ["Windows64/butteraugli.4DX", "Resources/manifest.json",
                     "Resources/constants.xlf"]
        if os.environ.get("PLATFORM") != "windows":
            generated += ["MacOS/butteraugli", "Info.plist", "_CodeSignature"]
        for relative in generated:
            if (contents / relative).exists():
                sys.exit("stale generated output reached configure: " + relative)
    fail("FAIL_BUILD" if build else "FAIL_CONFIGURE")
    if build:
        contents = root / os.environ["OUTPUT_CONTENTS"]
        if os.environ.get("PLATFORM") == "windows":
            target = contents / "Windows64/butteraugli.4DX"
            if os.environ.get("OUTPUT_MODE") != "missing":
                write(target, b"" if os.environ.get("OUTPUT_MODE") == "empty" else b"fresh-windows")
        else:
            sys.path.insert(0, str(root / ".github/scripts"))
            import source_version
            source = source_version._Source(root)
            if os.environ.get("OUTPUT_MODE") != "missing":
                write(contents / "MacOS/butteraugli",
                      b"" if os.environ.get("OUTPUT_MODE") == "empty" else b"fresh-universal")
            metadata = {"CFBundleShortVersionString": source.version}
            if source.build is not None:
                metadata["CFBundleVersion"] = source.build
            write(contents / "Info.plist", plistlib.dumps(metadata))
        for resource in ("manifest.json", "constants.xlf"):
            write(contents / "Resources" / resource, (root / "butteraugli" / resource).read_bytes())
elif name == "lipo":
    print(os.environ.get("ARCHS", "arm64 x86_64"))
elif name == "security":
    fail("FAIL_SECURITY")
    print('  1) FAKE "Developer ID Application: Fixture (TEST)"')
elif name == "codesign":
    fail("FAIL_VERIFY" if "--verify" in args else "FAIL_SIGN")
elif name in ("ditto", "hdiutil"):
    fail("FAIL_" + name.upper())
    write(p(args[-1]), b"fake-package")
elif name == "xcrun":
    if args[:2] == ["notarytool", "submit"]:
        is_dmg = args[2].endswith(".dmg")
        status = os.environ.get("DMG_NOTARY" if is_dmg else "ZIP_NOTARY", "Accepted")
        print(status if status == "invalid-json" else json.dumps({"status": status}))
        fail("FAIL_DMG_NOTARY" if is_dmg else "FAIL_ZIP_NOTARY")
    elif args[:2] == ["stapler", "staple"]:
        fail("FAIL_STAPLE")
    else:
        sys.exit("unexpected xcrun command")
elif name == "mkdir":
    for arg in args:
        if not arg.startswith("-"):
            p(arg).mkdir(parents="-p" in args, exist_ok="-p" in args)
elif name == "cp":
    shutil.copyfile(args[0], args[1])
elif name == "grep":
    lines = [line for line in sys.stdin.read().splitlines() if args[-1] in line]
    print("\n".join(lines))
    sys.exit(0 if lines else 1)
elif name == "head":
    print("\n".join(sys.stdin.read().splitlines()[:1]))
elif name == "sed":
    import re
    print(re.sub(r'.*"(.*)".*', r'\1', sys.stdin.read().strip()))
else:
    sys.exit("unrecognized stub command: " + name)
'''


class NativeWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.root = ROOT / ".github/tests" / ("native-fixture-" + uuid.uuid4().hex)
        self.root.mkdir()
        self.addCleanup(shutil.rmtree, self.root)
        for name in (
            "VERSION", "butteraugli/CMakeLists.txt", "butteraugli/Info.plist.in",
            "butteraugli/manifest.json", "butteraugli/constants.xlf",
            ".github/scripts/native.py", ".github/scripts/source_version.py",
        ):
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, target)
        self.bin = self.root / "closed-bin"
        self.bin.mkdir()
        for name in ("cmake", "lipo", "security", "codesign", "ditto", "hdiutil",
                     "xcrun", "mkdir", "cp", "grep", "head", "sed"):
            target = self.bin / name
            target.write_text("#!" + sys.executable + "\n" + STUB)
            target.chmod(0o755)
        (self.bin / "python3").symlink_to(sys.executable)
        self.env = {
            "PATH": str(self.bin), "HOME": str(self.root), "FIXTURE_ROOT": str(self.root),
            "RUNNER_TEMP": str(self.root / "runner"), "GITHUB_ENV": str(self.root / "env"),
            "KEYCHAIN_PATH": str(self.root / "fake.keychain"),
            "BUNDLE_PATH": str(BUNDLE), "OUTPUT_CONTENTS": str(BUNDLE / "Contents"),
            "PLATFORM": "macos", "TMPDIR": str(self.root / "runner"),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        (self.root / "runner").mkdir()
        # Source metadata, not a hard-coded initial release, owns expectations.
        result = subprocess.run(
            [sys.executable, ".github/scripts/source_version.py", "read"],
            cwd=self.root, env=self.env, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.version = result.stdout.strip()
        self.contents = self.root / BUNDLE / "Contents"
        self.wincontents = self.root / WINDOWS / "Contents"
        self.sentinels = [
            BUNDLE / "Contents/Resources/unrelated.txt",
            BUNDLE / "Contents/Resources/en.lproj/unrelated.txt",
            BUNDLE / "Contents/Windows64/vendor.dll",
            BUNDLE / "Contents/MacOS/unrelated.txt",
            WINDOWS / "Contents/Windows64/vendor.dll",
            WINDOWS / "Contents/Resources/vendor.txt",
            Path("butteraugli/butteraugli-test/Plugins/other.bundle/keep"),
            Path("deps/butteraugli/keep"), Path("deps/4D-Plugin-SDK/keep"),
        ]
        for path in self.sentinels:
            self.write(path, b"preserve")
        self.stale(self.contents)
        self.stale(self.wincontents, windows=True)
        self.write(Path("winbuild/butteraugli.4DX"), b"downloaded-windows")

    def write(self, relative, data):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def stale(self, contents, windows=False):
        names = ["Resources/constants.xlf", "Resources/manifest.json", "Windows64/butteraugli.4DX"]
        if not windows:
            names += ["Info.plist", "MacOS/butteraugli", "_CodeSignature/CodeResources"]
        for name in names:
            self.write(contents / name, b"stale")

    def step(self, name, success=True, windows=False):
        shell = PWSH if windows else BASH
        self.assertIsNotNone(shell, "real pwsh and bash are required for native workflow tests")
        if windows:
            self.env.update(PLATFORM="windows", OUTPUT_CONTENTS=str(WINDOWS / "Contents"))
        command = [shell, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command"] if windows else [shell, "--noprofile", "--norc", "-c"]
        text = run_text("build-windows" if windows else "build-macos", name, self.version)
        result = subprocess.run(command + [text], cwd=self.root, env=self.env,
                                capture_output=True, text=True, timeout=30)
        if success:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def calls(self):
        path = self.root / "calls.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def preserved(self):
        for path in self.sentinels:
            self.assertEqual((self.root / path).read_bytes(), b"preserve", str(path))

    def build(self):
        self.step("build macOS plugin (universal)")

    def test_windows_actual_cmake_step_removes_exact_stale_outputs(self):
        self.step("build with CMake", windows=True)
        self.assertEqual((self.wincontents / "Windows64/butteraugli.4DX").read_bytes(), b"fresh-windows")
        for name in ("constants.xlf", "manifest.json"):
            self.assertEqual((self.wincontents / "Resources" / name).read_bytes(),
                             (self.root / "butteraugli" / name).read_bytes())
        self.assertEqual((self.contents / "MacOS/butteraugli").read_bytes(), b"stale")
        self.preserved()
        cmake = [call for call in self.calls() if call[0] == "cmake"]
        self.assertEqual(cmake, [["cmake", "..", "-A", "x64"],
                                 ["cmake", "--build", ".", "--config", "Release"]])

    def test_windows_configure_failure_aborts_before_build(self):
        self.env["FAIL_CONFIGURE"] = "1"
        self.step("build with CMake", success=False, windows=True)
        self.assertEqual(len([c for c in self.calls() if c[0] == "cmake"]), 1)
        for name in ("Windows64/butteraugli.4DX", "Resources/manifest.json",
                     "Resources/constants.xlf"):
            self.assertFalse((self.wincontents / name).exists())
        self.preserved()

    def test_windows_build_failure_aborts(self):
        self.env["FAIL_BUILD"] = "1"
        self.step("build with CMake", success=False, windows=True)
        self.assertFalse((self.wincontents / "Windows64/butteraugli.4DX").exists())

    def test_windows_missing_output_cannot_use_tracked_prebuilt(self):
        self.env["OUTPUT_MODE"] = "missing"
        self.step("build with CMake", success=False, windows=True)
        self.preserved()

    def test_windows_empty_output_fails(self):
        self.env["OUTPUT_MODE"] = "empty"
        self.step("build with CMake", success=False, windows=True)

    def test_windows_cleanup_failure_is_fatal_without_permissions_fixture(self):
        path = self.wincontents / "Windows64/butteraugli.4DX"
        path.unlink()
        path.mkdir()
        (path / "blocker").write_bytes(b"not removable as a leaf")
        self.step("build with CMake", success=False, windows=True)
        self.assertFalse(any(c[0] == "cmake" for c in self.calls()))

    def test_macos_fresh_build_verification_merge_sign_and_package(self):
        self.build()
        self.assertEqual((self.contents / "MacOS/butteraugli").read_bytes(), b"fresh-universal")
        self.assertFalse((self.contents / "_CodeSignature").exists())
        self.assertFalse((self.contents / "Windows64/butteraugli.4DX").exists())
        self.assertEqual((self.wincontents / "Windows64/butteraugli.4DX").read_bytes(), b"stale")
        self.step("verify universal binary")
        self.step("merge Windows binaries into the bundle")
        self.assertEqual((self.contents / "Windows64/butteraugli.4DX").read_bytes(), b"downloaded-windows")
        self.step("codesign")
        self.step("package, notarize & staple")
        self.assertTrue((self.root / f"butteraugli-{self.version}.zip").is_file())
        self.assertTrue((self.root / f"butteraugli-{self.version}.dmg").is_file())
        self.assertIn(["xcrun", "stapler", "staple", f"butteraugli-{self.version}.dmg"], self.calls())
        self.preserved()
        configure = next(c for c in self.calls() if c[0] == "cmake")
        self.assertIn("-DCMAKE_OSX_ARCHITECTURES=arm64;x86_64", configure)
        self.assertIn("-DCMAKE_BUILD_TYPE=Release", configure)
        self.assertIn("-DCMAKE_OSX_DEPLOYMENT_TARGET=" + workflow()["env"]["MACOS_DEPLOYMENT_TARGET"], configure)

    def test_macos_cleanup_directory_failure_stops_before_cmake(self):
        path = self.contents / "MacOS/butteraugli"
        path.unlink()
        path.mkdir()
        (path / "blocker").write_bytes(b"directory, not generated executable")
        self.step("build macOS plugin (universal)", success=False)
        self.assertFalse(any(c[0] == "cmake" for c in self.calls()))
        self.preserved()

    def test_cleanup_removes_generated_symlinks_without_following_them(self):
        executable = self.contents / "MacOS/butteraugli"
        executable.unlink()
        target = self.root / "deps/butteraugli/keep"
        executable.symlink_to(target)
        signature = self.contents / "_CodeSignature"
        shutil.rmtree(signature)
        signature.symlink_to(target.parent, target_is_directory=True)
        self.build()
        self.assertFalse(executable.is_symlink())
        self.assertFalse(signature.exists())
        self.step("verify universal binary")
        self.preserved()

    def test_macos_configure_and_build_failures_abort(self):
        for key in ("FAIL_CONFIGURE", "FAIL_BUILD"):
            with self.subTest(key=key):
                self.env.pop("FAIL_CONFIGURE", None)
                self.env[key] = "1"
                self.step("build macOS plugin (universal)", success=False)
                self.assertFalse((self.contents / "MacOS/butteraugli").exists())
        self.preserved()

    def test_macos_missing_and_empty_outputs_fail_verification(self):
        for mode in ("missing", "empty"):
            with self.subTest(mode=mode):
                self.env["OUTPUT_MODE"] = mode
                self.build()
                self.step("verify universal binary", success=False)

    def test_architecture_tokens_are_exact_but_extra_slices_are_allowed(self):
        self.build()
        for archs in ("arm64e x86_64", "arm64 x86_64h", "arm64", "x86_64", ""):
            with self.subTest(archs=archs):
                self.env["ARCHS"] = archs
                self.step("verify universal binary", success=False)
        self.env["ARCHS"] = "x86_64 arm64 arm64e x86_64h"
        self.step("verify universal binary")

    def test_metadata_missing_or_drifting_is_rejected(self):
        self.build()
        path = self.contents / "Info.plist"
        original = path.read_bytes()
        metadata = plistlib.loads(original)
        for key in ("CFBundleShortVersionString", "CFBundleVersion"):
            if key not in metadata:
                continue
            with self.subTest(drift=key):
                changed = dict(metadata, **{key: "999999.0.0"})
                path.write_bytes(plistlib.dumps(changed))
                self.step("verify universal binary", success=False)
        changed = dict(metadata)
        changed.pop("CFBundleShortVersionString")
        path.write_bytes(plistlib.dumps(changed))
        self.step("verify universal binary", success=False)
        path.unlink()
        self.step("verify universal binary", success=False)
        path.write_bytes(original)
        self.step("verify universal binary")

    def test_localized_version_drift_is_rejected(self):
        self.build()
        self.write(BUNDLE / "Contents/Resources/en.lproj/InfoPlist.strings",
                   b'"CFBundleShortVersionString" = "999999.0.0";')
        self.step("verify universal binary", success=False)

    def test_stale_or_missing_generated_resources_are_rejected(self):
        self.build()
        for name in ("constants.xlf", "manifest.json"):
            path = self.contents / "Resources" / name
            original = path.read_bytes()
            path.write_bytes(b"stale")
            self.step("verify universal binary", success=False)
            path.unlink()
            self.step("verify universal binary", success=False)
            path.write_bytes(original)
        self.step("verify universal binary")

    def test_missing_windows_artifact_merge_fails(self):
        (self.root / "winbuild/butteraugli.4DX").unlink()
        self.step("merge Windows binaries into the bundle", success=False)

    def test_signing_and_signature_verification_failures_abort(self):
        for key in ("FAIL_SIGN", "FAIL_VERIFY", "FAIL_SECURITY"):
            with self.subTest(key=key):
                self.env[key] = "1"
                before = len(self.calls())
                self.step("codesign", success=False)
                calls = self.calls()[before:]
                if key == "FAIL_SIGN":
                    self.assertFalse(any("--verify" in c for c in calls))
                self.env.pop(key)

    def test_notary_exit_status_and_json_both_required_for_each_archive(self):
        for kind in ("ZIP", "DMG"):
            for value, nonzero in (("Accepted", True), ("Rejected", False),
                                   ("In Progress", False), ("invalid-json", False)):
                with self.subTest(kind=kind, value=value, nonzero=nonzero):
                    self.env[kind + "_NOTARY"] = value
                    if nonzero:
                        self.env["FAIL_" + kind + "_NOTARY"] = "1"
                    before = len(self.calls())
                    self.step("package, notarize & staple", success=False)
                    calls = self.calls()[before:]
                    self.assertFalse(any(c[:3] == ["xcrun", "stapler", "staple"] for c in calls))
                    if kind == "ZIP":
                        self.assertFalse(any(c[0] == "xcrun" and ".dmg" in " ".join(c) for c in calls))
                    self.env.pop(kind + "_NOTARY")
                    self.env.pop("FAIL_" + kind + "_NOTARY", None)
        self.assertFalse((self.root / "env").exists())

    def test_packaging_and_stapling_failures_do_not_export_success(self):
        for key in ("FAIL_DITTO", "FAIL_HDIUTIL", "FAIL_STAPLE"):
            with self.subTest(key=key):
                self.env[key] = "1"
                self.step("package, notarize & staple", success=False)
                self.assertFalse((self.root / "env").exists())
                self.env.pop(key)


if __name__ == "__main__":
    unittest.main()
