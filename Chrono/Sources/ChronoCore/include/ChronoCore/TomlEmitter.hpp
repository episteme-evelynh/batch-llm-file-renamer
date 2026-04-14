#pragma once

#include "Manifest.hpp"
#include <expected>       // C++23
#include <string>
#include <system_error>

namespace chrono {

/// Serialize a Manifest to a TOML v1.1.0 string.
///
/// Uses toml++ with TOML_ENABLE_UNRELEASED_FEATURES=1.
/// Timestamps are encoded as RFC 3339 offset-date-time values at UTC (Z suffix)
/// with nanosecond fractional-second precision.
///
/// Returns the formatted TOML text, or an error_code on failure.
std::expected<std::string, std::error_code>
emit(const Manifest& manifest);

/// Parse a TOML v1.1.0 string back into a Manifest.
/// Used by the macOS CLI to read manifests produced by the iOS app.
std::expected<Manifest, std::error_code>
parse_manifest(const std::string& toml_text);

} // namespace chrono
