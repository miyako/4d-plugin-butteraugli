"""Offline CLI integration tests: real selected trees and a create-only fake gh."""

import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import unittest
import uuid

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".github/scripts"))
import release
import source_version


FAKE_GH = r'''#!/usr/bin/env python3
import json
import os
from pathlib import Path
import subprocess
import sys
from urllib.parse import parse_qs, urlsplit

args = sys.argv[1:]
method = args[args.index("--method") + 1]
host = args[args.index("--hostname") + 1]
endpoint = args[args.index("--hostname") + 2]
config = json.loads(Path(os.environ["FAKE_CONFIG"]).read_text())
payload = None
asset = None
if "--input" in args:
    value = args[args.index("--input") + 1]
    if value == "-":
        payload = json.load(sys.stdin)
    else:
        asset = Path(value)
record = {"method": method, "host": host, "endpoint": endpoint,
          "payload": payload, "args": args}
with open(os.environ["FAKE_LOG"], "a") as stream:
    stream.write(json.dumps(record) + "\n")

def fail(message):
    print(message, file=sys.stderr)
    sys.exit(1)

def output(value):
    print(json.dumps(value))
    sys.exit(0)

if method == "GET":
    page = parse_qs(urlsplit(endpoint).query)["page"][0]
    result = config.get("pages", {}).get(page, [])
    if result == "fail":
        fail("authentication/404/network error")
    if result == "invalid-json":
        print("{invalid")
        sys.exit(0)
    output(result)
if method != "POST":
    fail("only GET and create-only POST are permitted")
if endpoint.endswith("/git/refs"):
    if config.get("tag_race"):
        subprocess.run(["git", "-c", "safe.bareRepository=explicit",
                        "--git-dir=" + os.environ["FAKE_REMOTE"],
                        "update-ref", payload["ref"], payload["sha"]], check=True)
        fail("422 Reference already exists")
    if config.get("ref_fail"):
        fail("tag creation failed")
    subprocess.run(["git", "-c", "safe.bareRepository=explicit",
                    "--git-dir=" + os.environ["FAKE_REMOTE"],
                    "update-ref", payload["ref"], payload["sha"]], check=True)
    response = {"ref": payload["ref"], "object": {"sha": payload["sha"], "type": "commit"}}
    response.update(config.get("ref_response", {}))
    output(response)
if endpoint.endswith("/releases"):
    if config.get("release_fail"):
        fail("release creation failed")
    output(dict(payload, id=config.get("release_id", 700)))
if "/assets?name=" in endpoint:
    if host != "uploads.github.com" or asset is None:
        fail("uploads must use the binary uploads API")
    if config.get("upload_fail") == asset.suffix:
        fail("upload failed")
    response = {"id": 701 if asset.suffix == ".zip" else 702,
                "name": asset.name, "size": asset.stat().st_size, "state": "uploaded"}
    response.update(config.get("asset_response", {}))
    output(response)
fail("unexpected endpoint")
'''


class ReleaseCLI(unittest.TestCase):
    def setUp(self):
        self.scratch = ROOT / (".release-test-" + uuid.uuid4().hex)
        self.root = self.scratch / "checkout"
        self.root.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, self.scratch)
        self.remote = self.scratch / "remote.git"
        self.log = self.scratch / "calls.jsonl"
        self.config = self.scratch / "config.json"
        self.config.write_text("{}")
        bin_path = self.scratch / "bin"
        bin_path.mkdir()
        fake = bin_path / "gh"
        fake.write_text(FAKE_GH)
        fake.chmod(0o755)
        self.env = dict(os.environ, PATH=str(bin_path) + os.pathsep + os.environ["PATH"],
                        GH_TOKEN="offline-fake-token", GITHUB_REPOSITORY="owner/repo",
                        FAKE_CONFIG=str(self.config), FAKE_LOG=str(self.log),
                        FAKE_REMOTE=str(self.remote), PYTHONDONTWRITEBYTECODE="1",
                        GIT_CONFIG_NOSYSTEM="1", GIT_TERMINAL_PROMPT="0")
        self.git("init", "-q")
        self.git("config", "user.name", "Offline fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("config", "core.filemode", "true")
        subprocess.run(["git", "init", "--bare", "-q", str(self.remote)],
                       env=self.env, check=True, capture_output=True)
        files = list(source_version.SOURCE_PATHS) + [
            ".github/scripts/release.py", ".github/scripts/source_version.py",
            ".github/workflows/release.yml", ".gitignore",
            "butteraugli/butteraugli-4dplugin.cpp", "butteraugli/constants.xlf",
            "butteraugli/manifest.json",
        ] + sorted(path for path in release.GENERATED if path.startswith(release.CONTENTS))
        for name in files:
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / name, target)
        self.version = source_version.read_version(self.root)
        self.tag = "v" + self.version
        self.git("add", ".")
        self.git("commit", "-qm", "selected fixture")
        self.sha = self.git("rev-parse", "HEAD").strip()
        self.env["GITHUB_SHA"] = self.sha
        self.git("remote", "add", "origin", str(self.remote))
        # Seed the local bare object store without publishing any tag.
        self.git("push", "-q", "origin", "HEAD:refs/heads/fixture")

    def git(self, *args):
        result = subprocess.run(["git", *args], cwd=self.root, env=self.env,
                                text=True, capture_output=True, check=True)
        return result.stdout

    def configure(self, **values):
        self.config.write_text(json.dumps(values))

    def calls(self):
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def cli(self, command, success=True):
        result = subprocess.run(
            [sys.executable, ".github/scripts/release.py", command],
            cwd=self.root, env=self.env, text=True, capture_output=True,
        )
        if success:
            self.assertEqual(result.returncode, 0, result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout)
        return result

    def built(self):
        info = self.root / release.CONTENTS / "Info.plist"
        metadata = plistlib.loads(info.read_bytes())
        metadata["CFBundleShortVersionString"] = self.version
        metadata["CFBundleVersion"] = source_version._Source(self.root).build
        info.write_bytes(plistlib.dumps(metadata))
        for name in ("MacOS/butteraugli", "Windows64/butteraugli.4DX",
                     "_CodeSignature/CodeResources"):
            (self.root / release.CONTENTS / name).write_bytes(b"native-output-" + name.encode())
        for name in ("manifest.json", "constants.xlf"):
            shutil.copyfile(self.root / "butteraugli" / name,
                            self.root / release.CONTENTS / "Resources" / name)
        outputs = {
            "butteraugli/cmake-build/CMakeCache.txt": b"native intermediates",
            "winbuild/butteraugli.4DX": b"downloaded windows",
            "butteraugli/butteraugli-test/Plugins/butteraugli/Contents/Windows64/butteraugli.4DX":
                b"windows build output",
            "butteraugli/butteraugli-test/Plugins/butteraugli/Contents/Resources/manifest.json":
                (self.root / "butteraugli/manifest.json").read_bytes(),
            "butteraugli/butteraugli-test/Plugins/butteraugli/Contents/Resources/constants.xlf":
                (self.root / "butteraugli/constants.xlf").read_bytes(),
            f"butteraugli-{self.version}.zip": b"zip native bytes",
            f"butteraugli-{self.version}.dmg": b"dmg native bytes",
        }
        for name, data in outputs.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        return {name: (self.root / name).read_bytes() for name in
                list(outputs) + [path for path in release.GENERATED
                                 if (self.root / path).is_file()]}

    def assert_no_api(self):
        self.assertEqual(self.calls(), [])

    def assert_no_post(self):
        self.assertFalse(any(call["method"] == "POST" for call in self.calls()))

    def test_preflight_clean_selected_checkout_and_outputs(self):
        output = self.scratch / "github-output"
        self.env["GITHUB_OUTPUT"] = str(output)
        self.cli("preflight")
        self.assertEqual(output.read_text(), f"version={self.version}\ntag={self.tag}\n")
        self.assertEqual(self.calls()[0]["method"], "GET")

    def test_preflight_does_not_create_bytecode_without_environment_override(self):
        self.env.pop("PYTHONDONTWRITEBYTECODE", None)
        self.cli("preflight")
        self.assertFalse(list(self.root.rglob("__pycache__")))
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_publish_retains_actual_native_rewrites_and_exact_create_only_protocol(self):
        before = self.built()
        self.cli("publish")
        self.assertEqual(before, {name: (self.root / name).read_bytes() for name in before})
        calls = self.calls()
        self.assertEqual([call["method"] for call in calls],
                         ["GET", "POST", "POST", "POST", "POST"])
        self.assertEqual(calls[1]["payload"],
                         {"ref": f"refs/tags/{self.tag}", "sha": self.sha})
        self.assertEqual(calls[2]["payload"], {
            "tag_name": self.tag, "name": self.tag, "target_commitish": self.sha,
            "draft": False, "prerelease": False, "body": "", "generate_release_notes": False,
        })
        for call, suffix in zip(calls[3:], ("zip", "dmg")):
            self.assertEqual(call["host"], "uploads.github.com")
            self.assertEqual(call["endpoint"],
                             f"/repos/owner/repo/releases/700/assets?name=butteraugli-{self.version}.{suffix}")
            self.assertIn("Content-Type: application/octet-stream", call["args"])
            self.assertNotEqual(call["args"][call["args"].index("--input") + 1], "-")
        self.assertEqual(self.git("ls-remote", "--refs", "origin", f"refs/tags/{self.tag}").strip(),
                         f"{self.sha}\trefs/tags/{self.tag}")

    def test_preflight_rejects_tracked_generated_dirt(self):
        path = self.root / release.CONTENTS / "MacOS/butteraugli"
        path.write_bytes(b"prior native output")
        self.cli("preflight", False)
        self.assert_no_api()

    def test_preflight_rejects_untracked_native_output_even_ignored(self):
        path = self.root / "butteraugli/cmake-build/CMakeCache.txt"
        path.parent.mkdir()
        path.write_text("stale")
        self.cli("preflight", False)
        self.assert_no_api()

    def test_dirty_source_worktree_fails_before_api(self):
        self.built()
        (self.root / "butteraugli/butteraugli-4dplugin.cpp").write_text("dirty")
        self.cli("publish", False)
        self.assert_no_api()

    def test_dirty_workflow_fails_before_api(self):
        self.built()
        (self.root / ".github/workflows/release.yml").write_text("dirty")
        self.cli("publish", False)
        self.assert_no_api()

    def test_staged_then_reversed_source_fails_before_api(self):
        self.built()
        name = "butteraugli/butteraugli-4dplugin.cpp"
        path = self.root / name
        original = path.read_bytes()
        path.write_bytes(b"staged source")
        self.git("add", name)
        path.write_bytes(original)
        self.cli("publish", False)
        self.assert_no_api()

    def test_staged_generated_output_is_allowed_and_retained(self):
        before = self.built()
        self.git("add", release.CONTENTS + "MacOS/butteraugli")
        self.cli("publish")
        self.assertEqual((self.root / release.CONTENTS / "MacOS/butteraugli").read_bytes(),
                         before[release.CONTENTS + "MacOS/butteraugli"])

    def test_assume_unchanged_source_fails_before_api(self):
        self.built()
        name = "butteraugli/butteraugli-4dplugin.cpp"
        self.git("update-index", "--assume-unchanged", name)
        (self.root / name).write_text("hidden")
        self.cli("publish", False)
        self.assert_no_api()

    def test_skip_worktree_source_fails_before_api(self):
        self.built()
        name = "butteraugli/butteraugli-4dplugin.cpp"
        self.git("update-index", "--skip-worktree", name)
        (self.root / name).write_text("hidden")
        self.cli("publish", False)
        self.assert_no_api()

    def test_head_moved_fails_before_api(self):
        self.git("commit", "--allow-empty", "-qm", "moved")
        self.built()
        self.cli("publish", False)
        self.assert_no_api()

    def test_untracked_source_fails_before_api(self):
        self.built()
        (self.root / "butteraugli/unexpected.cpp").write_text("untracked source")
        self.cli("publish", False)
        self.assert_no_api()

    def test_ignored_untracked_source_fails_preflight_before_api(self):
        exclude = self.root / ".git/info/exclude"
        with exclude.open("a") as stream:
            stream.write("\nbutteraugli/unexpected.cpp\n")
        (self.root / "butteraugli/unexpected.cpp").write_text("ignored source")
        self.cli("preflight", False)
        self.assert_no_api()

    def test_ignored_untracked_config_fails_publish_before_api(self):
        self.built()
        exclude = self.root / ".git/info/exclude"
        with exclude.open("a") as stream:
            stream.write("\n.github/workflows/unexpected.yml\n")
        (self.root / ".github/workflows/unexpected.yml").write_text("ignored config")
        self.cli("publish", False)
        self.assert_no_api()

    def test_generated_directory_is_not_blanket_exclusion(self):
        self.built()
        path = self.root / release.CONTENTS / "Resources/extra-config.json"
        path.write_text("{}")
        self.cli("publish", False)
        self.assert_no_api()

    def test_windows_output_directory_is_exact_whitelist(self):
        self.built()
        path = self.root / "butteraugli/butteraugli-test/Plugins/butteraugli/Contents/extra.cpp"
        path.write_text("not an output")
        self.cli("publish", False)
        self.assert_no_api()

    def test_tracked_mode_change_fails_before_api(self):
        self.built()
        path = self.root / "butteraugli/butteraugli-4dplugin.cpp"
        path.chmod(0o755)
        self.cli("publish", False)
        self.assert_no_api()

    def test_tracked_symlink_change_fails_before_api(self):
        self.built()
        path = self.root / "butteraugli/butteraugli-4dplugin.cpp"
        path.unlink()
        path.symlink_to("constants.xlf")
        self.cli("publish", False)
        self.assert_no_api()

    def test_tracked_deletion_fails_before_api(self):
        self.built()
        (self.root / "butteraugli/manifest.json").unlink()
        self.cli("publish", False)
        self.assert_no_api()

    def test_bundle_metadata_mismatch_fails_before_api(self):
        self.built()
        info = self.root / release.CONTENTS / "Info.plist"
        data = plistlib.loads(info.read_bytes())
        data["CFBundleShortVersionString"] = "999999.0.0"
        info.write_bytes(plistlib.dumps(data))
        self.cli("publish", False)
        self.assert_no_api()

    def test_missing_empty_and_symlink_assets_fail_before_api(self):
        self.built()
        asset = self.root / f"butteraugli-{self.version}.zip"
        for kind in ("missing", "empty", "symlink"):
            with self.subTest(kind=kind):
                if asset.exists() or asset.is_symlink():
                    asset.unlink()
                if kind == "empty":
                    asset.write_bytes(b"")
                elif kind == "symlink":
                    asset.symlink_to(f"butteraugli-{self.version}.dmg")
                self.cli("publish", False)
                self.assert_no_api()

    def test_missing_environment_fails_before_api(self):
        for key in ("GH_TOKEN", "GITHUB_REPOSITORY", "GITHUB_SHA"):
            with self.subTest(key=key):
                value = self.env.pop(key)
                self.cli("preflight", False)
                self.assert_no_api()
                self.env[key] = value

    def test_auth_404_network_and_malformed_json_fail_closed(self):
        for response in ("fail", "invalid-json", {}, None):
            with self.subTest(response=response):
                self.configure(pages={"1": response})
                self.cli("preflight", False)
                self.assert_no_post()

    def test_strict_release_entry_schemas(self):
        valid = {"id": 1, "tag_name": "v0.0.0", "draft": False}
        for changes in ({"id": True}, {"id": 0}, {"id": "1"}, {"tag_name": None},
                        {"draft": 0}, {"draft": None}):
            with self.subTest(changes=changes):
                self.configure(pages={"1": [dict(valid, **changes)]})
                self.cli("preflight", False)
                self.assert_no_post()

    def test_draft_on_second_page_blocks(self):
        self.configure(pages={
            "1": [{"id": 1, "tag_name": "v0.0.0", "draft": False}],
            "2": [{"id": 2, "tag_name": self.tag, "draft": True}],
        })
        self.cli("preflight", False)
        self.assertEqual(len(self.calls()), 3)
        self.assert_no_post()

    def test_second_page_failure_blocks_even_if_tag_absent_first_page(self):
        self.configure(pages={
            "1": [{"id": 1, "tag_name": "v0.0.0", "draft": False}], "2": "fail",
        })
        self.cli("preflight", False)
        self.assertEqual(len(self.calls()), 2)
        self.assert_no_post()

    def test_publish_rechecks_remote_availability_after_preflight(self):
        self.cli("preflight")
        self.built()
        self.configure(pages={"1": [{"id": 1, "tag_name": self.tag, "draft": True}]})
        self.cli("publish", False)
        self.assert_no_post()

    def test_existing_remote_tag_at_same_sha_blocks_before_creation(self):
        self.git("tag", self.tag)
        self.git("push", "-q", "origin", f"refs/tags/{self.tag}")
        self.cli("preflight", False)
        self.assert_no_post()

    def test_similarly_named_remote_tag_does_not_block(self):
        self.git("tag", self.tag + "-other")
        self.git("push", "-q", "origin", f"refs/tags/{self.tag}-other")
        self.cli("preflight")

    def test_same_sha_tag_race_post_fails_without_release_or_retry(self):
        self.built()
        self.configure(tag_race=True)
        result = self.cli("publish", False)
        self.assertEqual([call["method"] for call in self.calls()], ["GET", "POST"])
        self.assertIn("manual recovery", result.stderr)
        self.assertIn(self.sha, self.git("ls-remote", "--refs", "origin", f"refs/tags/{self.tag}"))

    def test_invalid_ref_response_stops_after_retaining_tag(self):
        self.built()
        self.configure(ref_response={"object": {"sha": "0" * 40}})
        self.cli("publish", False)
        self.assertEqual(len(self.calls()), 2)

    def test_release_creation_failure_retains_tag_and_does_not_upload(self):
        self.built()
        self.configure(release_fail=True)
        result = self.cli("publish", False)
        self.assertEqual(len(self.calls()), 3)
        self.assertIn("retained", result.stderr)
        self.assertIn(self.sha, self.git("ls-remote", "--refs", "origin", f"refs/tags/{self.tag}"))

    def test_new_release_id_must_be_strict_integer_and_not_preexisting(self):
        self.built()
        for release_id in (True, "700", 0, 7):
            with self.subTest(release_id=release_id):
                self.configure(release_id=release_id, pages={
                    "1": [{"id": 7, "tag_name": "v0.0.0", "draft": False}],
                })
                self.cli("publish", False)
                self.assertFalse(any("/assets?" in call["endpoint"] for call in self.calls()))
                subprocess.run(["git", "-c", "safe.bareRepository=explicit",
                                "--git-dir=" + str(self.remote), "update-ref", "-d",
                                f"refs/tags/{self.tag}"], env=self.env, check=True)
                self.log.unlink()

    def test_asset_upload_failure_retains_partial_publication(self):
        self.built()
        self.configure(upload_fail=".dmg")
        result = self.cli("publish", False)
        self.assertEqual(len(self.calls()), 5)
        self.assertIn("manual recovery", result.stderr)
        self.assertIn("retained", result.stderr)

    def test_asset_schema_requires_exact_basename_integer_size_and_uploaded_state(self):
        self.built()
        for changes in ({"size": True}, {"size": "16"}, {"size": 0},
                        {"name": "other.zip"}, {"state": "new"}, {"id": True}):
            with self.subTest(changes=changes):
                self.configure(asset_response=changes)
                self.cli("publish", False)
                self.assertEqual(len(self.calls()), 4)
                subprocess.run(["git", "-c", "safe.bareRepository=explicit",
                                "--git-dir=" + str(self.remote), "update-ref", "-d",
                                f"refs/tags/{self.tag}"], env=self.env, check=True)
                self.log.unlink()


if __name__ == "__main__":
    unittest.main()
