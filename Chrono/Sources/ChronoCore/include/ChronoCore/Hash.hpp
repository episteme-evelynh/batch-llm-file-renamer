#pragma once

#include <expected>       // C++23
#include <string>
#include <system_error>

namespace chrono {

/// Compute the SHA-256 digest of the file referred to by `fd`.
///
/// Strategy:
///  - Files > 1 MiB: mmap for efficient single-pass hashing.
///  - Files ≤ 1 MiB: read() loop with a 64 KiB buffer.
///
/// Uses CommonCrypto (CC_SHA256) on Darwin.
/// The fd is NOT closed by this function; caller retains ownership.
///
/// Returns a lowercase 64-character hex string, or an error_code on failure.
std::expected<std::string, std::error_code>
sha256_of_fd(int fd);

/// Convenience: hash a memory buffer directly (used in tests).
std::string sha256_of_bytes(const void* data, std::size_t len);

} // namespace chrono
