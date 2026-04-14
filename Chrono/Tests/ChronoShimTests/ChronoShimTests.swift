// ChronoShimTests.swift — Integration tests for the Objective-C++ ChronoShim layer
//
// These tests create real files on disk with known timestamps (via setattrlist),
// then verify that CHChronoEngine reads them correctly, duplicates them, and
// re-applies the original timestamps.

import XCTest
import Foundation
import ChronoShim

final class ChronoShimTests: XCTestCase {

    // ── Temp directory lifecycle ───────────────────────────────────────────

    var tmpDir: URL!

    override func setUp() {
        super.setUp()
        let base = FileManager.default.temporaryDirectory
        tmpDir = base.appendingPathComponent("ChronoShimTests-\(UUID().uuidString)")
        try! FileManager.default.createDirectory(at: tmpDir, withIntermediateDirectories: true)
    }

    override func tearDown() {
        try? FileManager.default.removeItem(at: tmpDir)
        super.tearDown()
    }

    // ── Helpers ────────────────────────────────────────────────────────────

    /// Write `content` to a new file inside `tmpDir` and return its URL.
    func makeFile(name: String, content: String = "chrono test") -> URL {
        let url = tmpDir.appendingPathComponent(name)
        try! content.write(to: url, atomically: true, encoding: .utf8)
        return url
    }

    /// Set creation date on a file via setattrlist.
    func setCreationDate(_ date: Date, on url: URL) throws {
        let path = url.path
        var ts   = timespec_from_date(date)
        var al   = attrlist()
        al.bitmapcount = UInt16(ATTR_BIT_MAP_COUNT)
        al.commonattr  = attrgroup_t(ATTR_CMN_CRTIME)
        let rc = setattrlist(path, &al, &ts, MemoryLayout<timespec>.size, 0)
        guard rc == 0 else {
            throw NSError(domain: NSPOSIXErrorDomain, code: Int(errno))
        }
    }

    /// Return a timespec from an NSDate.
    func timespec_from_date(_ d: Date) -> timespec {
        let ti = d.timeIntervalSince1970
        return timespec(tv_sec: Int(ti), tv_nsec: Int((ti - Double(Int(ti))) * 1e9))
    }

    // ── Tests ──────────────────────────────────────────────────────────────

    func testEnumerateFolderReturnsRecords() throws {
        _ = makeFile(name: "alpha.txt", content: "hello")
        _ = makeFile(name: "beta.txt",  content: "world")

        let engine = CHChronoEngine()
        var error: NSError?
        let records = engine.enumerateFolder(tmpDir, error: &error)

        XCTAssertNil(error)
        XCTAssertNotNil(records)
        XCTAssertEqual(records?.count, 2)

        for rec in records ?? [] {
            XCTAssertFalse(rec.relativePath.isEmpty)
            XCTAssertGreaterThan(rec.sizeBytes, 0)
            XCTAssertEqual(rec.sha256Hex.count, 64)
            XCTAssertNotNil(rec.dateCreated)
            XCTAssertNotNil(rec.dateModified)
        }
    }

    func testEnumerateSkipsDotFiles() throws {
        _ = makeFile(name: "visible.txt")
        _ = makeFile(name: ".hidden")

        let engine = CHChronoEngine()
        var error: NSError?
        let records = engine.enumerateFolder(tmpDir, error: &error)

        XCTAssertNil(error)
        let names = records?.map(\.relativePath) ?? []
        XCTAssertTrue(names.contains("visible.txt"))
        XCTAssertFalse(names.contains(".hidden"))
    }

    func testDuplicatePreservesTimestamps() throws {
        let srcURL = makeFile(name: "source.txt", content: "original content")
        let dstURL = tmpDir.appendingPathComponent("dest.txt")

        // Set a known creation date (2024-01-15 12:00:00 UTC)
        let knownDate = Date(timeIntervalSinceReferenceDate: 727012800)  // 2024-01-15T12:00:00Z
        try setCreationDate(knownDate, on: srcURL)

        let engine = CHChronoEngine()
        var enumError: NSError?
        guard let records = engine.enumerateFolder(tmpDir, error: &enumError),
              let record  = records.first(where: { $0.relativePath == "source.txt" }) else {
            XCTFail("Enumeration failed: \(enumError?.localizedDescription ?? "")")
            return
        }

        var dupError: NSError?
        let ok = engine.duplicateFile(at: srcURL, to: dstURL, applyTimestamps: record,
                                      error: &dupError)
        XCTAssertTrue(ok, dupError?.localizedDescription ?? "")
        XCTAssertTrue(FileManager.default.fileExists(atPath: dstURL.path))

        // Verify creation date on duplicate
        let dstValues = try dstURL.resourceValues(forKeys: [.creationDateKey,
                                                             .contentModificationDateKey])
        if let dstCreation = dstValues.creationDate, let srcCreation = record.dateCreated {
            XCTAssertLessThanOrEqual(abs(dstCreation.timeIntervalSince(srcCreation)), 1.0,
                "Creation date mismatch: got \(dstCreation), expected \(srcCreation)")
        }
        if let dstMod = dstValues.contentModificationDate, let srcMod = record.dateModified {
            XCTAssertLessThanOrEqual(abs(dstMod.timeIntervalSince(srcMod)), 1.0,
                "Modification date mismatch")
        }
    }

    func testManifestEmissionAndRoundTrip() throws {
        _ = makeFile(name: "doc.txt", content: "manifest round-trip test")

        let engine = CHChronoEngine()
        var enumError: NSError?
        guard let records = engine.enumerateFolder(tmpDir, error: &enumError) else {
            XCTFail("Enumeration failed")
            return
        }

        var emitError: NSError?
        let toml = engine.emitManifest(fromRecords: records,
                                        sourceFolderPath: tmpDir.path,
                                        deviceName: "TestDevice",
                                        error: &emitError)
        XCTAssertNil(emitError)
        XCTAssertNotNil(toml)

        guard let tomlText = toml else { return }

        // Basic schema compliance checks
        XCTAssertTrue(tomlText.contains("schema_version"))
        XCTAssertTrue(tomlText.contains("tool_version"))
        XCTAssertTrue(tomlText.contains("relative_path"))
        XCTAssertTrue(tomlText.contains("sha256"))
        XCTAssertTrue(tomlText.contains("date_created"))
        XCTAssertTrue(tomlText.contains("date_modified"))

        // Parse round-trip
        var parseError: NSError?
        let parsed = engine.parseManifest(tomlText, error: &parseError)
        XCTAssertNil(parseError)
        XCTAssertNotNil(parsed)
        XCTAssertEqual(parsed?.entries.count, records.count)
        XCTAssertEqual(parsed?.header.sourceDevice, "TestDevice")
    }

    func testApplyTimestampsToFileAt() throws {
        let fileURL   = makeFile(name: "apply_test.txt")
        let engine    = CHChronoEngine()

        let created   = Date(timeIntervalSince1970: 1_700_000_100)
        let modified  = Date(timeIntervalSince1970: 1_700_000_200)
        let added     = Date(timeIntervalSince1970: 1_700_000_300)

        var applyError: NSError?
        let ok = engine.applyTimestamps(toFileAt: fileURL,
                                         dateCreated:  created,
                                         dateModified: modified,
                                         dateAdded:    added,
                                         error: &applyError)
        // Date Added write may or may not succeed depending on FS; only
        // fail on Date Created / Date Modified errors.
        if !ok {
            // If the only failure is Date Added, consider it a partial success.
            // Any other failure is a test failure.
            if let err = applyError, err.domain != NSPOSIXErrorDomain || err.code != EPERM {
                XCTFail("applyTimestamps failed: \(err.localizedDescription)")
            }
        }

        let values = try fileURL.resourceValues(
            forKeys: [.creationDateKey, .contentModificationDateKey])

        if let actual = values.creationDate {
            XCTAssertLessThanOrEqual(abs(actual.timeIntervalSince(created)), 1.0)
        }
        if let actual = values.contentModificationDate {
            XCTAssertLessThanOrEqual(abs(actual.timeIntervalSince(modified)), 1.0)
        }
    }

    func testEnumerateNonExistentFolderReturnsError() {
        let nonExistent = tmpDir.appendingPathComponent("does_not_exist")
        let engine = CHChronoEngine()
        var error: NSError?
        let result = engine.enumerateFolder(nonExistent, error: &error)
        XCTAssertNil(result)
        XCTAssertNotNil(error)
        XCTAssertEqual(error?.domain, NSPOSIXErrorDomain)
    }
}
