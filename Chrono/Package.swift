// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "Chrono",
    platforms: [
        .iOS(.v17),
        .macOS(.v14),
    ],
    products: [
        .library(name: "ChronoCore", targets: ["ChronoCore"]),
        .library(name: "ChronoShim", targets: ["ChronoShim"]),
        .executable(name: "chrono-cli", targets: ["ChronoCLI"]),
    ],
    targets: [
        // ── C++23 core library ─────────────────────────────────────────────
        .target(
            name: "ChronoCore",
            path: "Sources/ChronoCore",
            sources: ["src"],
            publicHeadersPath: "include",
            cxxSettings: [
                .define("TOML_ENABLE_UNRELEASED_FEATURES", to: "1"),
                .headerSearchPath("../../Vendor/tomlplusplus"),
                .headerSearchPath("include"),
                .unsafeFlags(["-std=c++23"]),
            ]
        ),

        // ── Objective-C++ shim ─────────────────────────────────────────────
        .target(
            name: "ChronoShim",
            dependencies: ["ChronoCore"],
            path: "Sources/ChronoShim",
            sources: ["src"],
            publicHeadersPath: "include",
            cxxSettings: [
                .headerSearchPath("../../Sources/ChronoCore/include"),
                .headerSearchPath("include"),
                .unsafeFlags(["-std=c++23"]),
            ]
        ),

        // ── macOS CLI executable ───────────────────────────────────────────
        .executableTarget(
            name: "ChronoCLI",
            dependencies: ["ChronoShim"],
            path: "ChronoCLI",
            swiftSettings: [
                .interoperabilityMode(.Cxx),
            ]
        ),

        // ── Unit tests: C++23 core ─────────────────────────────────────────
        .testTarget(
            name: "ChronoCoreTests",
            dependencies: ["ChronoCore"],
            path: "Tests/ChronoCoreTests",
            cxxSettings: [
                .headerSearchPath("../../Sources/ChronoCore/include"),
                .define("TOML_ENABLE_UNRELEASED_FEATURES", to: "1"),
                .headerSearchPath("../../Vendor/tomlplusplus"),
                .unsafeFlags(["-std=c++23"]),
            ]
        ),

        // ── Integration tests: ObjC++ shim ────────────────────────────────
        .testTarget(
            name: "ChronoShimTests",
            dependencies: ["ChronoShim"],
            path: "Tests/ChronoShimTests",
            swiftSettings: [
                .interoperabilityMode(.Cxx),
            ]
        ),
    ],
    cxxLanguageStandard: .cxx23
)
