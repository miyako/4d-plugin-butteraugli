#!/usr/bin/env python3
"""Read, bump, and validate the source-owned macOS bundle version."""

from __future__ import annotations

import argparse
import codecs
from dataclasses import dataclass
from pathlib import Path
import plistlib
import re
import sys
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[2]
SOURCE_PATHS = ("VERSION", "butteraugli/CMakeLists.txt", "butteraugli/Info.plist.in")
SHORT = "MACOSX_BUNDLE_SHORT_VERSION_STRING"
BUILD = "MACOSX_BUNDLE_BUNDLE_VERSION"
PLIST_SHORT = "CFBundleShortVersionString"
PLIST_BUILD = "CFBundleVersion"
_VERSION = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\Z")
_REFERENCE = re.compile(r"\$\{([^{}]+)\}")


def _canonical(value: str) -> str:
    if not isinstance(value, str) or not _VERSION.fullmatch(value):
        raise ValueError(f"not a canonical X.Y.Z version: {value!r}")
    return value


@dataclass
class _Text:
    path: Path
    raw: bytes
    text: str
    encoding: str
    bom: bytes

    @classmethod
    def read(cls, path: Path) -> "_Text":
        raw = path.read_bytes()
        for bom, encoding in (
            (codecs.BOM_UTF8, "utf-8"),
            (codecs.BOM_UTF16_LE, "utf-16-le"),
            (codecs.BOM_UTF16_BE, "utf-16-be"),
        ):
            if raw.startswith(bom):
                return cls(path, raw, raw[len(bom):].decode(encoding), encoding, bom)
        encoding = "utf-8"
        if b"\0" in raw:
            even = raw[0::2].count(0)
            odd = raw[1::2].count(0)
            encoding = "utf-16-le" if odd > even else "utf-16-be"
        return cls(path, raw, raw.decode(encoding), encoding, b"")

    def render(self, edits: list[tuple[int, int, str]]) -> bytes:
        text = self.text
        last = len(text) + 1
        for start, end, value in sorted(edits, reverse=True):
            if end > last:
                raise ValueError(f"overlapping version edits in {self.path}")
            text = text[:start] + value + text[end:]
            last = start
        return self.bom + text.encode(self.encoding)


@dataclass(frozen=True)
class _Token:
    value: str
    start: int
    end: int
    delimiter: bool = False


def _bracket(text: str, offset: int) -> tuple[int, int] | None:
    match = re.match(r"\[(=*)\[", text[offset:])
    if not match:
        return None
    start = offset + len(match[0])
    closing = "]" + match[1] + "]"
    end = text.find(closing, start)
    if end < 0:
        raise ValueError("unterminated CMake bracket argument/comment")
    return start, end + len(closing)


def _tokens(text: str) -> list[_Token]:
    tokens = []
    i = 0
    while i < len(text):
        if text[i].isspace():
            i += 1
        elif text[i] == "#":
            bracket = _bracket(text, i + 1)
            if bracket:
                i = bracket[1]
            else:
                newline = re.search(r"[\r\n]", text[i:])
                i = len(text) if newline is None else i + newline.start() + 1
        elif text[i] in "()":
            tokens.append(_Token(text[i], i, i + 1, True))
            i += 1
        elif text[i] == '"':
            start = i = i + 1
            value = ""
            while i < len(text) and text[i] != '"':
                if text[i] == "\\":
                    i += 1
                    if i >= len(text):
                        raise ValueError("unterminated CMake escape")
                value += text[i]
                i += 1
            if i == len(text):
                raise ValueError("unterminated CMake quoted argument")
            tokens.append(_Token(value, start, i))
            i += 1
        else:
            bracket = _bracket(text, i)
            if bracket:
                start, end = bracket
                closing = re.match(r"\[(=*)\[", text[i:])[1]
                stop = end - len(closing) - 2
                tokens.append(_Token(text[start:stop], start, stop))
                i = end
                continue
            start = i
            value = ""
            while i < len(text) and not text[i].isspace() and text[i] not in "()#":
                if text[i] == "\\":
                    i += 1
                    if i >= len(text):
                        raise ValueError("unterminated CMake escape")
                value += text[i]
                i += 1
            tokens.append(_Token(value, start, i))
    return tokens


class _CMake:
    def __init__(self, text: str):
        self.variables: dict[str, list[_Token]] = {}
        self.invalid_variables: set[str] = set()
        self.metadata: dict[str, _Token] = {}
        tokens = _tokens(text)
        i = 0
        while i + 1 < len(tokens):
            command = tokens[i].value.lower()
            if not tokens[i + 1].delimiter or tokens[i + 1].value != "(":
                i += 1
                continue
            i += 2
            args = []
            depth = 1
            while i < len(tokens) and depth:
                token = tokens[i]
                if token.delimiter and token.value == "(":
                    depth += 1
                elif token.delimiter and token.value == ")":
                    depth -= 1
                if depth:
                    args.append(token)
                i += 1
            if depth:
                raise ValueError("unterminated CMake command")
            if command == "set" and args:
                name = args[0].value
                tail = [arg.value for arg in args[2:]]
                single_value = not tail or (
                    len(tail) in (3, 4) and tail[0] == "CACHE"
                    and tail[1] in ("BOOL", "FILEPATH", "PATH", "STRING", "INTERNAL")
                    and (len(tail) == 3 or tail[3] == "FORCE")
                )
                if not single_value:
                    self.invalid_variables.add(name)
                    if name in (SHORT, BUILD):
                        raise ValueError(f"non-scalar CMake {name}")
                if len(args) >= 2:
                    self.variables.setdefault(name, []).append(args[1])
                    if name in (SHORT, BUILD):
                        self._add(name, args[1])
                elif name in (SHORT, BUILD):
                    raise ValueError(f"missing CMake value for {name}")
                else:
                    self.invalid_variables.add(name)
            elif command == "set_target_properties":
                positions = [n for n, arg in enumerate(args) if arg.value == "PROPERTIES"]
                if not positions:
                    continue
                props = args[positions[0] + 1:]
                if len(props) % 2:
                    raise ValueError("unpaired CMake target property")
                for key, value in zip(props[::2], props[1::2]):
                    if key.value in (SHORT, BUILD):
                        self._add(key.value, value)
        if SHORT not in self.metadata:
            raise ValueError(f"missing CMake {SHORT}")

    def _add(self, key: str, token: _Token) -> None:
        if key in self.metadata:
            raise ValueError(f"duplicate CMake {key}")
        self.metadata[key] = token

    def resolve(self, value: str, stack: tuple[str, ...] = ()) -> str:
        def substitute(match: re.Match) -> str:
            name = match[1]
            if name in stack:
                raise ValueError(f"cyclic CMake reference: {name}")
            if name in self.invalid_variables:
                raise ValueError(f"non-scalar CMake variable: {name}")
            if name in self.metadata:
                token = self.metadata[name]
            else:
                values = self.variables.get(name, [])
                if len(values) != 1:
                    raise ValueError(f"unresolved or duplicate CMake variable: {name}")
                token = values[0]
            return self.resolve(token.value, stack + (name,))

        resolved = _REFERENCE.sub(substitute, value)
        if "${" in resolved or "$<" in resolved or "@" in resolved:
            raise ValueError(f"unresolved CMake value: {resolved!r}")
        return resolved

    def version(self, key: str) -> str | None:
        token = self.metadata.get(key)
        return None if token is None else _canonical(self.resolve(token.value, (key,)))


class _UniqueDict(dict):
    def __setitem__(self, key, value):
        if key in self:
            raise ValueError(f"duplicate plist key: {key}")
        super().__setitem__(key, value)


def _plist(data: bytes | str) -> dict:
    if isinstance(data, bytes) and data.startswith(b"bplist00"):
        result = plistlib.loads(data, dict_type=_UniqueDict)
    else:
        text = data if isinstance(data, str) else _decode(data)
        # Parse decoded text so UTF-16 and BOMless input use the detected encoding,
        # not an obsolete XML encoding declaration.
        element = ET.fromstring(text)
        for dictionary in element.iter("dict"):
            keys = [child.text or "" for child in dictionary if child.tag == "key"]
            if len(keys) != len(set(keys)):
                raise ValueError("duplicate plist key")
        normalized = re.sub(r"<\?xml[^?]*\?>", "", text, count=1)
        result = plistlib.loads(normalized.encode("utf-8"), fmt=plistlib.FMT_XML, dict_type=_UniqueDict)
    if not isinstance(result, dict):
        raise ValueError("plist root must be a dictionary")
    return result


def _decode(data: bytes) -> str:
    for bom, encoding in (
        (codecs.BOM_UTF8, "utf-8"),
        (codecs.BOM_UTF16_LE, "utf-16-le"),
        (codecs.BOM_UTF16_BE, "utf-16-be"),
    ):
        if data.startswith(bom):
            return data[len(bom):].decode(encoding)
    if b"\0" in data:
        return data.decode("utf-16-le" if data[1::2].count(0) > data[0::2].count(0) else "utf-16-be")
    return data.decode("utf-8")


def _short_span(text: str) -> tuple[int, int]:
    # XML comments can contain realistic-looking keys; never edit those.
    masked = re.sub(r"<!--.*?-->", lambda m: " " * len(m[0]), text, flags=re.S)
    match = re.search(
        r"<key>\s*CFBundleShortVersionString\s*</key>\s*<string>(.*?)</string>",
        masked, flags=re.S,
    )
    if not match:
        raise ValueError("short version must be an XML string")
    return match.span(1)


class _Source:
    def __init__(self, root: Path, contents: dict[str, bytes] | None = None):
        self.docs = {}
        for name in SOURCE_PATHS:
            if contents is None:
                self.docs[name] = _Text.read(root / name)
            else:
                original = _Text.read(root / name)
                raw = contents[name]
                self.docs[name] = _Text(original.path, raw, _decode(raw), original.encoding, original.bom)
        version_text = self.docs[SOURCE_PATHS[0]].text
        self.version = _canonical(version_text.strip())
        self.cmake = _CMake(self.docs[SOURCE_PATHS[1]].text)
        if self.cmake.version(SHORT) != self.version:
            raise ValueError("CMake short version differs from VERSION")
        self.build = self.cmake.version(BUILD)
        self.plist = _plist(self.docs[SOURCE_PATHS[2]].text)
        if PLIST_SHORT not in self.plist:
            raise ValueError(f"missing {PLIST_SHORT}")
        if self.build is None and PLIST_BUILD in self.plist:
            value = self.plist[PLIST_BUILD]
            if not isinstance(value, str):
                raise ValueError(f"{PLIST_BUILD} must be a string")
            self.build = _canonical(self.cmake.resolve(value))
        self._check(self.plist, source=True)

    def _check(self, metadata: dict, source: bool = False, localized: bool = False) -> None:
        for key, expected in ((PLIST_SHORT, self.version), (PLIST_BUILD, self.build)):
            if key not in metadata:
                if key == PLIST_SHORT and not localized:
                    raise ValueError(f"missing {key}")
                continue
            value = metadata[key]
            if not isinstance(value, str):
                raise ValueError(f"{key} must be a string")
            if source:
                value = self.cmake.resolve(value)
            if _canonical(value) != expected:
                raise ValueError(f"{key} differs from source: {value!r} != {expected!r}")


def read_version(root: Path = Path(".")) -> str:
    """Return VERSION after checking all source version declarations."""
    return _Source(Path(root)).version


def bump(mode: str, root: Path = Path(".")) -> str:
    """Bump marketing version only, preserving the independent build version."""
    if mode not in ("major", "minor", "patch"):
        raise ValueError(f"unsupported bump mode: {mode}")
    root = Path(root)
    source = _Source(root)
    components = [int(value) for value in source.version.split(".")]
    index = ("major", "minor", "patch").index(mode)
    components[index] += 1
    components[index + 1:] = [0] * (2 - index)
    version = ".".join(map(str, components))
    edits: dict[str, list[tuple[int, int, str]]] = {name: [] for name in SOURCE_PATHS}
    document = source.docs[SOURCE_PATHS[0]]
    start = len(document.text) - len(document.text.lstrip())
    edits[SOURCE_PATHS[0]].append((start, start + len(source.version), version))
    token = source.cmake.metadata[SHORT]
    # Preserve aliases when their leaf is exclusively owned by the short version.
    candidate = token
    seen = set()
    while (match := _REFERENCE.fullmatch(candidate.value)) and match[1] not in seen:
        seen.add(match[1])
        values = source.cmake.variables.get(match[1], [])
        if len(values) != 1 or match[1] in (SHORT, BUILD):
            break
        candidate = values[0]
    if candidate is not token:
        probe = source.docs[SOURCE_PATHS[1]].render([(candidate.start, candidate.end, version)])
        try:
            cmake = _CMake(_decode(probe))
            safe = cmake.version(SHORT) == version and cmake.version(BUILD) == source.build
        except ValueError:
            safe = False
        if safe:
            token = candidate
    edits[SOURCE_PATHS[1]].append((token.start, token.end, version))
    short = source.plist[PLIST_SHORT]
    if "${" not in short:
        start, end = _short_span(source.docs[SOURCE_PATHS[2]].text)
        edits[SOURCE_PATHS[2]].append((start, end, version))
    rendered = {name: source.docs[name].render(edits[name]) for name in SOURCE_PATHS}
    checked = _Source(root, rendered)
    if checked.build != source.build:
        raise ValueError("bump would change the independent build version")
    for name in SOURCE_PATHS:
        if rendered[name] != source.docs[name].raw:
            source.docs[name].path.write_bytes(rendered[name])
    return version


def _strings(data: bytes) -> dict:
    if data.startswith(b"bplist00") or _decode(data).lstrip().startswith(("<?xml", "<plist")):
        return _plist(data)
    text = _decode(data)
    atom = r'(?:"(?:\\.|[^"\\])*"|[^\s=;"/]+)'
    trivia = r'(?:\s+|//[^\r\n]*|/\*.*?\*/)*'
    pattern = re.compile(
        rf'\s+|//[^\r\n]*|/\*.*?\*/|({atom}){trivia}={trivia}({atom}){trivia};', re.S
    )
    result = _UniqueDict()
    offset = 0
    for match in pattern.finditer(text):
        if match.start() != offset:
            raise ValueError("invalid InfoPlist.strings syntax")
        offset = match.end()
        if match[1] is not None:
            def unquote(value):
                if not value.startswith('"'):
                    return value
                value = value[1:-1]
                escapes = {'"': '"', "\\": "\\", "n": "\n", "r": "\r", "t": "\t",
                           "a": "\a", "b": "\b", "f": "\f", "v": "\v"}

                def escape(match):
                    code = match[1]
                    if code in escapes:
                        return escapes[code]
                    if code.startswith(("U", "u")) and len(code) == 5:
                        return chr(int(code[1:], 16))
                    if all(character in "01234567" for character in code):
                        return chr(int(code, 8))
                    raise ValueError(f"unsupported InfoPlist.strings escape: {code}")

                return re.sub(r'\\([Uu][0-9a-fA-F]{4}|[0-7]{1,3}|.)', escape, value)
            result[unquote(match[1])] = unquote(match[2])
    if offset != len(text):
        raise ValueError("invalid InfoPlist.strings syntax")
    return result


def validate_bundle(path: Path, root: Path = Path(".")) -> None:
    """Validate built plist and actual localized overrides without modifying them."""
    source = _Source(Path(root))
    path = Path(path)
    if path.is_file():
        info = path
        resources = path.parent / "Resources"
    elif (path / "Contents" / "Info.plist").is_file():
        info = path / "Contents" / "Info.plist"
        resources = path / "Contents" / "Resources"
    else:
        info = path / "Info.plist"
        resources = path / "Resources"
    source._check(_plist(info.read_bytes()))
    if resources.is_dir():
        for localized in resources.glob("*.lproj/InfoPlist.strings"):
            source._check(_strings(localized.read_bytes()), localized=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("read", "bump", "validate-bundle"):
        command = commands.add_parser(name)
        command.add_argument("--root", type=Path, default=argparse.SUPPRESS)
        if name == "bump":
            command.add_argument("--mode", choices=("major", "minor", "patch"), required=True)
        elif name == "validate-bundle":
            command.add_argument("path", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "read":
            print(read_version(args.root))
        elif args.command == "bump":
            print(bump(args.mode, args.root))
        else:
            validate_bundle(args.path, args.root)
    except (OSError, ValueError, UnicodeError, ET.ParseError, plistlib.InvalidFileException) as error:
        parser.exit(1, f"source-version: {error}\n")
    return 0


if __name__ == "__main__":
    sys.dont_write_bytecode = True
    sys.exit(main())
