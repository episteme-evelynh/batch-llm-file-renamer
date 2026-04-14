#pragma once

#include <cstdint>
#include <optional>
#include <string>
#include <sys/stat.h>   // mode_t, struct timespec

namespace chrono {

/// Mirrors the TOML [[file]] schema entry. Represents one file's metadata and
/// three Darwin timestamps. All timestamps are stored as struct timespec
/// (seconds + nanoseconds) in UTC.
struct FileRecord {
    // ── Identity ───────────────────────────────────────────────────────────
    std::string  relative_path;   ///< Path relative to the enumerated root
    uint64_t     size_bytes = 0;  ///< File size reported by stat
    std::string  sha256_hex;      ///< Lowercase hex-encoded SHA-256 digest

    // ── Darwin timestamps ──────────────────────────────────────────────────
    std::optional<struct timespec> date_created;   ///< st_birthtimespec / ATTR_CMN_CRTIME
    std::optional<struct timespec> date_modified;  ///< st_mtimespec / ATTR_CMN_MODTIME
    std::optional<struct timespec> date_added;     ///< ATTR_CMN_ADDEDTIME (may be absent)

    // ── File attributes ────────────────────────────────────────────────────
    mode_t                    posix_mode = 0;  ///< st_mode (permission bits)
    std::optional<std::string> uti;            ///< Uniform Type Identifier (if known)

    // ── Constructors / comparison ──────────────────────────────────────────
    FileRecord() = default;
    FileRecord(const FileRecord&) = default;
    FileRecord(FileRecord&&) noexcept = default;
    FileRecord& operator=(const FileRecord&) = default;
    FileRecord& operator=(FileRecord&&) noexcept = default;

    bool operator==(const FileRecord&) const noexcept;
    bool operator!=(const FileRecord& o) const noexcept { return !(*this == o); }
};

/// Compare two timespec values. Returns true if they encode the same instant.
inline bool timespec_equal(const struct timespec& a, const struct timespec& b) noexcept {
    return a.tv_sec == b.tv_sec && a.tv_nsec == b.tv_nsec;
}

} // namespace chrono
