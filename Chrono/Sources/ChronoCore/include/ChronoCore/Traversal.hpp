#pragma once

#include "FileRecord.hpp"
#include <expected>       // C++23
#include <string>
#include <system_error>
#include <vector>

namespace chrono {

/// Configuration for directory traversal.
struct TraversalConfig {
    bool skip_dot_prefixed = true;   ///< Skip entries whose names start with '.'
    bool recursive         = true;   ///< Descend into subdirectories
    bool compute_hash      = true;   ///< Compute SHA-256 for each file
};

/// Enumerate all regular files beneath `base_path` (which is already open as
/// `dir_fd`). Populates FileRecord::relative_path, size_bytes, timestamps, and
/// posix_mode for each file. If config.compute_hash is true, sha256_hex is also
/// set by opening each file fd and calling sha256_of_fd().
///
/// Uses opendir/readdir (POSIX). getattrlist() reads ATTR_CMN_ADDEDTIME.
///
/// Returns the accumulated records, or an error_code on failure.
std::expected<std::vector<FileRecord>, std::error_code>
enumerate(const std::string& base_path, const TraversalConfig& config = {});

} // namespace chrono
