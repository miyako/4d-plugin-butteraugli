"""Portable source-version protocol tests; no native build tools are required."""

import codecs
import contextlib
import importlib.util
import io
import os
from pathlib import Path
import plistlib
import shutil
import sys
import subprocess
import unittest
import uuid


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "source_version.py"
SPEC = importlib.util.spec_from_file_location("source_version", SCRIPT)
sv = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = sv
SPEC.loader.exec_module(sv)
CURRENT = (sv.ROOT / "VERSION").read_text().strip()
BUILD = sv._CMake((sv.ROOT / sv.SOURCE_PATHS[1]).read_text()).version(sv.BUILD)
ENCODINGS = (
    ("utf-8", b""),
    ("utf-8", codecs.BOM_UTF8),
    ("utf-16-le", codecs.BOM_UTF16_LE),
    ("utf-16-be", codecs.BOM_UTF16_BE),
    ("utf-16-le", b""),
    ("utf-16-be", b""),
)


class SourceVersionTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).parent / (".source-version-test-" + uuid.uuid4().hex)
        self.root.mkdir()
        self.addCleanup(shutil.rmtree, self.root)
        self.make_source()

    def make_source(self, version=CURRENT, build=BUILD, encoding="utf-8", bom=b"", ending="\n"):
        cmake = (
            "cmake_minimum_required(VERSION 3.20)\n"
            "project(test)\n"
            "set(TARGET butteraugli)\n"
            "# unrelated café 日本語\n"
            "set_target_properties(${TARGET} PROPERTIES\n"
            f' MACOSX_BUNDLE_SHORT_VERSION_STRING "{version}"\n'
        )
        if build is not None:
            cmake += f' MACOSX_BUNDLE_BUNDLE_VERSION "{build}"\n'
        cmake += ' OUTPUT_NAME "unchanged"\n)\n'
        metadata = {sv.PLIST_SHORT: "${" + sv.SHORT + "}", "Unrelated": "café 日本語"}
        if build is not None:
            metadata[sv.PLIST_BUILD] = "${" + sv.BUILD + "}"
        plist = plistlib.dumps(metadata, sort_keys=False).decode()
        self.original = {}
        self.encoding, self.bom = encoding, bom
        for name, text in zip(sv.SOURCE_PATHS, (version + "\n", cmake, plist)):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            raw = bom + text.replace("\n", ending).encode(encoding)
            path.write_bytes(raw)
            self.original[name] = raw

    def text(self, name):
        return sv._decode((self.root / name).read_bytes())

    def write(self, name, text):
        (self.root / name).write_bytes(self.bom + text.encode(self.encoding))

    def cmake(self, text):
        self.write(sv.SOURCE_PATHS[1], text)

    def plist(self, values):
        self.write(sv.SOURCE_PATHS[2], plistlib.dumps(values, sort_keys=False).decode())

    def snapshot(self):
        return {name: (self.root / name).read_bytes() for name in sv.SOURCE_PATHS}

    def assert_rejected_without_writes(self):
        before = self.snapshot()
        with self.assertRaises((ValueError, UnicodeError)):
            sv.read_version(self.root)
        for mode in ("major", "minor", "patch"):
            with self.assertRaises((ValueError, UnicodeError)):
                sv.bump(mode, self.root)
            self.assertEqual(self.snapshot(), before)

    def bundle(self, metadata=None, binary=False):
        bundle = self.root / "built.bundle"
        contents = bundle / "Contents"
        contents.mkdir(parents=True, exist_ok=True)
        metadata = metadata if metadata is not None else {
            sv.PLIST_SHORT: CURRENT, sv.PLIST_BUILD: BUILD, "Unrelated": "value"
        }
        data = plistlib.dumps(metadata, fmt=plistlib.FMT_BINARY if binary else plistlib.FMT_XML)
        (contents / "Info.plist").write_bytes(data)
        return bundle

    def test_source_paths_are_only_authoritative_inputs(self):
        self.assertEqual(sv.ROOT, SCRIPT.parents[2])
        self.assertEqual(set(sv.SOURCE_PATHS), {
            "VERSION", "butteraugli/CMakeLists.txt", "butteraugli/Info.plist.in"
        })
        self.assertEqual(sv.read_version(self.root), CURRENT)

    def test_modes_reset_only_marketing_components(self):
        parts = [int(value) for value in CURRENT.split(".")]
        for mode, index in (("major", 0), ("minor", 1), ("patch", 2)):
            with self.subTest(mode=mode):
                self.make_source()
                expected = parts[:]
                expected[index] += 1
                expected[index + 1:] = [0] * (2 - index)
                expected = ".".join(map(str, expected))
                self.assertEqual(sv.bump(mode, self.root), expected)
                self.assertEqual(sv.read_version(self.root), expected)
                self.assertEqual(sv._CMake(self.text(sv.SOURCE_PATHS[1])).version(sv.BUILD), BUILD)
                self.assertEqual((self.root / sv.SOURCE_PATHS[2]).read_bytes(), self.original[sv.SOURCE_PATHS[2]])

    def test_all_encodings_and_line_endings_preserve_unrelated_bytes(self):
        new = CURRENT.rsplit(".", 1)[0] + "." + str(int(CURRENT.rsplit(".", 1)[1]) + 1)
        for encoding, bom in ENCODINGS:
            for ending in ("\n", "\r\n", "\r", "\n\r\n\r"):
                with self.subTest(encoding=encoding, bom=bom, ending=repr(ending)):
                    self.make_source(encoding=encoding, bom=bom, ending=ending)
                    self.assertEqual(sv.read_version(self.root), CURRENT)
                    sv.bump("patch", self.root)
                    for name in sv.SOURCE_PATHS:
                        expected = self.original[name]
                        if name == sv.SOURCE_PATHS[0]:
                            expected = expected.replace(CURRENT.encode(encoding), new.encode(encoding), 1)
                        elif name == sv.SOURCE_PATHS[1]:
                            old_token = f'"{CURRENT}"'.encode(encoding)
                            expected = expected.replace(old_token, f'"{new}"'.encode(encoding), 1)
                        self.assertEqual((self.root / name).read_bytes(), expected)

    def test_literal_plist_short_is_cobumped_without_normalization(self):
        for encoding, bom in ENCODINGS:
            with self.subTest(encoding=encoding, bom=bom):
                self.make_source(encoding=encoding, bom=bom, ending="\r\n")
                text = self.text(sv.SOURCE_PATHS[2]).replace("${" + sv.SHORT + "}", CURRENT)
                text = text.replace("<dict>", "<dict><!-- <key>CFBundleShortVersionString</key>"
                                    "<string>9.9.9</string> -->")
                self.write(sv.SOURCE_PATHS[2], text)
                before = (self.root / sv.SOURCE_PATHS[2]).read_bytes()
                new = sv.bump("minor", self.root)
                self.assertEqual((self.root / sv.SOURCE_PATHS[2]).read_bytes(),
                                 before.replace(CURRENT.encode(encoding), new.encode(encoding), 1))
                self.assertEqual(sv.read_version(self.root), new)

    def test_cmake_noise_quoted_unquoted_bracket_and_comments(self):
        noise = (
            '# MACOSX_BUNDLE_SHORT_VERSION_STRING "9.9.9"\n'
            '#[=[ set_target_properties(x PROPERTIES MACOSX_BUNDLE_SHORT_VERSION_STRING 9.9.9) ]=]\n'
            'message("set_target_properties(x PROPERTIES MACOSX_BUNDLE_SHORT_VERSION_STRING 9.9.9)")\n'
            'message(")")\n'
            'message([=[ MACOSX_BUNDLE_SHORT_VERSION_STRING 9.9.9 # noise ]=])\n'
            'set(NOISE "# MACOSX_BUNDLE_SHORT_VERSION_STRING 9.9.9")\n'
        )
        cmake = self.text(sv.SOURCE_PATHS[1]).replace(f'"{CURRENT}"', CURRENT, 1)
        self.cmake(noise + cmake)
        self.assertEqual(sv.read_version(self.root), CURRENT)
        new = sv.bump("patch", self.root)
        self.assertTrue(self.text(sv.SOURCE_PATHS[1]).startswith(noise))
        self.assertEqual(sv.read_version(self.root), new)

    def test_same_line_and_cross_line_cmake_duplicates(self):
        cmake = self.text(sv.SOURCE_PATHS[1])
        for key in (sv.SHORT, sv.BUILD):
            for separator in (" ", "\n", "\r\n", "\r"):
                with self.subTest(key=key, separator=repr(separator)):
                    self.cmake(cmake.replace(' OUTPUT_NAME', f'{separator}{key} "{CURRENT}" OUTPUT_NAME'))
                    self.assert_rejected_without_writes()

    def test_cmake_set_metadata_and_alias_chain(self):
        self.cmake(f'set(VERSION_LEAF "{CURRENT}")\nset(VERSION_ALIAS ${{VERSION_LEAF}})\n'
                   f'set({sv.SHORT} "${{VERSION_ALIAS}}")\nset({sv.BUILD} "{BUILD}")\n')
        new = sv.bump("patch", self.root)
        self.assertIn(f'set(VERSION_LEAF "{new}")', self.text(sv.SOURCE_PATHS[1]))
        self.assertIn(f'set({sv.SHORT} "${{VERSION_ALIAS}}")', self.text(sv.SOURCE_PATHS[1]))
        self.assertEqual(sv.read_version(self.root), new)

    def test_bracket_metadata_and_cache_variable(self):
        self.cmake(f'set(VERSION_LEAF "{CURRENT}" CACHE STRING "marketing" FORCE)\n'
                   f'set({sv.SHORT} [=[${{VERSION_LEAF}}]=])\n'
                   f'set({sv.BUILD} [[{BUILD}]])\n')
        new = sv.bump("patch", self.root)
        self.assertIn(f'set(VERSION_LEAF "{new}" CACHE STRING "marketing" FORCE)',
                      self.text(sv.SOURCE_PATHS[1]))
        self.assertEqual(sv.read_version(self.root), new)

    def test_non_scalar_set_assignments_fail_closed(self):
        for declarations in (
            f'set({sv.SHORT} {CURRENT} junk)\n',
            f'set({sv.SHORT} {CURRENT} {sv.SHORT} {CURRENT})\n',
            f'set(V {CURRENT} junk)\nset({sv.SHORT} ${{V}})\n',
            f'set(V {CURRENT})\nset(V)\nset({sv.SHORT} ${{V}})\n',
        ):
            with self.subTest(declarations=declarations):
                self.cmake(declarations)
                self.assert_rejected_without_writes()

    def test_independent_build_alias_is_not_modified(self):
        self.make_source(build=CURRENT)
        cmake = self.text(sv.SOURCE_PATHS[1])
        self.cmake(f'set(SHARED "{CURRENT}")\n' + cmake.replace(f'"{CURRENT}"', '"${SHARED}"'))
        new = sv.bump("patch", self.root)
        self.assertIn(f'set(SHARED "{CURRENT}")', self.text(sv.SOURCE_PATHS[1]))
        self.assertEqual(sv._CMake(self.text(sv.SOURCE_PATHS[1])).version(sv.BUILD), CURRENT)
        self.assertEqual(sv.read_version(self.root), new)

    def test_equal_build_and_marketing_values_remain_independent(self):
        self.make_source(build=CURRENT)
        before = self.text(sv.SOURCE_PATHS[1])
        build_declaration = f'MACOSX_BUNDLE_BUNDLE_VERSION "{CURRENT}"'
        self.assertIn(build_declaration, before)
        new = sv.bump("patch", self.root)
        after = self.text(sv.SOURCE_PATHS[1])
        self.assertIn(build_declaration, after)
        self.assertIn(f'MACOSX_BUNDLE_SHORT_VERSION_STRING "{new}"', after)
        self.assertEqual(after, before.replace(
            f'MACOSX_BUNDLE_SHORT_VERSION_STRING "{CURRENT}"',
            f'MACOSX_BUNDLE_SHORT_VERSION_STRING "{new}"', 1,
        ))
        self.assertEqual(sv._CMake(after).version(sv.BUILD), CURRENT)
        self.assertEqual(sv.read_version(self.root), new)

    def test_composed_reference_can_be_read_and_bumped(self):
        major, minor, patch = CURRENT.split(".")
        cmake = self.text(sv.SOURCE_PATHS[1]).replace(f'"{CURRENT}"', '"${A}.${B}.${C}"', 1)
        self.cmake(f'set(A {major})\nset(B {minor})\nset(C {patch})\n' + cmake)
        self.assertEqual(sv.read_version(self.root), CURRENT)
        new = sv.bump("patch", self.root)
        self.assertEqual(sv.read_version(self.root), new)

    def test_unresolved_cyclic_duplicate_and_drifting_references(self):
        original = self.text(sv.SOURCE_PATHS[1])
        for prefix, value in (
            ("", "${MISSING}"),
            ("set(A ${A})\n", "${A}"),
            ("set(A ${B})\nset(B ${A})\n", "${A}"),
            (f"set(A {CURRENT})\nset(A {CURRENT})\n", "${A}"),
            ("set(A 99.88.77)\n", "${A}"),
            ("", "$<CONFIG:Release>"),
        ):
            with self.subTest(value=value, prefix=prefix):
                self.cmake(prefix + original.replace(f'"{CURRENT}"', f'"{value}"', 1))
                self.assert_rejected_without_writes()

    def test_canonical_versions_only(self):
        for value in ("1", "1.2", "01.2.3", "1.02.3", "1.2.03", "1.2.3.4", "v1.2.3",
                      "1.2.3-beta", "-1.2.3", "1.2.3 extra", "１.2.3", ""):
            with self.subTest(value=value):
                self.make_source()
                self.write("VERSION", value + "\n")
                self.assert_rejected_without_writes()

    def test_missing_short_in_either_or_both_sources_fails_closed(self):
        for cmake_missing, plist_missing in ((True, False), (False, True), (True, True)):
            with self.subTest(cmake=cmake_missing, plist=plist_missing):
                self.make_source()
                if cmake_missing:
                    cmake = self.text(sv.SOURCE_PATHS[1])
                    self.cmake(cmake.replace(f' MACOSX_BUNDLE_SHORT_VERSION_STRING "{CURRENT}"\n', ""))
                if plist_missing:
                    self.plist({sv.PLIST_BUILD: "${" + sv.BUILD + "}"})
                self.assert_rejected_without_writes()

    def test_missing_build_is_preserved(self):
        self.make_source(build=None)
        self.assertEqual(sv.read_version(self.root), CURRENT)
        new = sv.bump("patch", self.root)
        self.assertNotIn(sv.BUILD, self.text(sv.SOURCE_PATHS[1]))
        self.assertNotIn(sv.PLIST_BUILD, self.text(sv.SOURCE_PATHS[2]))
        self.assertEqual(sv.read_version(self.root), new)
        sv.validate_bundle(self.bundle({sv.PLIST_SHORT: new}), self.root)

    def test_plist_only_independent_build_literal(self):
        self.make_source(build=None)
        self.plist({sv.PLIST_SHORT: CURRENT, sv.PLIST_BUILD: "7.8.9"})
        new = sv.bump("patch", self.root)
        self.assertEqual(sv._plist(self.text(sv.SOURCE_PATHS[2]))[sv.PLIST_BUILD], "7.8.9")
        sv.validate_bundle(self.bundle({sv.PLIST_SHORT: new, sv.PLIST_BUILD: "7.8.9"}), self.root)

    def test_plist_only_independent_build_reference(self):
        self.make_source(build=None)
        self.cmake('set(INDEPENDENT_BUILD "7.8.9")\n' + self.text(sv.SOURCE_PATHS[1]))
        self.plist({sv.PLIST_SHORT: "${" + sv.SHORT + "}", sv.PLIST_BUILD: "${INDEPENDENT_BUILD}"})
        before = (self.root / sv.SOURCE_PATHS[2]).read_bytes()
        new = sv.bump("patch", self.root)
        self.assertEqual((self.root / sv.SOURCE_PATHS[2]).read_bytes(), before)
        sv.validate_bundle(self.bundle({sv.PLIST_SHORT: new, sv.PLIST_BUILD: "7.8.9"}), self.root)

    def test_plist_duplicate_keys_and_invalid_values(self):
        original = self.text(sv.SOURCE_PATHS[2])
        for key in (sv.PLIST_SHORT, sv.PLIST_BUILD, "Unrelated"):
            with self.subTest(key=key):
                self.write(sv.SOURCE_PATHS[2], original.replace(
                    "</dict>", f"<key>{key}</key><string>{CURRENT}</string></dict>"))
                self.assert_rejected_without_writes()
        for value in ("99.88.77", "${UNRESOLVED}", "${" + sv.BUILD + "}", 102, ""):
            with self.subTest(value=value):
                if value == "${" + sv.BUILD + "}" and BUILD == CURRENT:
                    continue
                self.plist({sv.PLIST_SHORT: value})
                self.assert_rejected_without_writes()

    def test_rendered_reference_drift_is_detected_before_any_write(self):
        cmake = self.text(sv.SOURCE_PATHS[1])
        self.cmake(f'set(PLIST_VERSION "{CURRENT}")\n' + cmake)
        self.plist({sv.PLIST_SHORT: "${PLIST_VERSION}", sv.PLIST_BUILD: BUILD})
        self.assertEqual(sv.read_version(self.root), CURRENT)
        before = self.snapshot()
        with self.assertRaises(ValueError):
            sv.bump("patch", self.root)
        self.assertEqual(self.snapshot(), before)

    def test_invalid_mode_does_not_write(self):
        before = self.snapshot()
        with self.assertRaises(ValueError):
            sv.bump("revision", self.root)
        self.assertEqual(self.snapshot(), before)

    def test_built_xml_binary_and_all_text_encodings(self):
        for binary in (False, True):
            with self.subTest(binary=binary):
                bundle = self.bundle(binary=binary)
                before = (bundle / "Contents" / "Info.plist").read_bytes()
                sv.validate_bundle(bundle, self.root)
                sv.validate_bundle(bundle / "Contents", self.root)
                sv.validate_bundle(bundle / "Contents" / "Info.plist", self.root)
                self.assertEqual((bundle / "Contents" / "Info.plist").read_bytes(), before)
        bundle = self.bundle()
        path = bundle / "Contents" / "Info.plist"
        text = path.read_text()
        for encoding, bom in ENCODINGS:
            with self.subTest(encoding=encoding, bom=bom):
                path.write_bytes(bom + text.replace("\n", "\r\n").encode(encoding))
                sv.validate_bundle(bundle, self.root)

    def test_built_stale_unresolved_missing_and_nonstring_versions(self):
        for metadata in (
            {}, {sv.PLIST_BUILD: BUILD},
            {sv.PLIST_SHORT: "${" + sv.SHORT + "}"},
            {sv.PLIST_SHORT: "99.88.77"},
            {sv.PLIST_SHORT: 102},
            {sv.PLIST_SHORT: CURRENT, sv.PLIST_BUILD: "99.88.77"},
            {sv.PLIST_SHORT: CURRENT, sv.PLIST_BUILD: "${" + sv.BUILD + "}"},
        ):
            with self.subTest(metadata=metadata):
                for binary in (False, True):
                    with self.assertRaises(ValueError):
                        sv.validate_bundle(self.bundle(metadata, binary=binary), self.root)

    def test_built_build_absence_is_optional(self):
        sv.validate_bundle(self.bundle({sv.PLIST_SHORT: CURRENT}), self.root)

    def test_built_duplicate_xml_keys(self):
        bundle = self.bundle()
        path = bundle / "Contents" / "Info.plist"
        path.write_text(path.read_text().replace(
            "</dict>", f"<key>{sv.PLIST_SHORT}</key><string>{CURRENT}</string></dict>"))
        with self.assertRaises(ValueError):
            sv.validate_bundle(bundle, self.root)

    def test_localized_text_plist_binary_and_encodings(self):
        bundle = self.bundle()
        localized = bundle / "Contents" / "Resources" / "ja.lproj" / "InfoPlist.strings"
        localized.parent.mkdir(parents=True)
        for encoding, bom in ENCODINGS:
            for ending in ("\n", "\r\n", "\r"):
                with self.subTest(encoding=encoding, bom=bom, ending=repr(ending)):
                    text = (f'/* 日本語 */\n"{sv.PLIST_SHORT}" = "{CURRENT}";\n'
                            f'"{sv.PLIST_BUILD}" = "{BUILD}";\n"CFBundleName" = "日本語";\n')
                    localized.write_bytes(bom + text.replace("\n", ending).encode(encoding))
                    before = localized.read_bytes()
                    sv.validate_bundle(bundle, self.root)
                    self.assertEqual(localized.read_bytes(), before)
        for fmt in (plistlib.FMT_BINARY, plistlib.FMT_XML):
            localized.write_bytes(plistlib.dumps({sv.PLIST_SHORT: CURRENT}, fmt=fmt))
            sv.validate_bundle(bundle, self.root)

    def test_localized_overrides_fail_closed(self):
        bundle = self.bundle()
        localized = bundle / "Contents" / "Resources" / "en.lproj" / "InfoPlist.strings"
        localized.parent.mkdir(parents=True)
        for text in (
            f'"{sv.PLIST_SHORT}" = "99.88.77";',
            f'"{sv.PLIST_BUILD}" = "99.88.77";',
            f'"{sv.PLIST_SHORT}" = "${{{sv.SHORT}}}";',
            f'"{sv.PLIST_SHORT}" = "{CURRENT}"; "{sv.PLIST_SHORT}" = "{CURRENT}";',
            "not a strings file",
        ):
            with self.subTest(text=text):
                localized.write_bytes(codecs.BOM_UTF16_LE + text.encode("utf-16-le"))
                with self.assertRaises(ValueError):
                    sv.validate_bundle(bundle, self.root)
        localized.write_text('"CFBundleName" = "Localized";')
        sv.validate_bundle(bundle, self.root)

    def test_localized_unquoted_and_escaped_keys(self):
        bundle = self.bundle()
        localized = bundle / "Contents" / "Resources" / "en.lproj" / "InfoPlist.strings"
        localized.parent.mkdir(parents=True)
        localized.write_text(f'{sv.PLIST_SHORT} /* comment */ = {CURRENT};\n')
        sv.validate_bundle(bundle, self.root)
        escaped = 'CFBundleShortVersionStrin\\U0067'
        localized.write_text(f'"{escaped}" = "{CURRENT}";\n')
        sv.validate_bundle(bundle, self.root)
        localized.write_text(f'"{escaped}" = "99.88.77";\n')
        with self.assertRaises(ValueError):
            sv.validate_bundle(bundle, self.root)
        localized.write_text(f'"{escaped}" = "{CURRENT}"; "{sv.PLIST_SHORT}" = "{CURRENT}";')
        with self.assertRaises(ValueError):
            sv.validate_bundle(bundle, self.root)

    def test_cli_root_before_and_after_subcommand(self):
        for argv in (["--root", str(self.root), "read"], ["read", "--root", str(self.root)]):
            with self.subTest(argv=argv), contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(sv.main(argv), 0)
                self.assertEqual(output.getvalue().strip(), CURRENT)
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(sv.main(["bump", "--root", str(self.root), "--mode", "patch"]), 0)
            self.assertEqual(output.getvalue().strip(), sv.read_version(self.root))
        bundle = self.bundle({sv.PLIST_SHORT: sv.read_version(self.root), sv.PLIST_BUILD: BUILD})
        self.assertEqual(sv.main(["validate-bundle", str(bundle), "--root", str(self.root)]), 0)

    def test_default_root_is_current_working_directory(self):
        original = Path.cwd()
        try:
            os.chdir(self.root)
            self.assertEqual(sv.read_version(), CURRENT)
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(sv.main(["read"]), 0)
                self.assertEqual(output.getvalue().strip(), CURRENT)
            new = sv.bump("patch")
            self.assertEqual(sv.read_version(), new)
            bundle = self.bundle({sv.PLIST_SHORT: new, sv.PLIST_BUILD: BUILD})
            sv.validate_bundle(bundle)
        finally:
            os.chdir(original)

    def test_direct_cli_does_not_create_bytecode(self):
        script = self.root / ".github" / "scripts" / "source_version.py"
        script.parent.mkdir(parents=True)
        script.write_bytes(SCRIPT.read_bytes())
        environment = os.environ.copy()
        environment.pop("PYTHONDONTWRITEBYTECODE", None)
        result = subprocess.run(
            [sys.executable, str(script), "read"], cwd=self.root, env=environment,
            capture_output=True, text=True, check=True,
        )
        self.assertEqual(result.stdout.strip(), CURRENT)
        self.assertEqual(list(self.root.rglob("__pycache__")), [])

    def test_cli_failure_has_nonzero_status_and_no_writes(self):
        self.write("VERSION", "invalid\n")
        before = self.snapshot()
        with contextlib.redirect_stderr(io.StringIO()) as output, self.assertRaises(SystemExit) as error:
            sv.main(["read", "--root", str(self.root)])
        self.assertEqual(error.exception.code, 1)
        self.assertIn("source-version:", output.getvalue())
        self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
