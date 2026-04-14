#pragma once

#include "FileRecord.hpp"
#include <cstddef>
#include <string>
#include <vector>

namespace chrono {

/// Mirrors the TOML [metadata] table.
struct ManifestMetadata {
    std::string     schema_version   = "1.0.0";
    std::string     tool_version     = "0.1.0";
    std::string     source_device;          ///< UIDevice.current.name
    std::string     source_folder;          ///< Original absolute path (informational)
    struct timespec export_timestamp = {};  ///< When the export was performed (UTC)
    std::string     timezone;               ///< IANA timezone identifier
    std::size_t     file_count       = 0;
    uint64_t        total_size_bytes = 0;
};

/// Complete manifest: metadata header + per-file records.
struct Manifest {
    ManifestMetadata         metadata;
    std::vector<FileRecord>  files;

    /// Convenience: recompute file_count and total_size_bytes from `files`.
    void recompute_totals() noexcept;
};

} // namespace chrono
