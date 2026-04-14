#include <ChronoCore/Traversal.hpp>
#include <ChronoCore/Hash.hpp>

#include <cerrno>
#include <cstring>
#include <dirent.h>
#include <fcntl.h>
#include <sys/attr.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>

#include <stack>
#include <string>

namespace chrono {

// ── getattrlist helper ──────────────────────────────────────────────────────

/// Buffer layout returned by getattrlist for ATTR_CMN_ADDEDTIME.
struct AttrBufAddedTime {
    uint32_t        length;   // returned-attributes length field
    struct timespec addedtime;
};

static std::optional<struct timespec>
read_added_time(const std::string& path) noexcept {
    struct attrlist al{};
    al.bitmapcount = ATTR_BIT_MAP_COUNT;
    al.commonattr  = ATTR_CMN_ADDEDTIME;

    AttrBufAddedTime buf{};
    if (::getattrlist(path.c_str(), &al, &buf, sizeof(buf), 0) != 0) {
        return std::nullopt;
    }
    return buf.addedtime;
}

// ── Recursive enumeration ───────────────────────────────────────────────────

std::expected<std::vector<FileRecord>, std::error_code>
enumerate(const std::string& base_path, const TraversalConfig& config) {
    std::vector<FileRecord> results;

    // Stack holds (absolute_path, relative_prefix)
    struct Frame {
        std::string abs_path;
        std::string rel_prefix;
    };
    std::stack<Frame> stack;
    stack.push({base_path, ""});

    while (!stack.empty()) {
        auto [abs, rel_prefix] = stack.top();
        stack.pop();

        DIR* dir = ::opendir(abs.c_str());
        if (!dir) {
            return std::unexpected(
                std::error_code(errno, std::system_category()));
        }

        struct dirent* entry;
        while ((entry = ::readdir(dir)) != nullptr) {
            std::string name(entry->d_name);

            // Always skip '.' and '..'
            if (name == "." || name == "..") continue;

            // Optionally skip dot-prefixed entries
            if (config.skip_dot_prefixed && !name.empty() && name[0] == '.') continue;

            std::string abs_child  = abs + "/" + name;
            std::string rel_child  = rel_prefix.empty() ? name : rel_prefix + "/" + name;

            struct stat st{};
            if (::lstat(abs_child.c_str(), &st) != 0) {
                // Non-fatal: skip unreadable entries
                continue;
            }

            if (S_ISDIR(st.st_mode)) {
                if (config.recursive) {
                    stack.push({abs_child, rel_child});
                }
                continue;
            }

            if (!S_ISREG(st.st_mode)) continue;  // Skip symlinks, sockets, etc.

            FileRecord rec;
            rec.relative_path  = rel_child;
            rec.size_bytes     = static_cast<uint64_t>(st.st_size);
            rec.posix_mode     = st.st_mode;

#if defined(__APPLE__)
            rec.date_created   = st.st_birthtimespec;
            rec.date_modified  = st.st_mtimespec;
#else
            // Fallback for non-Darwin (st_birthtimespec not standard)
            rec.date_modified  = st.st_mtim;
#endif

            // Read Date Added via getattrlist
            rec.date_added = read_added_time(abs_child);

            // Compute SHA-256 if requested
            if (config.compute_hash) {
                int fd = ::open(abs_child.c_str(), O_RDONLY | O_CLOEXEC);
                if (fd >= 0) {
                    auto hash_result = sha256_of_fd(fd);
                    ::close(fd);
                    if (hash_result) {
                        rec.sha256_hex = std::move(*hash_result);
                    }
                }
            }

            results.push_back(std::move(rec));
        }

        ::closedir(dir);
    }

    return results;
}

} // namespace chrono
