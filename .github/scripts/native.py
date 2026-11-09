#!/usr/bin/env python3
"""Narrow cleanup and native output gates; never build or sign here."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys

sys.dont_write_bytecode = True
import source_version

BUNDLE = Path("butteraugli/butteraugli-test/Plugins/butteraugli.bundle")


def clean():
    contents = BUNDLE / "Contents"
    for relative in (
        "MacOS/butteraugli", "Info.plist", "Resources/constants.xlf",
        "Resources/manifest.json", "Windows64/butteraugli.4DX",
    ):
        path = contents / relative
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.exists():
            raise ValueError(f"expected generated file, not directory: {path}")
    signature = contents / "_CodeSignature"
    if signature.is_symlink():
        signature.unlink()
    elif signature.exists():
        shutil.rmtree(signature)


def verify():
    executable = BUNDLE / "Contents/MacOS/butteraugli"
    if not executable.is_file() or executable.stat().st_size == 0:
        raise ValueError("native executable is missing or empty")
    archs = subprocess.check_output(["lipo", "-archs", str(executable)], text=True).split()
    if not {"arm64", "x86_64"} <= set(archs):
        raise ValueError(f"required exact architecture slices missing: {archs}")
    source_version.validate_bundle(BUNDLE)
    for name in ("constants.xlf", "manifest.json"):
        if (BUNDLE / "Contents/Resources" / name).read_bytes() != (
            Path("butteraugli") / name
        ).read_bytes():
            raise ValueError(f"copied resource is stale: {name}")


def accepted(path):
    data = json.loads(Path(path).read_text())
    if not isinstance(data, dict) or data.get("status") != "Accepted":
        raise ValueError("notarytool did not report Accepted")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("clean", "verify", "accepted"))
    parser.add_argument("path", nargs="?")
    args = parser.parse_args()
    try:
        if args.command == "clean":
            clean()
        elif args.command == "verify":
            verify()
        else:
            if not args.path:
                raise ValueError("notary JSON file is required")
            accepted(args.path)
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(f"Native gate failed: {error}", file=sys.stderr)
        sys.exit(1)
