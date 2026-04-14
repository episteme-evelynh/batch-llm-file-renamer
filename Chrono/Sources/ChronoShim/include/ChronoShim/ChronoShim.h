#import <Foundation/Foundation.h>

NS_ASSUME_NONNULL_BEGIN

// ── CHManifestEntry ──────────────────────────────────────────────────────────

/// Swift-visible representation of one [[file]] row from a parsed TOML manifest.
@interface CHManifestEntry : NSObject
@property (nonatomic, readonly) NSString *relativePath;
@property (nonatomic, readonly) uint64_t  sizeBytes;
@property (nonatomic, readonly) NSString *sha256;
@property (nonatomic, readonly, nullable) NSDate *dateCreated;
@property (nonatomic, readonly, nullable) NSDate *dateModified;
@property (nonatomic, readonly, nullable) NSDate *dateAdded;
@end

// ── CHManifestHeader ─────────────────────────────────────────────────────────

/// Swift-visible representation of the [metadata] section from a parsed TOML manifest.
@interface CHManifestHeader : NSObject
@property (nonatomic, readonly) NSString *schemaVersion;
@property (nonatomic, readonly) NSString *toolVersion;
@property (nonatomic, readonly) NSString *sourceDevice;
@property (nonatomic, readonly) NSString *sourceFolder;
@property (nonatomic, readonly, nullable) NSDate *exportTimestamp;
@property (nonatomic, readonly) NSString *timezone;
@property (nonatomic, readonly) NSInteger fileCount;
@property (nonatomic, readonly) uint64_t  totalSizeBytes;
@end

// ── CHParsedManifest ─────────────────────────────────────────────────────────

/// Full parsed manifest returned by -[CHChronoEngine parseManifest:error:].
@interface CHParsedManifest : NSObject
@property (nonatomic, readonly) CHManifestHeader                *header;
@property (nonatomic, readonly) NSArray<CHManifestEntry *>      *entries;
@end

// ── CHTimestampRecord ────────────────────────────────────────────────────────

/// Immutable snapshot of one file's metadata and three Darwin timestamps.
/// Returned by -[CHChronoEngine enumerateFolder:error:].
@interface CHTimestampRecord : NSObject

/// Path relative to the enumerated folder root (e.g. "Photos/IMG_0001.HEIC").
@property (nonatomic, readonly) NSString *relativePath;

/// File size in bytes.
@property (nonatomic, readonly) uint64_t sizeBytes;

/// Lowercase hex-encoded SHA-256 digest of the file contents.
@property (nonatomic, readonly) NSString *sha256Hex;

/// Birth time (Date Created). Nil if the filesystem does not expose it.
@property (nonatomic, readonly, nullable) NSDate *dateCreated;

/// Content modification date (Date Modified).
@property (nonatomic, readonly, nullable) NSDate *dateModified;

/// Added-to-directory date (Date Added).  Nil if not available (non-APFS, or
/// getattrlist returned an error).
@property (nonatomic, readonly, nullable) NSDate *dateAdded;

/// POSIX permission bits (st_mode).
@property (nonatomic, readonly) mode_t posixMode;

/// Uniform Type Identifier string (e.g. "public.heic"), if known.
@property (nonatomic, readonly, nullable) NSString *uti;

@end


// ── CHChronoEngine ───────────────────────────────────────────────────────────

/// Thread-safe engine that bridges between Swift/ObjC callers and the C++23
/// ChronoCore library. All heavy work happens inside POSIX calls; Darwin
/// timestamp APIs (getattrlist, fsetattrlist, futimens) are accessed directly.
///
/// Every method follows the Cocoa NSError** out-parameter convention.
/// C++ exceptions are caught at the shim boundary and translated to NSError.
@interface CHChronoEngine : NSObject

// ── Enumeration ─────────────────────────────────────────────────────────────

/// Recursively enumerate `folderURL`, read all three timestamps for each
/// regular file, and compute each file's SHA-256 digest.
///
/// The caller must have already called `[folderURL startAccessingSecurityScopedResource]`
/// before invoking this method, and must call `stopAccessingSecurityScopedResource`
/// when done with all returned URLs.
///
/// @param folderURL  A file:// URL for the folder to enumerate.
/// @param error      On failure, set to an NSError in NSPOSIXErrorDomain or
///                   com.chrono.cpp domain.
/// @return           An array of CHTimestampRecord, or nil on error.
- (nullable NSArray<CHTimestampRecord *> *)
    enumerateFolder:(NSURL *)folderURL
              error:(NSError **)error;

// ── Duplication ─────────────────────────────────────────────────────────────

/// Copy the file at `sourceURL` to `destinationURL`, then re-apply the three
/// Darwin timestamps from `record`.
///
/// Uses clonefile() for APFS copy-on-write when available; falls back to a
/// read/write loop with a 64 KiB buffer.
///
/// Timestamp application order:
///  1. Date Created  — fsetattrlist(ATTR_CMN_CRTIME)
///  2. Date Modified — futimens()
///  3. Date Added    — fsetattrlist(ATTR_CMN_ADDEDTIME)
///     If Date Added write fails, the error is logged but the method still
///     returns YES so the caller can proceed.
///
/// @param sourceURL       File to copy.
/// @param destinationURL  Destination path (must not already exist).
/// @param record          Timestamp record obtained from enumerateFolder:.
/// @param error           On failure, set to an NSError.
/// @return                YES on success, NO on fatal error.
- (BOOL)duplicateFileAt:(NSURL *)sourceURL
                     to:(NSURL *)destinationURL
       applyTimestamps:(CHTimestampRecord *)record
                  error:(NSError **)error;

// ── Manifest emission ────────────────────────────────────────────────────────

/// Serialise an array of CHTimestampRecord values into a TOML v1.1.0 manifest
/// string.
///
/// @param records          Records from enumerateFolder:.
/// @param sourcePath       Original absolute folder path (informational).
/// @param deviceName       Value of UIDevice.current.name (or similar).
/// @param error            On failure, set to an NSError in com.chrono.toml.
/// @return                 TOML text as NSString, or nil on error.
- (nullable NSString *)
    emitManifestFromRecords:(NSArray<CHTimestampRecord *> *)records
           sourceFolderPath:(NSString *)sourcePath
                 deviceName:(NSString *)deviceName
                      error:(NSError **)error;

// ── Manifest parsing (macOS CLI) ─────────────────────────────────────────────

/// Parse a TOML v1.1.0 manifest string (produced by emitManifestFromRecords:)
/// and return a structured CHParsedManifest.
///
/// @param tomlText  The full TOML manifest as NSString.
/// @param error     On failure, set to an NSError in com.chrono.toml domain.
/// @return          Parsed manifest, or nil on error.
- (nullable CHParsedManifest *)
    parseManifest:(NSString *)tomlText
            error:(NSError **)error;

// ── Timestamp application (macOS CLI + local iOS use) ────────────────────────

/// Apply timestamps to an existing file at `fileURL`.  Nil date arguments are
/// silently skipped.
///
/// Date Added write:
///  - Attempted via fsetattrlist(ATTR_CMN_ADDEDTIME).
///  - If that fails, the value is stored in the extended attribute
///    "com.chrono.dateAdded" as an ISO 8601 UTF-8 string (fallback).
///
/// @param fileURL      Target file (must be owned by the calling process).
/// @param created      Date Created to apply, or nil.
/// @param modified     Date Modified to apply, or nil.
/// @param added        Date Added to apply, or nil.
/// @param error        On failure, set to an NSError.
/// @return             YES if all requested timestamps were applied, NO otherwise.
- (BOOL)applyTimestampsToFileAt:(NSURL *)fileURL
                    dateCreated:(nullable NSDate *)created
                   dateModified:(nullable NSDate *)modified
                      dateAdded:(nullable NSDate *)added
                          error:(NSError **)error;

@end

NS_ASSUME_NONNULL_END
