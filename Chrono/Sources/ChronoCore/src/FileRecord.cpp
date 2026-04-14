#include <ChronoCore/FileRecord.hpp>

namespace chrono {

bool FileRecord::operator==(const FileRecord& o) const noexcept {
    // Compare optional timespecs
    auto ts_eq = [](const std::optional<struct timespec>& a,
                    const std::optional<struct timespec>& b) -> bool {
        if (a.has_value() != b.has_value()) return false;
        if (!a.has_value()) return true;
        return timespec_equal(*a, *b);
    };

    return relative_path   == o.relative_path
        && size_bytes       == o.size_bytes
        && sha256_hex       == o.sha256_hex
        && ts_eq(date_created,  o.date_created)
        && ts_eq(date_modified, o.date_modified)
        && ts_eq(date_added,    o.date_added)
        && posix_mode       == o.posix_mode
        && uti              == o.uti;
}

} // namespace chrono
