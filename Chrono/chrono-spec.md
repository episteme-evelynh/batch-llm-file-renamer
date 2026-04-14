# chrono-spec.md — Chrono Project Specification
# Source of truth for Claude Code. Do not deviate without explicit instruction.

## 1. Project identity

- **Name**: Chrono
- **Purpose**: Duplicate files on iOS/iPadOS while preserving Date Created,
  Date Modified, and Date Added timestamps. Produce a TOML v1.1.0 sidecar
  manifest. A macOS companion CLI re-applies timestamps from the manifest.
- **Deployment**: App Store or sideload (both sandbox-constrained).

## 2. Targets

| Target | Platform | Type | Language |
|--------|----------|------|----------|
| Chrono (iOS app) | iOS/iPadOS 26 | SwiftUI app | Swift 6.3 + ObjC++ + C++23 |
| chrono-cli | macOS 26 | command-line tool | Swift 6.3 + C++23 |
| ChronoCore | cross-platform | SwiftPM library (C++23) | C++23 |
| ChronoShim | iOS + macOS | SwiftPM library (ObjC++) | Objective-C++ |

## 3. Toolchain versions

- Xcode: 26.4
- Swift: 6.3 (strict concurrency, Swift 6 language mode)
- C++: C++23 (CLANG_CXX_LANGUAGE_STANDARD = c++23)
- TOML: v1.1.0 (toml++ with TOML_ENABLE_UNRELEASED_FEATURES = 1)
- Minimum deployment: iOS/iPadOS 26.0, macOS 26.0

## 4. Architectural layers (top to bottom)

```
┌─────────────────────────────────────────────┐
│  Swift UI + orchestration (Chrono app)      │
│  - SwiftUI views, folder picker, progress   │
│  - Security-scoped URL lifecycle            │
│  - Share sheet / Files export               │
├─────────────────────────────────────────────┤
│  Objective-C++ shim (ChronoShim, .mm)       │
│  - Swift-facing @objc API                   │
│  - fd lifetime (RAII via C++ guard)         │
│  - POSIX calls: open/read/write/close,      │
│    stat/lstat, setattrlist/fsetattrlist,     │
│    utimensat/futimens                       │
│  - errno → NSError translation              │
│  - C++ exception → NSError shielding        │
├─────────────────────────────────────────────┤
│  C++23 core (ChronoCore)                    │
│  - Directory traversal (opendir/readdir)    │
│  - Stat reading (all four timestamps)       │
│  - SHA-256 hashing (CommonCrypto or own)    │
│  - TOML v1.1.0 emission (toml++)           │
│  - Data structures: FileRecord, Manifest    │
├─────────────────────────────────────────────┤
│  POSIX I/O + Darwin extensions              │
│  - open/read/write/close, fcntl             │
│  - stat/lstat, getattrlist/setattrlist      │
│  - utimensat/futimens                       │
│  - mmap (optional, for large-file hashing)  │
└─────────────────────────────────────────────┘
```

## 5. Directory layout

```
Chrono/
├── Package.swift                    # SwiftPM manifest for ChronoCore + ChronoShim
├── chrono-spec.md                   # THIS FILE
├── Sources/
│   ├── ChronoCore/                  # C++23 library
│   │   ├── include/
│   │   │   └── ChronoCore/
│   │   │       ├── FileRecord.hpp
│   │   │       ├── Manifest.hpp
│   │   │       ├── Traversal.hpp
│   │   │       ├── TomlEmitter.hpp
│   │   │       └── Hash.hpp
│   │   └── src/
│   │       ├── FileRecord.cpp
│   │       ├── Manifest.cpp
│   │       ├── Traversal.cpp
│   │       ├── TomlEmitter.cpp
│   │       └── Hash.cpp
│   └── ChronoShim/                  # Objective-C++ bridge
│       ├── include/
│       │   └── ChronoShim/
│       │       └── ChronoShim.h     # @objc-visible API
│       └── src/
│           └── ChronoShim.mm
├── ChronoApp/                       # Xcode iOS app target
│   ├── ChronoApp.swift
│   ├── ContentView.swift
│   ├── FolderPickerView.swift
│   ├── ExportView.swift
│   ├── Assets.xcassets/
│   ├── Info.plist
│   └── Chrono.entitlements
├── ChronoCLI/                       # macOS CLI target
│   └── main.swift
├── Tests/
│   ├── ChronoCoreTests/
│   │   └── ChronoCoreTests.cpp
│   └── ChronoShimTests/
│       └── ChronoShimTests.swift
└── Vendor/
    └── tomlplusplus/                # toml++ header-only library (vendored)
        └── toml.hpp                 # REPLACE with real header from github.com/marzer/tomlplusplus
```

## 6. Public API boundary: Swift ↔ Objective-C++ shim

See `Sources/ChronoShim/include/ChronoShim/ChronoShim.h` for the full
`@objc` interface. Key classes:

- **CHTimestampRecord** — read-only snapshot of one file's timestamps + metadata
- **CHManifestEntry**   — one [[file]] row from a parsed TOML manifest
- **CHManifestHeader**  — [metadata] section from a parsed manifest
- **CHParsedManifest**  — full parsed manifest (header + entries array)
- **CHChronoEngine**    — the main engine; all operations go through this class

## 7. TOML v1.1.0 manifest schema

```toml
# Chrono Sidecar Manifest — TOML v1.1.0
# Machine-generated. Do not edit.

[metadata]
schema_version    = "1.0.0"
tool_version      = "0.1.0"
source_device     = "iPad Pro (M4)"
source_folder     = "/private/var/mobile/Containers/..."
export_timestamp  = 2026-04-14T12:00:00.000000000Z
timezone          = "America/New_York"
file_count        = 3
total_size_bytes  = 1048576

[[file]]
relative_path     = "Photos/IMG_0001.HEIC"
size_bytes        = 524288
sha256            = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
date_created      = 2026-01-15T08:30:00.000000000Z
date_modified     = 2026-02-20T14:15:30.500000000Z
date_added        = 2026-01-15T08:30:01.000000000Z
posix_mode        = 0o100644
uti               = "public.heic"
```

### Schema field reference

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `metadata.schema_version` | string | yes | Manifest schema version (semver) |
| `metadata.tool_version` | string | yes | Chrono app version (semver) |
| `metadata.source_device` | string | yes | UIDevice.current.name |
| `metadata.source_folder` | string | yes | Original absolute path (informational) |
| `metadata.export_timestamp` | offset-date-time | yes | When export was performed |
| `metadata.timezone` | string | yes | IANA timezone of source device |
| `metadata.file_count` | integer | yes | Number of [[file]] entries |
| `metadata.total_size_bytes` | integer | yes | Sum of all file sizes |
| `file.relative_path` | string | yes | Path relative to export root |
| `file.size_bytes` | integer | yes | File size in bytes |
| `file.sha256` | string | yes | Hex-encoded SHA-256 for pairing |
| `file.date_created` | offset-date-time | yes | Birth time (creation date) |
| `file.date_modified` | offset-date-time | yes | Content modification date |
| `file.date_added` | offset-date-time | no | Added-to-directory date (may be nil) |
| `file.posix_mode` | integer | no | POSIX permission bits |
| `file.uti` | string | no | Uniform Type Identifier |

## 8. Timestamp capability matrix

| Timestamp | Read iOS | Write iOS | Read macOS | Write macOS | iOS API | macOS API |
|-----------|----------|-----------|------------|-------------|---------|-----------|
| Date Created | ✅ | ✅ | ✅ | ✅ | URLResourceValues / fsetattrlist ATTR_CMN_CRTIME | setattrlist ATTR_CMN_CRTIME |
| Date Modified | ✅ | ✅ | ✅ | ✅ | URLResourceValues / utimensat | utimensat / setattrlist |
| Date Added | ✅ | ⚠️ likely | ✅ | ✅ | NSURL setResourceValue / fsetattrlist ATTR_CMN_ADDEDTIME | setattrlist ATTR_CMN_ADDEDTIME |

⚠️ = SDK header declares read-write from iOS 13+; shared XNU kernel has no
iOS-specific restriction; not community-verified on-device. Implemented with
read-back verification and manifest fallback (xattr "com.chrono.dateAdded").

## 9. Sandbox and access requirements

### Security-scoped URL lifecycle
1. Present UIDocumentPickerViewController with UTType.folder
2. Receive URL in FolderPickerView.Coordinator.documentPicker(_:didPickDocumentsAt:)
3. Call url.startAccessingSecurityScopedResource()
4. Create security-scoped bookmark, store in UserDefaults
5. Perform all POSIX / Foundation file operations
6. Call url.stopAccessingSecurityScopedResource() in defer block

### Security-scoped bookmarks (persist across launches)
- Create:  url.bookmarkData(options: .withSecurityScope, ...)
- Resolve: URL(resolvingBookmarkData:options:.withSecurityScope)
- Store bookmark Data in UserDefaults key "com.chrono.sourceFolderBookmark"
- Check bookmarkDataIsStale and refresh if stale

### Info.plist keys
- UIFileSharingEnabled = YES (expose app Documents to Files app)
- LSSupportsOpeningDocumentsInPlace = YES (open-in-place support)
- No entitlement needed for document picker or bookmarks on iOS

### NSFileCoordinator
- Recommended for cloud-backed file providers (iCloud, Dropbox)
- Not required for local-only operations
- Use coordinateAccessWithIntents for iCloud-sourced files

## 10. Error translation rules (ObjC++ shim)

| Source | Target | Rule |
|--------|--------|------|
| POSIX errno | NSError | Domain: NSPOSIXErrorDomain, code: errno value, userInfo: strerror(errno) |
| C++ std::exception | NSError | Domain: "com.chrono.cpp", code: 1, userInfo: e.what() |
| C++ unknown throw | NSError | Domain: "com.chrono.cpp", code: 2, userInfo: "Unknown C++ exception" |
| NSException from ObjC | NSError | Domain: "com.chrono.cpp", code: 3, userInfo: ex.reason |
| setattrlist failure | NSError | Domain: NSPOSIXErrorDomain, code: errno, userInfo includes attr name |
| TOML emission failure | NSError | Domain: "com.chrono.toml", code: 1, userInfo: description |

All ObjC++ methods use the Cocoa NSError** out-parameter pattern.
All C++ calls in .mm files are wrapped in @try/@catch at the shim boundary.

## 11. Build configuration notes

### Package.swift
- `cxxLanguageStandard: .cxx23` at package level
- ChronoCore: `-DTOML_ENABLE_UNRELEASED_FEATURES=1`, headerSearchPath to Vendor/
- ChronoShim: depends on ChronoCore; compiled as ObjC++
- ChronoCLI:  `.interoperabilityMode(.Cxx)` in swiftSettings

### Xcode project (iOS app target)
- SWIFT_OBJC_INTEROP_MODE = objcxx
- CLANG_CXX_LANGUAGE_STANDARD = c++23
- Link ChronoShim (and transitively ChronoCore)

### toml++ vendor
- Place real toml.hpp at Vendor/tomlplusplus/toml.hpp
- Source: https://github.com/marzer/tomlplusplus/releases (v3.4.0+)
- The stub in the repo is for type-checking only; replace before building

## 12. Test plan outline

### Unit tests (ChronoCoreTests)
- FileRecord: roundtrip construction, equality, copy
- Traversal: enumerate a temp directory with known structure
- Hash: SHA-256 of known inputs matches expected hex
- TomlEmitter: emit a Manifest, parse result, verify fields

### Integration tests (ChronoShimTests)
- Create temp files with known timestamps via setattrlist
- Enumerate via CHChronoEngine, verify timestamps match
- Duplicate file, verify timestamps on duplicate
- Emit manifest, parse TOML string, verify schema compliance
- Round-trip: set timestamps → read → emit TOML → parse → apply → verify
- Error path: non-existent folder returns NSPOSIXErrorDomain error

### iOS UI tests (manual / XCUITest)
- Folder picker presents and returns valid URL
- Security-scoped access permits enumeration
- Export produces manifest.toml alongside duplicated folder
- Share sheet offers AirDrop / Files save

### macOS CLI tests
- Parse sample manifest, verify all fields
- Apply timestamps to temp files, verify with stat/getattrlist
- SHA-256 mismatch triggers warning but continues
- Missing file triggers warning with clear message
- Date Added restoration verified via getattrlist ATTR_CMN_ADDEDTIME
- Verify pass (--verify flag) detects sub-second mismatch within tolerance
