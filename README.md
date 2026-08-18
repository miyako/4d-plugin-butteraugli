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

This project uses GitHub Actions for automated testing and releases:

| Workflow | Trigger | Purpose |
|---|---|---|
| `test.yml` | Tag push / manual | Build & test on macOS + Windows |
| `bump-version.yml` | Manual | Bump `VERSION`, commit, push tag |
| `release.yml` | `v*.*.*` tag | Build, sign, notarize, GitHub Release |

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
