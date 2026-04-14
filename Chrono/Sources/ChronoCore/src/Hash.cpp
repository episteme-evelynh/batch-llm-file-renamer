#include <ChronoCore/Hash.hpp>

#include <cerrno>
#include <cstdint>
#include <cstring>
#include <fcntl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>

#if defined(__APPLE__)
#  include <CommonCrypto/CommonCrypto.h>
#  define CHRONO_SHA256_CTX        CC_SHA256_CTX
#  define CHRONO_SHA256_INIT(ctx)  CC_SHA256_Init(ctx)
#  define CHRONO_SHA256_UPDATE(ctx, data, len) CC_SHA256_Update(ctx, data, len)
#  define CHRONO_SHA256_FINAL(digest, ctx) CC_SHA256_Final(digest, ctx)
#  define CHRONO_SHA256_DIGEST_LENGTH CC_SHA256_DIGEST_LENGTH
#else
#  include <openssl/sha.h>
#  define CHRONO_SHA256_CTX        SHA256_CTX
#  define CHRONO_SHA256_INIT(ctx)  SHA256_Init(ctx)
#  define CHRONO_SHA256_UPDATE(ctx, data, len) SHA256_Update(ctx, data, static_cast<size_t>(len))
#  define CHRONO_SHA256_FINAL(digest, ctx) SHA256_Final(digest, ctx)
#  define CHRONO_SHA256_DIGEST_LENGTH SHA256_DIGEST_LENGTH
#endif

#include <array>
#include <string>

namespace chrono {

static constexpr std::size_t kMmapThreshold = 1u * 1024u * 1024u;  // 1 MiB
static constexpr std::size_t kReadBufSize   = 64u * 1024u;          // 64 KiB

// ── Hex encoding helper ─────────────────────────────────────────────────────

static std::string bytes_to_hex(const uint8_t* data, std::size_t len) {
    static constexpr char kHex[] = "0123456789abcdef";
    std::string out;
    out.reserve(len * 2);
    for (std::size_t i = 0; i < len; ++i) {
        out += kHex[(data[i] >> 4) & 0xF];
        out += kHex[(data[i]     ) & 0xF];
    }
    return out;
}

// ── Public implementation ───────────────────────────────────────────────────

std::string sha256_of_bytes(const void* data, std::size_t len) {
    uint8_t digest[CHRONO_SHA256_DIGEST_LENGTH];
    CHRONO_SHA256_CTX ctx;
    CHRONO_SHA256_INIT(&ctx);
    CHRONO_SHA256_UPDATE(&ctx, data, static_cast<CC_LONG>(len));
    CHRONO_SHA256_FINAL(digest, &ctx);
    return bytes_to_hex(digest, sizeof(digest));
}

std::expected<std::string, std::error_code>
sha256_of_fd(int fd) {
    // Determine file size
    struct stat st{};
    if (::fstat(fd, &st) != 0) {
        return std::unexpected(std::error_code(errno, std::system_category()));
    }
    const auto file_size = static_cast<std::size_t>(st.st_size);

    uint8_t digest[CHRONO_SHA256_DIGEST_LENGTH];
    CHRONO_SHA256_CTX ctx;
    CHRONO_SHA256_INIT(&ctx);

    if (file_size == 0) {
        // Empty file: finalise immediately
        CHRONO_SHA256_FINAL(digest, &ctx);
        return bytes_to_hex(digest, sizeof(digest));
    }

    if (file_size >= kMmapThreshold) {
        // mmap path for large files
        void* mapped = ::mmap(nullptr, file_size, PROT_READ, MAP_PRIVATE, fd, 0);
        if (mapped == MAP_FAILED) {
            return std::unexpected(std::error_code(errno, std::system_category()));
        }
        ::madvise(mapped, file_size, MADV_SEQUENTIAL);
        CHRONO_SHA256_UPDATE(&ctx, mapped, static_cast<CC_LONG>(file_size));
        ::munmap(mapped, file_size);
    } else {
        // read() loop for small files
        // Seek to beginning first in case fd position is non-zero
        if (::lseek(fd, 0, SEEK_SET) == -1) {
            return std::unexpected(std::error_code(errno, std::system_category()));
        }
        std::array<uint8_t, kReadBufSize> buf{};
        ssize_t n;
        while ((n = ::read(fd, buf.data(), buf.size())) > 0) {
            CHRONO_SHA256_UPDATE(&ctx, buf.data(), static_cast<CC_LONG>(n));
        }
        if (n < 0) {
            return std::unexpected(std::error_code(errno, std::system_category()));
        }
    }

    CHRONO_SHA256_FINAL(digest, &ctx);
    return bytes_to_hex(digest, sizeof(digest));
}

} // namespace chrono
