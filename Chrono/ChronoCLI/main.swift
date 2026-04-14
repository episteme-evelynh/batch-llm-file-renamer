// chrono-cli — macOS companion CLI for the Chrono iOS app
//
// Usage:
//   chrono-cli apply  <manifest.toml> <target-folder> [--verify]
//   chrono-cli verify <manifest.toml> <target-folder>
//
// Reads the TOML v1.1.0 manifest produced by the iOS app, matches each
// [[file]] entry against files in <target-folder>, and re-applies all three
// Darwin timestamps (Date Created, Date Modified, Date Added) using
// setattrlist / utimensat through the ChronoShim Objective-C++ bridge.
//
// Exit codes:
//   0  All files processed successfully
//   1  One or more files failed or could not be matched
//   2  Manifest parse error or invalid arguments

import Foundation
import ChronoShim

// ── Argument parsing ─────────────────────────────────────────────────────────

enum CLIError: Error {
    case usage
    case badManifest(String)
    case badTargetFolder(String)
}

struct CLIArgs {
    enum Command { case apply, verify }
    let command:      Command
    let manifestURL:  URL
    let targetFolder: URL
    let verify:       Bool
}

func parseArgs() throws -> CLIArgs {
    var args = CommandLine.arguments.dropFirst()

    var command: CLIArgs.Command = .apply
    var verify = false
    var positional: [String] = []

    while let arg = args.first {
        args = args.dropFirst()
        switch arg {
        case "--verify":
            verify = true
        case "apply":
            command = .apply
        case "verify":
            command = .verify
            verify  = true
        default:
            if arg.hasPrefix("-") {
                fputs("Unknown flag: \(arg)\n", stderr)
                throw CLIError.usage
            }
            positional.append(arg)
        }
    }

    guard positional.count == 2 else { throw CLIError.usage }

    let manifestPath = positional[0]
    let targetPath   = positional[1]
    let fm = FileManager.default

    guard fm.fileExists(atPath: manifestPath) else {
        throw CLIError.badManifest("Manifest not found: \(manifestPath)")
    }
    guard fm.fileExists(atPath: targetPath) else {
        throw CLIError.badTargetFolder("Target folder not found: \(targetPath)")
    }

    return CLIArgs(
        command:      command,
        manifestURL:  URL(fileURLWithPath: manifestPath).standardizedFileURL,
        targetFolder: URL(fileURLWithPath: targetPath ).standardizedFileURL,
        verify:       verify || command == .verify)
}

// ── Output helpers ───────────────────────────────────────────────────────────

private func printOK(_ msg: String)   { print("  \u{2713} \(msg)") }    // ✓
private func printWarn(_ msg: String) { print("  \u{26A0} \(msg)") }    // ⚠
private func printFail(_ msg: String) { fputs("  \u{2717} \(msg)\n", stderr) }  // ✗

// ── SHA-256 verification ─────────────────────────────────────────────────────

#if canImport(CommonCrypto)
import CommonCrypto

func sha256Hex(of url: URL) -> String? {
    guard let data = try? Data(contentsOf: url) else { return nil }
    var digest = [UInt8](repeating: 0, count: Int(CC_SHA256_DIGEST_LENGTH))
    data.withUnsafeBytes { ptr in
        _ = CC_SHA256(ptr.baseAddress, CC_LONG(data.count), &digest)
    }
    return digest.map { String(format: "%02x", $0) }.joined()
}
#else
func sha256Hex(of url: URL) -> String? { return nil }
#endif

// ── Timestamp application ────────────────────────────────────────────────────

struct ApplyResult {
    var applied  = 0
    var partial  = 0
    var failed   = 0
    var skipped  = 0
}

@discardableResult
func applyTimestamps(
    entries:      [CHManifestEntry],
    targetFolder: URL,
    verify:       Bool
) -> ApplyResult {
    let engine = CHChronoEngine()
    var result = ApplyResult()

    for entry in entries {
        let fileURL = targetFolder.appendingPathComponent(entry.relativePath)

        guard FileManager.default.fileExists(atPath: fileURL.path) else {
            printWarn("Missing: \(entry.relativePath)")
            result.skipped += 1
            continue
        }

        // Optional SHA-256 integrity check
        if !entry.sha256.isEmpty {
            if let actual = sha256Hex(of: fileURL), actual != entry.sha256 {
                printWarn("SHA-256 mismatch for \(entry.relativePath) — applying timestamps anyway")
            }
        }

        var applyError: NSError?
        let ok = engine.applyTimestamps(
            toFileAt:     fileURL,
            dateCreated:  entry.dateCreated,
            dateModified: entry.dateModified,
            dateAdded:    entry.dateAdded,
            error:        &applyError)

        if !ok {
            printFail("\(entry.relativePath): \(applyError?.localizedDescription ?? "unknown error")")
            result.failed += 1
            continue
        }

        if verify {
            let verified = verifyTimestamps(fileURL: fileURL, entry: entry)
            if verified {
                printOK(entry.relativePath)
                result.applied += 1
            } else {
                printWarn("\(entry.relativePath) (partial — verify mismatch)")
                result.partial += 1
            }
        } else {
            printOK(entry.relativePath)
            result.applied += 1
        }
    }

    return result
}

// ── Timestamp verification ────────────────────────────────────────────────────

func verifyTimestamps(fileURL: URL, entry: CHManifestEntry) -> Bool {
    guard let values = try? fileURL.resourceValues(
        forKeys: [.creationDateKey, .contentModificationDateKey, .addedToDirectoryDateKey])
    else { return false }

    let tolerance: TimeInterval = 1.0
    var ok = true

    if let expected = entry.dateCreated,  let actual = values.creationDate {
        if abs(expected.timeIntervalSince(actual)) > tolerance { ok = false }
    }
    if let expected = entry.dateModified, let actual = values.contentModificationDate {
        if abs(expected.timeIntervalSince(actual)) > tolerance { ok = false }
    }
    if let expected = entry.dateAdded,    let actual = values.addedToDirectoryDate {
        if abs(expected.timeIntervalSince(actual)) > tolerance { ok = false }
    }
    return ok
}

// ── Entry point ──────────────────────────────────────────────────────────────

func run() -> Int32 {
    let args: CLIArgs
    do {
        args = try parseArgs()
    } catch CLIError.usage {
        let exe = (CommandLine.arguments.first as NSString?)?.lastPathComponent ?? "chrono-cli"
        fputs("""
              Usage:
                \(exe) apply  <manifest.toml> <target-folder> [--verify]
                \(exe) verify <manifest.toml> <target-folder>

              """, stderr)
        return 2
    } catch CLIError.badManifest(let msg) {
        fputs("Error: \(msg)\n", stderr)
        return 2
    } catch CLIError.badTargetFolder(let msg) {
        fputs("Error: \(msg)\n", stderr)
        return 1
    } catch {
        fputs("Unexpected error: \(error)\n", stderr)
        return 1
    }

    // ── Parse manifest ─────────────────────────────────────────────────────
    let tomlText: String
    do {
        tomlText = try String(contentsOf: args.manifestURL, encoding: .utf8)
    } catch {
        fputs("Error reading manifest: \(error.localizedDescription)\n", stderr)
        return 2
    }

    let engine = CHChronoEngine()
    var parseError: NSError?
    guard let manifest = engine.parseManifest(tomlText, error: &parseError) else {
        fputs("Error: TOML parse failed — \(parseError?.localizedDescription ?? "unknown")\n", stderr)
        return 2
    }

    // ── Print header info ──────────────────────────────────────────────────
    let h = manifest.header
    print("Chrono manifest v\(h.schemaVersion) (tool v\(h.toolVersion))")
    print("Source device:  \(h.sourceDevice)")
    print("Source folder:  \(h.sourceFolder)")
    if let ts = h.exportTimestamp {
        let fmt = ISO8601DateFormatter()
        print("Exported:       \(fmt.string(from: ts))")
    }
    print("Files:          \(h.fileCount) (\(h.totalSizeBytes) bytes total)")
    print("Target folder:  \(args.targetFolder.path)")
    print(String(repeating: "-", count: 60))

    // ── Apply timestamps ───────────────────────────────────────────────────
    let result = applyTimestamps(
        entries:      manifest.entries,
        targetFolder: args.targetFolder,
        verify:       args.verify)

    // ── Summary ────────────────────────────────────────────────────────────
    print(String(repeating: "-", count: 60))
    print("Results:  \(result.applied) restored  \(result.partial) partial  \(result.failed) failed  \(result.skipped) missing")

    if result.failed > 0 || result.skipped > 0 { return 1 }
    return 0
}

exit(run())
