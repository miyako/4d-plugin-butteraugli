# butteraugli

A 4D plugin wrapping Google's butteraugli perceptual image difference library. Compares two images and returns a heatmap visualizing the differences as perceived by the human visual system.

## Requirements

- 4D v21.1 or later (compatible with the `compatibilityVersion` in `.4DProject`)

## Installation

Download the latest release from the [Releases](../../releases) page.

### macOS & Windows (single download)

1. Download the `.zip` from the release
2. Extract to get the `butteraugli.bundle` folder
3. Copy the `.bundle` into your 4D application's **Plugins** folder (or your database's **Plugins** folder)
4. Restart 4D

### macOS only (notarized DMG)

1. Download the `.dmg` from the release
2. Mount it and copy the `.bundle` into your **Plugins** folder
3. Restart 4D

## Commands

### `butteraugli`

```4d
$heatmap:=butteraugli($image1; $image2)
```

| Parameter | Type | Description |
|---|---|---|
| `$image1` | Picture | Reference image |
| `$image2` | Picture | Comparison image |
| `$heatmap` | Picture | Difference heatmap as PNG |

**Example:**

```4d
var $img1; $img2; $heatmap : Picture
READ PICTURE FILE("ref.png"; $img1)
READ PICTURE FILE("modified.png"; $img2)
$heatmap:=butteraugli($img1; $img2)
WRITE PICTURE FILE("diff.png"; $heatmap)
```

## Building from Source

### Prerequisites

- CMake 3.20+
- Xcode (macOS) or Visual Studio 2022 (Windows)

### Clone

```bash
git clone --recurse-submodules https://github.com/{owner}/butteraugli-plugin.git
cd butteraugli-plugin
```

### Build (macOS)

```bash
cd butteraugli
mkdir -p cmake-build && cd cmake-build
cmake .. -DCMAKE_BUILD_TYPE=Release
cmake --build .
```

### Build (Windows)

```pwsh
cd butteraugli
mkdir cmake-build; cd cmake-build
cmake .. -A x64
cmake --build . --config Release
```

### Run Tests

Requires [tool4d](https://developer.4d.com/docs/Admin/cli/) (free, no license needed):

```bash
/path/to/tool4d --dataless --startup-method=test_all --project=$(pwd)/butteraugli-test/Project/butteraugli.4DProject
```

## CI/CD

Release preparation and publication are independent manual operations. Select the
branch/ref in Actions; neither operation dispatches another workflow.

| Workflow | Trigger | Purpose |
|---|---|---|
| `test.yml` | Tag push / manual | Build & test on macOS + Windows |
| `bump-version.yml` — **Bump** | Manual, required patch/minor/major choice (default patch) | Commit source versions to the selected branch; never build, tag or release |
| `release.yml` — **Build** | Manual, no custom inputs | Build the selected immutable SHA, sign, notarize, then create a new tag and release |

`VERSION` is the canonical `X.Y.Z` publication version. The original Bump changed
only that file; CMake's `MACOSX_BUNDLE_SHORT_VERSION_STRING` had consequently
remained stale at `1.0.0`. It now follows `VERSION`, without an intake version
increment. `MACOSX_BUNDLE_BUNDLE_VERSION` is independent build metadata and remains
`1.0.0`; Bump does not increment it. The source `Info.plist.in` keeps its CMake
references, and its short-version key is required. Its build key is optional and,
if absent, stays absent. Prebuilt bundle metadata is not source authority and is
never normalized by Bump.

Bump requires the selected branch still to advertise the selected event SHA,
creates exactly one source-only child commit, and checks Git's actual advertised
old SHA again in an executable pre-push hook. Concurrent advances, rewinds,
deletions and already-pushed results fail closed. Pushes are nonforce with both
`-c push.followTags=false` and `--no-follow-tags`. Generated commits use the Actions
bot identity and carry the Copilot co-author trailer.

Build keeps the Windows x64 CMake job followed by the macOS universal CMake job,
the deployment target `10.15`, static MSVC runtime, CoreGraphics framework,
SDK/dependency sources, copied resources and 4D test project. The explicit Unix
Makefiles generator keeps the macOS output at the canonical bundle path, rather
than a configuration-named sibling. Only known generated files/signature are
cleaned, never the whole bundle, Plugins, resources or dependencies. Exact
`arm64` and `x86_64` slices, fresh plist/resources, signing verification, successful
notary commands with JSON `Accepted`, and stapling must all pass.

Publication is serialized across all refs by one repository-wide concurrency
group with cancellation disabled (this does not promise FIFO ordering). There is
no default-branch-only restriction. Authenticated, paginated release enumeration
(including drafts) and exact remote-tag absence are checked before native work
and again after every native/sign/notary/staple gate. Existing releases or tags,
including same-SHA tag races, are errors. A create-only tag POST targets the
selected SHA, then a separate release POST creates a new release ID, followed by
separate ZIP and DMG uploads to that ID. Original release-note behavior is
preserved: empty body and no generated notes.

Initial preflight requires a clean checkout. Final publication verifies the
selected HEAD and both index and on-disk nongenerated inputs, while allowing only
the known native-generated bundle files and build/artifact paths to change.
Tracked generated bytes are retained, not reset to simulate cleanliness.

**Partial publication requires manual recovery.** No helper deletes, overwrites,
reuses, rolls back or retries a tag, release or asset. If a tag POST succeeds but a
later operation fails, inspect the reported tag/release ID and retained assets
manually, decide how to recover, and only then start a new Build. Authentication,
network, malformed API data and incomplete uploads fail explicitly.

Offline regression checks require Python 3, PyYAML, PowerShell (`pwsh`) and
actionlint. They use isolated Git fixtures and closed-PATH native stubs, with no
native tools, credentials or Actions dispatch:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s .github/tests -v
actionlint .github/workflows/*.yml
```

After committing a migration, the genuine source-bump proof runs the real helper
against a disposable bare origin with `safe.bareRepository=explicit`, then reruns
the entire suite and actionlint on that bumped checkout:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 .github/tests/prove_bump.py "$(git rev-parse HEAD)"
```

### Required Secrets (for `release.yml` only)

Configure these in **Settings → Secrets and variables → Actions**:

| Secret | Description |
|---|---|
| `APPLE_DEVELOPER_ID_CERTIFICATE` | Base64-encoded `.p12` Developer ID Application certificate |
| `APPLE_DEVELOPER_ID_CERTIFICATE_PASSWORD` | Password for the `.p12` export |
| `KEYCHAIN_PASSWORD` | Arbitrary password for the CI runner's temporary keychain |
| `NOTARYTOOL_APPLE_ID` | Apple ID email for notarization |
| `NOTARYTOOL_TEAM_ID` | Apple Developer Team ID (10-char alphanumeric) |
| `NOTARYTOOL_PASSWORD` | App-specific password from [appleid.apple.com](https://appleid.apple.com/account/manage) |

## License

Apache-2.0
