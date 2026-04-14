#include <ChronoCore/Manifest.hpp>
#include <numeric>

namespace chrono {

void Manifest::recompute_totals() noexcept {
    metadata.file_count = files.size();
    metadata.total_size_bytes = std::accumulate(
        files.begin(), files.end(), uint64_t{0},
        [](uint64_t acc, const FileRecord& r) { return acc + r.size_bytes; });
}

} // namespace chrono
