// ChronoCoreTests.cpp — Unit tests for the C++23 ChronoCore library
//
// Compiled as a SwiftPM test target (XCTest is unavailable for pure C++ targets;
// tests use a lightweight hand-rolled harness compatible with ctest / swift test).
// On Darwin with Xcode the target can be switched to use XCTest via an
// Objective-C++ wrapper if needed.

#include <ChronoCore/FileRecord.hpp>
#include <ChronoCore/Manifest.hpp>
#include <ChronoCore/Traversal.hpp>
#include <ChronoCore/Hash.hpp>
#include <ChronoCore/TomlEmitter.hpp>

#include <cassert>
#include <cstdio>
#include <cstring>
#include <fcntl.h>
#include <sys/attr.h>
#include <sys/stat.h>
#include <unistd.h>

// ── Minimal test harness ─────────────────────────────────────────────────────

static int g_pass = 0, g_fail = 0;

#define TEST(name)   void test_##name()
#define RUN(name)    do { \
    std::printf("  %-50s", #name); \
    try { test_##name(); std::printf(" PASS\n"); ++g_pass; } \
    catch (const std::exception& e) { std::printf(" FAIL: %s\n", e.what()); ++g_fail; } \
    catch (...) { std::printf(" FAIL (unknown exception)\n"); ++g_fail; } \
} while (0)

#define ASSERT(expr) do { \
    if (!(expr)) throw std::runtime_error("Assertion failed: " #expr " at line " + std::to_string(__LINE__)); \
} while(0)

#define ASSERT_EQ(a, b) do { \
    if ((a) != (b)) throw std::runtime_error( \
        std::string("Expected equality: ") + #a + " != " + #b + " at line " + std::to_string(__LINE__)); \
} while(0)

// ── Helper: create a temp file with known content ────────────────────────────

static std::string make_temp_file(const char* content, std::size_t len) {
    char tmpl[] = "/tmp/chrono_test_XXXXXX";
    int fd = ::mkstemp(tmpl);
    if (fd < 0) throw std::runtime_error("mkstemp failed");
    ::write(fd, content, len);
    ::close(fd);
    return tmpl;
}

// ── FileRecord ────────────────────────────────────────────────────────────────

TEST(filerecord_default_construction) {
    chrono::FileRecord r;
    ASSERT(r.relative_path.empty());
    ASSERT_EQ(r.size_bytes, uint64_t{0});
    ASSERT(!r.date_created.has_value());
    ASSERT(!r.date_modified.has_value());
    ASSERT(!r.date_added.has_value());
}

TEST(filerecord_equality) {
    chrono::FileRecord a, b;
    a.relative_path = "Photos/IMG_001.HEIC";
    a.size_bytes    = 512000;
    a.sha256_hex    = "abc123";
    b = a;  // copy assignment
    ASSERT(a == b);

    b.size_bytes = 999;
    ASSERT(a != b);
}

TEST(filerecord_copy_semantics) {
    chrono::FileRecord orig;
    orig.relative_path = "test.txt";
    struct timespec ts{ 1700000000, 123456789 };
    orig.date_created = ts;

    chrono::FileRecord copy = orig;
    ASSERT(copy == orig);

    copy.relative_path = "other.txt";
    ASSERT(orig.relative_path == "test.txt");   // no aliasing
}

TEST(timespec_equal) {
    struct timespec a{ 100, 999 }, b{ 100, 999 }, c{ 100, 1000 };
    ASSERT(chrono::timespec_equal(a, b));
    ASSERT(!chrono::timespec_equal(a, c));
}

// ── Hash ─────────────────────────────────────────────────────────────────────

TEST(sha256_empty_string) {
    // SHA-256("") = e3b0c44298fc1c149afbf4c8996fb924...
    const std::string expected =
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855";
    std::string got = chrono::sha256_of_bytes("", 0);
    ASSERT_EQ(got, expected);
}

TEST(sha256_known_input) {
    // SHA-256("abc") = ba7816bf8f01cfea414140de5dae2ec73b00361b...
    const std::string expected =
        "ba7816bf8f01cfea414140de5dae2ec73b00361bbccd4a21596b188e2286521f";
    std::string got = chrono::sha256_of_bytes("abc", 3);
    ASSERT_EQ(got, expected);
}

TEST(sha256_of_fd_small_file) {
    const char content[] = "hello chrono";
    std::string path = make_temp_file(content, sizeof(content) - 1);
    int fd = ::open(path.c_str(), O_RDONLY);
    ASSERT(fd >= 0);
    auto result = chrono::sha256_of_fd(fd);
    ::close(fd);
    ::unlink(path.c_str());
    ASSERT(result.has_value());
    ASSERT_EQ(result->size(), std::size_t{64});
    // All hex characters
    for (char c : *result) {
        ASSERT((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'));
    }
}

TEST(sha256_of_fd_empty_file) {
    std::string path = make_temp_file("", 0);
    int fd = ::open(path.c_str(), O_RDONLY);
    ASSERT(fd >= 0);
    auto result = chrono::sha256_of_fd(fd);
    ::close(fd);
    ::unlink(path.c_str());
    ASSERT(result.has_value());
    const std::string expected =
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855";
    ASSERT_EQ(*result, expected);
}

// ── Traversal ─────────────────────────────────────────────────────────────────

TEST(traversal_temp_directory) {
    // Create: /tmp/chrono_trav_XXXXXX/
    //           file_a.txt
    //           subdir/
    //             file_b.txt
    char base[] = "/tmp/chrono_trav_XXXXXX";
    ASSERT(::mkdtemp(base) != nullptr);

    std::string basePath(base);
    std::string subdir = basePath + "/subdir";
    ::mkdir(subdir.c_str(), 0755);

    auto write_file = [](const std::string& p, const char* content) {
        int fd = ::open(p.c_str(), O_WRONLY | O_CREAT | O_TRUNC, 0644);
        if (fd >= 0) { ::write(fd, content, ::strlen(content)); ::close(fd); }
    };
    write_file(basePath + "/file_a.txt", "hello");
    write_file(subdir   + "/file_b.txt", "world");
    // Dot-prefixed — should be skipped
    write_file(basePath + "/.DS_Store", "ignored");

    chrono::TraversalConfig cfg;
    cfg.compute_hash = false;  // speed up test
    auto result = chrono::enumerate(basePath, cfg);

    // Clean up
    ::unlink((basePath + "/file_a.txt").c_str());
    ::unlink((subdir   + "/file_b.txt").c_str());
    ::unlink((basePath + "/.DS_Store").c_str());
    ::rmdir(subdir.c_str());
    ::rmdir(base);

    ASSERT(result.has_value());
    ASSERT_EQ(result->size(), std::size_t{2});

    // Both paths must be present
    bool found_a = false, found_b = false;
    for (const auto& r : *result) {
        if (r.relative_path == "file_a.txt")         found_a = true;
        if (r.relative_path == "subdir/file_b.txt")  found_b = true;
        ASSERT(r.posix_mode != 0);
        ASSERT(r.size_bytes > 0);
    }
    ASSERT(found_a && found_b);
}

// ── Manifest ─────────────────────────────────────────────────────────────────

TEST(manifest_recompute_totals) {
    chrono::Manifest m;
    chrono::FileRecord r1, r2;
    r1.size_bytes = 100;
    r2.size_bytes = 200;
    m.files = { r1, r2 };
    m.recompute_totals();
    ASSERT_EQ(m.metadata.file_count, std::size_t{2});
    ASSERT_EQ(m.metadata.total_size_bytes, uint64_t{300});
}

// ── TOML round-trip ──────────────────────────────────────────────────────────

TEST(toml_emit_and_parse_roundtrip) {
    chrono::Manifest manifest;
    manifest.metadata.schema_version    = "1.0.0";
    manifest.metadata.tool_version      = "0.1.0";
    manifest.metadata.source_device     = "iPad Pro (M4)";
    manifest.metadata.source_folder     = "/private/var/mobile/test";
    manifest.metadata.export_timestamp  = { 1700000000, 0 };
    manifest.metadata.timezone          = "America/New_York";

    chrono::FileRecord rec;
    rec.relative_path   = "Photos/IMG_001.HEIC";
    rec.size_bytes      = 524288;
    rec.sha256_hex      = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855";
    rec.date_created    = { 1700000100, 123000000 };
    rec.date_modified   = { 1700000200, 456000000 };
    rec.date_added      = { 1700000300, 789000000 };
    rec.posix_mode      = 0100644;
    rec.uti             = "public.heic";
    manifest.files      = { rec };
    manifest.recompute_totals();

    // Emit
    auto emit_result = chrono::emit(manifest);
    ASSERT(emit_result.has_value());
    ASSERT(!emit_result->empty());

    // Parse
    auto parse_result = chrono::parse_manifest(*emit_result);
    ASSERT(parse_result.has_value());

    const auto& m2 = *parse_result;
    ASSERT_EQ(m2.metadata.schema_version, std::string{"1.0.0"});
    ASSERT_EQ(m2.metadata.source_device,  std::string{"iPad Pro (M4)"});
    ASSERT_EQ(m2.files.size(), std::size_t{1});

    const auto& r2 = m2.files[0];
    ASSERT_EQ(r2.relative_path, std::string{"Photos/IMG_001.HEIC"});
    ASSERT_EQ(r2.size_bytes,    uint64_t{524288});
    ASSERT_EQ(r2.sha256_hex,    rec.sha256_hex);
    ASSERT(r2.date_created.has_value());
    // Allow ≤1 second tolerance after nanosecond round-trip through TOML datetime
    ASSERT(std::abs(static_cast<long>(r2.date_created->tv_sec) - 1700000100L) <= 1);
}

// ── Main ─────────────────────────────────────────────────────────────────────

int main() {
    std::printf("=== ChronoCoreTests ===\n");

    RUN(filerecord_default_construction);
    RUN(filerecord_equality);
    RUN(filerecord_copy_semantics);
    RUN(timespec_equal);
    RUN(sha256_empty_string);
    RUN(sha256_known_input);
    RUN(sha256_of_fd_small_file);
    RUN(sha256_of_fd_empty_file);
    RUN(traversal_temp_directory);
    RUN(manifest_recompute_totals);
    RUN(toml_emit_and_parse_roundtrip);

    std::printf("\n%d passed, %d failed\n", g_pass, g_fail);
    return g_fail == 0 ? 0 : 1;
}
