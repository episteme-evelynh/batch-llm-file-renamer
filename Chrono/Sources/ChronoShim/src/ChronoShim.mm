// ChronoShim.mm — Objective-C++ bridge between Swift/ObjC and ChronoCore C++23
//
// Every public method is wrapped in @try/@catch (NSException) with an inner
// C++ try/catch (std::exception) to guarantee no exception crosses the
// Swift ↔ ObjC boundary.

#import "ChronoShim/ChronoShim.h"

// ── Private mutable interfaces (internal .mm use only) ──────────────────────

@interface CHManifestEntry ()
@property (nonatomic, readwrite) NSString *relativePath;
@property (nonatomic, readwrite) uint64_t  sizeBytes;
@property (nonatomic, readwrite) NSString *sha256;
@property (nonatomic, readwrite, nullable) NSDate *dateCreated;
@property (nonatomic, readwrite, nullable) NSDate *dateModified;
@property (nonatomic, readwrite, nullable) NSDate *dateAdded;
@end

@interface CHManifestHeader ()
@property (nonatomic, readwrite) NSString  *schemaVersion;
@property (nonatomic, readwrite) NSString  *toolVersion;
@property (nonatomic, readwrite) NSString  *sourceDevice;
@property (nonatomic, readwrite) NSString  *sourceFolder;
@property (nonatomic, readwrite, nullable) NSDate *exportTimestamp;
@property (nonatomic, readwrite) NSString  *timezone;
@property (nonatomic, readwrite) NSInteger  fileCount;
@property (nonatomic, readwrite) uint64_t   totalSizeBytes;
@end

@interface CHParsedManifest ()
@property (nonatomic, readwrite) CHManifestHeader           *header;
@property (nonatomic, readwrite) NSArray<CHManifestEntry *> *entries;
@end

#include <ChronoCore/FileRecord.hpp>
#include <ChronoCore/Manifest.hpp>
#include <ChronoCore/Traversal.hpp>
#include <ChronoCore/Hash.hpp>
#include <ChronoCore/TomlEmitter.hpp>

#include <cerrno>
#include <cstring>
#include <ctime>
#include <fcntl.h>
#include <sys/attr.h>
#include <sys/clonefile.h>   // clonefile() — available macOS 10.12+, iOS 10+
#include <sys/mount.h>
#include <sys/stat.h>
#include <sys/xattr.h>
#include <unistd.h>

// ── RAII file-descriptor guard ──────────────────────────────────────────────

class FDGuard {
public:
    explicit FDGuard(int fd) noexcept : fd_(fd) {}
    ~FDGuard() { if (fd_ >= 0) ::close(fd_); }

    FDGuard(FDGuard&& o) noexcept : fd_(o.fd_) { o.fd_ = -1; }
    FDGuard(const FDGuard&) = delete;
    FDGuard& operator=(const FDGuard&) = delete;

    int  get()  const noexcept { return fd_; }
    explicit operator bool() const noexcept { return fd_ >= 0; }
    int release() noexcept { int r = fd_; fd_ = -1; return r; }

private:
    int fd_;
};

// ── Error helpers ───────────────────────────────────────────────────────────

static NSError* posix_error(int err_no, NSString* detail = nil) {
    NSMutableDictionary* info = [NSMutableDictionary new];
    const char* msg = ::strerror(err_no);
    info[NSLocalizedDescriptionKey] = detail
        ? [NSString stringWithFormat:@"%@ — %s", detail, msg]
        : [NSString stringWithUTF8String:msg];
    return [NSError errorWithDomain:NSPOSIXErrorDomain
                               code:err_no
                           userInfo:info];
}

static NSError* cpp_error(const char* what, int code = 1) {
    return [NSError errorWithDomain:@"com.chrono.cpp"
                               code:code
                           userInfo:@{ NSLocalizedDescriptionKey:
                               [NSString stringWithUTF8String:what ?: "Unknown C++ exception"] }];
}

static NSError* toml_error(NSString* desc) {
    return [NSError errorWithDomain:@"com.chrono.toml"
                               code:1
                           userInfo:@{ NSLocalizedDescriptionKey: desc }];
}

// ── Timestamp conversion helpers ─────────────────────────────────────────────

/// NSDate → struct timespec
static struct timespec nsdate_to_timespec(NSDate* date) noexcept {
    double ti = date.timeIntervalSince1970;
    struct timespec ts;
    ts.tv_sec  = static_cast<time_t>(ti);
    ts.tv_nsec = static_cast<long>((ti - static_cast<double>(ts.tv_sec)) * 1e9);
    return ts;
}

/// struct timespec → NSDate
static NSDate* timespec_to_nsdate(const struct timespec& ts) {
    double ti = static_cast<double>(ts.tv_sec)
              + static_cast<double>(ts.tv_nsec) * 1e-9;
    return [NSDate dateWithTimeIntervalSince1970:ti];
}

// ── setattrlist helpers ──────────────────────────────────────────────────────

/// Apply ATTR_CMN_CRTIME (creation date) to an open file descriptor.
static int set_crtime(int fd, const struct timespec& ts) {
    struct attrlist al{};
    al.bitmapcount = ATTR_BIT_MAP_COUNT;
    al.commonattr  = ATTR_CMN_CRTIME;
    struct timespec buf = ts;
    return ::fsetattrlist(fd, &al, &buf, sizeof(buf), 0);
}

/// Apply ATTR_CMN_ADDEDTIME (added-to-directory date) to an open fd.
static int set_addedtime(int fd, const struct timespec& ts) {
    struct attrlist al{};
    al.bitmapcount = ATTR_BIT_MAP_COUNT;
    al.commonattr  = ATTR_CMN_ADDEDTIME;
    struct timespec buf = ts;
    return ::fsetattrlist(fd, &al, &buf, sizeof(buf), 0);
}

/// Store Date Added as a fallback extended attribute (ISO 8601 UTC string).
static void store_added_xattr(int fd, const struct timespec& ts) {
    std::time_t t = static_cast<std::time_t>(ts.tv_sec);
    char buf[64];
    struct tm gmt{};
    ::gmtime_r(&t, &gmt);
    ::strftime(buf, sizeof(buf), "%Y-%m-%dT%H:%M:%SZ", &gmt);
    ::fsetxattr(fd, "com.chrono.dateAdded", buf, ::strlen(buf), 0, 0);
}

// ── File copy helper ─────────────────────────────────────────────────────────

/// Copy srcFd → dstFd via a 64 KiB read/write loop. Returns errno on failure.
static int copy_fd_loop(int src, int dst) {
    static constexpr std::size_t kBufSize = 64u * 1024u;
    char buf[kBufSize];
    ssize_t n;
    while ((n = ::read(src, buf, kBufSize)) > 0) {
        const char* p = buf;
        ssize_t remaining = n;
        while (remaining > 0) {
            ssize_t w = ::write(dst, p, static_cast<size_t>(remaining));
            if (w < 0) return errno;
            p         += w;
            remaining -= w;
        }
    }
    return (n < 0) ? errno : 0;
}

// ══════════════════════════════════════════════════════════════════════════════
// CHTimestampRecord
// ══════════════════════════════════════════════════════════════════════════════

@implementation CHTimestampRecord {
    chrono::FileRecord _record;
}

- (instancetype)initWithRecord:(chrono::FileRecord)record {
    if ((self = [super init])) {
        _record = std::move(record);
    }
    return self;
}

- (NSString *)relativePath {
    return [NSString stringWithUTF8String:_record.relative_path.c_str()];
}
- (uint64_t)sizeBytes   { return _record.size_bytes; }
- (NSString *)sha256Hex {
    return [NSString stringWithUTF8String:_record.sha256_hex.c_str()];
}
- (NSDate *)dateCreated {
    if (!_record.date_created) return nil;
    return timespec_to_nsdate(*_record.date_created);
}
- (NSDate *)dateModified {
    if (!_record.date_modified) return nil;
    return timespec_to_nsdate(*_record.date_modified);
}
- (NSDate *)dateAdded {
    if (!_record.date_added) return nil;
    return timespec_to_nsdate(*_record.date_added);
}
- (mode_t)posixMode { return _record.posix_mode; }
- (NSString *)uti {
    if (!_record.uti) return nil;
    return [NSString stringWithUTF8String:_record.uti->c_str()];
}

/// Expose the underlying C++ record (used internally by CHChronoEngine).
- (const chrono::FileRecord&)cppRecord { return _record; }

@end

// ══════════════════════════════════════════════════════════════════════════════
// CHChronoEngine
// ══════════════════════════════════════════════════════════════════════════════

@implementation CHChronoEngine

// ── enumerateFolder: ─────────────────────────────────────────────────────────

- (nullable NSArray<CHTimestampRecord *> *)
    enumerateFolder:(NSURL *)folderURL
              error:(NSError **)error
{
    @try {
        try {
            const char* path = folderURL.fileSystemRepresentation;
            if (!path) {
                if (error) *error = posix_error(EINVAL, @"Invalid folder URL");
                return nil;
            }

            auto result = chrono::enumerate(std::string(path));
            if (!result) {
                if (error) *error = posix_error(result.error().value());
                return nil;
            }

            NSMutableArray<CHTimestampRecord *> *out =
                [NSMutableArray arrayWithCapacity:result->size()];

            for (auto& rec : *result) {
                // SHA-256 may have been computed during traversal; if not,
                // compute it now.
                if (rec.sha256_hex.empty()) {
                    std::string full = std::string(path) + "/" + rec.relative_path;
                    FDGuard fd(::open(full.c_str(), O_RDONLY | O_CLOEXEC));
                    if (fd) {
                        auto h = chrono::sha256_of_fd(fd.get());
                        if (h) rec.sha256_hex = std::move(*h);
                    }
                }
                [out addObject:[[CHTimestampRecord alloc] initWithRecord:std::move(rec)]];
            }
            return [out copy];

        } catch (const std::exception& e) {
            if (error) *error = cpp_error(e.what());
            return nil;
        } catch (...) {
            if (error) *error = cpp_error("Unknown C++ exception", 2);
            return nil;
        }
    } @catch (NSException *ex) {
        if (error) *error = [NSError errorWithDomain:@"com.chrono.cpp"
                                                code:3
                                            userInfo:@{NSLocalizedDescriptionKey: ex.reason ?: @"NSException"}];
        return nil;
    }
}

// ── duplicateFileAt:to:applyTimestamps:error: ─────────────────────────────────

- (BOOL)duplicateFileAt:(NSURL *)sourceURL
                     to:(NSURL *)destinationURL
       applyTimestamps:(CHTimestampRecord *)record
                  error:(NSError **)error
{
    @try {
        try {
            const char* srcPath  = sourceURL.fileSystemRepresentation;
            const char* dstPath  = destinationURL.fileSystemRepresentation;
            if (!srcPath || !dstPath) {
                if (error) *error = posix_error(EINVAL, @"Invalid URL");
                return NO;
            }

            // ── Copy ──────────────────────────────────────────────────────
            // Attempt APFS CoW clone first
            bool cloned = false;
#if defined(__APPLE__)
            if (::clonefileat(AT_FDCWD, srcPath, AT_FDCWD, dstPath, 0) == 0) {
                cloned = true;
            } else if (errno != ENOTSUP && errno != EXDEV) {
                if (error) *error = posix_error(errno, @"clonefileat");
                return NO;
            }
#endif
            if (!cloned) {
                FDGuard srcFd(::open(srcPath, O_RDONLY | O_CLOEXEC));
                if (!srcFd) {
                    if (error) *error = posix_error(errno, @"open source");
                    return NO;
                }
                FDGuard dstFd(::open(dstPath,
                    O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, 0644));
                if (!dstFd) {
                    if (error) *error = posix_error(errno, @"open destination");
                    return NO;
                }
                int err = copy_fd_loop(srcFd.get(), dstFd.get());
                if (err) {
                    if (error) *error = posix_error(err, @"copy loop");
                    return NO;
                }
            }

            // ── Apply timestamps ──────────────────────────────────────────
            // Open the destination file we just created
            FDGuard dstFd(::open(dstPath, O_RDWR | O_CLOEXEC));
            if (!dstFd) {
                if (error) *error = posix_error(errno, @"open dest for timestamps");
                return NO;
            }

            // 1. Date Created
            if (NSDate* dc = record.dateCreated) {
                struct timespec ts = nsdate_to_timespec(dc);
                if (set_crtime(dstFd.get(), ts) != 0 && error) {
                    *error = posix_error(errno, @"set ATTR_CMN_CRTIME");
                    return NO;
                }
            }

            // 2. Date Modified
            if (NSDate* dm = record.dateModified) {
                struct timespec ts = nsdate_to_timespec(dm);
                struct timespec times[2] = {
                    { 0, UTIME_OMIT },   // access time — leave unchanged
                    ts
                };
                if (::futimens(dstFd.get(), times) != 0 && error) {
                    *error = posix_error(errno, @"futimens");
                    return NO;
                }
            }

            // 3. Date Added — best-effort, failure is non-fatal
            if (NSDate* da = record.dateAdded) {
                struct timespec ts = nsdate_to_timespec(da);
                if (set_addedtime(dstFd.get(), ts) != 0) {
                    NSLog(@"[ChronoShim] ATTR_CMN_ADDEDTIME write failed (errno %d); "
                          @"storing fallback xattr for %@", errno, destinationURL);
                    store_added_xattr(dstFd.get(), ts);
                }
            }

            return YES;

        } catch (const std::exception& e) {
            if (error) *error = cpp_error(e.what());
            return NO;
        } catch (...) {
            if (error) *error = cpp_error("Unknown C++ exception", 2);
            return NO;
        }
    } @catch (NSException *ex) {
        if (error) *error = [NSError errorWithDomain:@"com.chrono.cpp"
                                                code:3
                                            userInfo:@{NSLocalizedDescriptionKey: ex.reason ?: @"NSException"}];
        return NO;
    }
}

// ── emitManifestFromRecords: ─────────────────────────────────────────────────

- (nullable NSString *)
    emitManifestFromRecords:(NSArray<CHTimestampRecord *> *)records
           sourceFolderPath:(NSString *)sourcePath
                 deviceName:(NSString *)deviceName
                      error:(NSError **)error
{
    @try {
        try {
            chrono::Manifest manifest;
            manifest.metadata.source_folder = sourcePath.UTF8String;
            manifest.metadata.source_device = deviceName.UTF8String;

            // export_timestamp = now (UTC)
            struct timespec now{};
            ::clock_gettime(CLOCK_REALTIME, &now);
            manifest.metadata.export_timestamp = now;

            // Detect local IANA timezone
            NSTimeZone* tz = [NSTimeZone localTimeZone];
            manifest.metadata.timezone = tz.name.UTF8String;

            for (CHTimestampRecord* rec in records) {
                manifest.files.push_back(rec.cppRecord);
            }
            manifest.recompute_totals();

            auto result = chrono::emit(manifest);
            if (!result) {
                if (error) *error = toml_error(@"TOML emission failed");
                return nil;
            }
            return [NSString stringWithUTF8String:result->c_str()];

        } catch (const std::exception& e) {
            if (error) *error = cpp_error(e.what());
            return nil;
        } catch (...) {
            if (error) *error = cpp_error("Unknown C++ exception", 2);
            return nil;
        }
    } @catch (NSException *ex) {
        if (error) *error = [NSError errorWithDomain:@"com.chrono.cpp"
                                                code:3
                                            userInfo:@{NSLocalizedDescriptionKey: ex.reason ?: @"NSException"}];
        return nil;
    }
}

// ── applyTimestampsToFileAt: ─────────────────────────────────────────────────

- (BOOL)applyTimestampsToFileAt:(NSURL *)fileURL
                    dateCreated:(nullable NSDate *)created
                   dateModified:(nullable NSDate *)modified
                      dateAdded:(nullable NSDate *)added
                          error:(NSError **)error
{
    @try {
        try {
            const char* path = fileURL.fileSystemRepresentation;
            if (!path) {
                if (error) *error = posix_error(EINVAL, @"Invalid URL");
                return NO;
            }

            FDGuard fd(::open(path, O_RDWR | O_CLOEXEC));
            if (!fd) {
                if (error) *error = posix_error(errno, @"open for timestamp application");
                return NO;
            }

            // 1. Date Created
            if (created) {
                struct timespec ts = nsdate_to_timespec(created);
                if (set_crtime(fd.get(), ts) != 0) {
                    if (error) *error = posix_error(errno, @"ATTR_CMN_CRTIME");
                    return NO;
                }
            }

            // 2. Date Modified
            if (modified) {
                struct timespec ts = nsdate_to_timespec(modified);
                struct timespec times[2] = { { 0, UTIME_OMIT }, ts };
                if (::futimens(fd.get(), times) != 0) {
                    if (error) *error = posix_error(errno, @"futimens");
                    return NO;
                }
            }

            // 3. Date Added — attempt native; fall back to xattr
            if (added) {
                struct timespec ts = nsdate_to_timespec(added);
                if (set_addedtime(fd.get(), ts) != 0) {
                    NSLog(@"[ChronoShim] ATTR_CMN_ADDEDTIME write failed (errno %d); "
                          @"using xattr fallback for %@", errno, fileURL);
                    store_added_xattr(fd.get(), ts);
                }
            }

            return YES;

        } catch (const std::exception& e) {
            if (error) *error = cpp_error(e.what());
            return NO;
        } catch (...) {
            if (error) *error = cpp_error("Unknown C++ exception", 2);
            return NO;
        }
    } @catch (NSException *ex) {
        if (error) *error = [NSError errorWithDomain:@"com.chrono.cpp"
                                                code:3
                                            userInfo:@{NSLocalizedDescriptionKey: ex.reason ?: @"NSException"}];
        return NO;
    }
}

// ── parseManifest: ────────────────────────────────────────────────────────────

- (nullable CHParsedManifest *)
    parseManifest:(NSString *)tomlText
            error:(NSError **)error
{
    @try {
        try {
            auto result = chrono::parse_manifest(tomlText.UTF8String);
            if (!result) {
                if (error) *error = toml_error(@"Manifest parse failed");
                return nil;
            }
            const chrono::Manifest& m = *result;

            // ── Header ────────────────────────────────────────────────────
            CHManifestHeader* header = [CHManifestHeader new];
            [header setSchemaVersion:[NSString stringWithUTF8String:m.metadata.schema_version.c_str()]];
            [header setToolVersion:  [NSString stringWithUTF8String:m.metadata.tool_version.c_str()]];
            [header setSourceDevice: [NSString stringWithUTF8String:m.metadata.source_device.c_str()]];
            [header setSourceFolder: [NSString stringWithUTF8String:m.metadata.source_folder.c_str()]];
            [header setTimezone:     [NSString stringWithUTF8String:m.metadata.timezone.c_str()]];
            [header setFileCount:    static_cast<NSInteger>(m.metadata.file_count)];
            [header setTotalSizeBytes: m.metadata.total_size_bytes];
            if (m.metadata.export_timestamp.tv_sec != 0) {
                [header setExportTimestamp: timespec_to_nsdate(m.metadata.export_timestamp)];
            }

            // ── Entries ───────────────────────────────────────────────────
            NSMutableArray<CHManifestEntry *>* entries =
                [NSMutableArray arrayWithCapacity:m.files.size()];
            for (const auto& rec : m.files) {
                CHManifestEntry* entry = [CHManifestEntry new];
                [entry setRelativePath:[NSString stringWithUTF8String:rec.relative_path.c_str()]];
                [entry setSizeBytes:   rec.size_bytes];
                [entry setSha256:      [NSString stringWithUTF8String:rec.sha256_hex.c_str()]];
                if (rec.date_created)  [entry setDateCreated:  timespec_to_nsdate(*rec.date_created)];
                if (rec.date_modified) [entry setDateModified: timespec_to_nsdate(*rec.date_modified)];
                if (rec.date_added)    [entry setDateAdded:    timespec_to_nsdate(*rec.date_added)];
                [entries addObject:entry];
            }

            CHParsedManifest* parsed = [CHParsedManifest new];
            [parsed setHeader:header];
            [parsed setEntries:[entries copy]];
            return parsed;

        } catch (const std::exception& e) {
            if (error) *error = cpp_error(e.what());
            return nil;
        } catch (...) {
            if (error) *error = cpp_error("Unknown C++ exception", 2);
            return nil;
        }
    } @catch (NSException *ex) {
        if (error) *error = [NSError errorWithDomain:@"com.chrono.cpp"
                                                code:3
                                            userInfo:@{NSLocalizedDescriptionKey: ex.reason ?: @"NSException"}];
        return nil;
    }
}

@end

// ══════════════════════════════════════════════════════════════════════════════
// CHManifestEntry — mutable backing for deserialized [[file]] rows
// ══════════════════════════════════════════════════════════════════════════════

@implementation CHManifestEntry
@synthesize relativePath, sizeBytes, sha256;
@synthesize dateCreated, dateModified, dateAdded;
@end

// ══════════════════════════════════════════════════════════════════════════════
// CHManifestHeader — mutable backing for [metadata]
// ══════════════════════════════════════════════════════════════════════════════

@implementation CHManifestHeader
@synthesize schemaVersion, toolVersion, sourceDevice, sourceFolder;
@synthesize exportTimestamp, timezone, fileCount, totalSizeBytes;
@end

// ══════════════════════════════════════════════════════════════════════════════
// CHParsedManifest
// ══════════════════════════════════════════════════════════════════════════════

@implementation CHParsedManifest
@synthesize header, entries;
@end
