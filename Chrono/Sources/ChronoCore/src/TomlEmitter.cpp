#define TOML_ENABLE_UNRELEASED_FEATURES 1
#include <toml.hpp>

#include <ChronoCore/TomlEmitter.hpp>
#include <ChronoCore/Manifest.hpp>

#include <ctime>
#include <sstream>

namespace chrono {

// ── timespec → toml::date_time (UTC) ───────────────────────────────────────

static toml::date_time timespec_to_toml(const struct timespec& ts) {
    // Convert seconds since epoch to broken-down time (UTC)
    std::time_t t = static_cast<std::time_t>(ts.tv_sec);
    struct tm gmt{};
    ::gmtime_r(&t, &gmt);

    toml::date date{
        static_cast<uint16_t>(gmt.tm_year + 1900),
        static_cast<uint8_t> (gmt.tm_mon  + 1),
        static_cast<uint8_t> (gmt.tm_mday),
    };
    toml::time time_val{
        static_cast<uint8_t>(gmt.tm_hour),
        static_cast<uint8_t>(gmt.tm_min),
        static_cast<uint8_t>(gmt.tm_sec),
        static_cast<uint32_t>(ts.tv_nsec),  // nanoseconds
    };
    // UTC offset = 0 minutes
    return toml::date_time{ date, time_val, toml::time_offset{ 0, 0 } };
}

// ── toml::date_time → timespec ──────────────────────────────────────────────

static struct timespec toml_to_timespec(const toml::date_time& dt) noexcept {
    struct tm gmt{};
    gmt.tm_year = dt.date.year  - 1900;
    gmt.tm_mon  = dt.date.month - 1;
    gmt.tm_mday = dt.date.day;
    gmt.tm_hour = dt.time.hour;
    gmt.tm_min  = dt.time.minute;
    gmt.tm_sec  = dt.time.second;
    gmt.tm_isdst = 0;

    // timegm is available on Darwin / glibc
    struct timespec ts{};
    ts.tv_sec  = ::timegm(&gmt);
    ts.tv_nsec = static_cast<long>(dt.time.nanosecond);

    // Adjust for timezone offset if present
    if (dt.offset.has_value()) {
        int offset_secs = static_cast<int>(dt.offset->minutes) * 60;
        ts.tv_sec -= offset_secs;
    }
    return ts;
}

// ── Emit ────────────────────────────────────────────────────────────────────

std::expected<std::string, std::error_code>
emit(const Manifest& manifest) {
    try {
        toml::table root;

        // ── [metadata] ─────────────────────────────────────────────────────
        toml::table meta;
        meta.emplace("schema_version",   manifest.metadata.schema_version);
        meta.emplace("tool_version",     manifest.metadata.tool_version);
        meta.emplace("source_device",    manifest.metadata.source_device);
        meta.emplace("source_folder",    manifest.metadata.source_folder);
        meta.emplace("export_timestamp", timespec_to_toml(manifest.metadata.export_timestamp));
        meta.emplace("timezone",         manifest.metadata.timezone);
        meta.emplace("file_count",       static_cast<int64_t>(manifest.metadata.file_count));
        meta.emplace("total_size_bytes", static_cast<int64_t>(manifest.metadata.total_size_bytes));
        root.emplace("metadata", std::move(meta));

        // ── [[file]] entries ───────────────────────────────────────────────
        toml::array files_arr;
        for (const auto& rec : manifest.files) {
            toml::table entry;
            entry.emplace("relative_path", rec.relative_path);
            entry.emplace("size_bytes",    static_cast<int64_t>(rec.size_bytes));
            entry.emplace("sha256",        rec.sha256_hex);

            if (rec.date_created)
                entry.emplace("date_created",  timespec_to_toml(*rec.date_created));
            if (rec.date_modified)
                entry.emplace("date_modified", timespec_to_toml(*rec.date_modified));
            if (rec.date_added)
                entry.emplace("date_added",    timespec_to_toml(*rec.date_added));

            entry.emplace("posix_mode", static_cast<int64_t>(rec.posix_mode));
            if (rec.uti)
                entry.emplace("uti", *rec.uti);

            files_arr.push_back(std::move(entry));
        }
        root.emplace("file", std::move(files_arr));

        // ── Serialise ──────────────────────────────────────────────────────
        std::ostringstream oss;
        oss << "# Chrono Sidecar Manifest — TOML v1.1.0\n"
            << "# Machine-generated. Do not edit.\n\n"
            << toml::default_formatter{ root };
        return oss.str();

    } catch (const std::exception& e) {
        return std::unexpected(
            std::error_code(EIO, std::system_category()));  // generic I/O error
    }
}

// ── Parse ───────────────────────────────────────────────────────────────────

std::expected<Manifest, std::error_code>
parse_manifest(const std::string& toml_text) {
    try {
        auto root = toml::parse(toml_text);

        Manifest manifest;
        ManifestMetadata& meta = manifest.metadata;

        const auto& m = root["metadata"];
        meta.schema_version    = m["schema_version"].value<std::string>().value_or("1.0.0");
        meta.tool_version      = m["tool_version"].value<std::string>().value_or("0.1.0");
        meta.source_device     = m["source_device"].value<std::string>().value_or("");
        meta.source_folder     = m["source_folder"].value<std::string>().value_or("");
        meta.timezone          = m["timezone"].value<std::string>().value_or("");
        meta.file_count        = static_cast<std::size_t>(
                                    m["file_count"].value<int64_t>().value_or(0));
        meta.total_size_bytes  = static_cast<uint64_t>(
                                    m["total_size_bytes"].value<int64_t>().value_or(0));

        if (auto dt = m["export_timestamp"].value<toml::date_time>()) {
            meta.export_timestamp = toml_to_timespec(*dt);
        }

        // ── [[file]] array ─────────────────────────────────────────────────
        if (auto* arr = root["file"].as_array()) {
            for (const auto& node : *arr) {
                const auto& entry = *node.as_table();
                FileRecord rec;
                rec.relative_path = entry["relative_path"].value<std::string>().value_or("");
                rec.size_bytes    = static_cast<uint64_t>(
                                        entry["size_bytes"].value<int64_t>().value_or(0));
                rec.sha256_hex    = entry["sha256"].value<std::string>().value_or("");
                rec.posix_mode    = static_cast<mode_t>(
                                        entry["posix_mode"].value<int64_t>().value_or(0));
                if (auto s = entry["uti"].value<std::string>()) rec.uti = *s;

                if (auto dt = entry["date_created"].value<toml::date_time>())
                    rec.date_created  = toml_to_timespec(*dt);
                if (auto dt = entry["date_modified"].value<toml::date_time>())
                    rec.date_modified = toml_to_timespec(*dt);
                if (auto dt = entry["date_added"].value<toml::date_time>())
                    rec.date_added    = toml_to_timespec(*dt);

                manifest.files.push_back(std::move(rec));
            }
        }

        return manifest;

    } catch (const toml::parse_error& e) {
        return std::unexpected(
            std::error_code(EINVAL, std::system_category()));
    } catch (const std::exception&) {
        return std::unexpected(
            std::error_code(EIO, std::system_category()));
    }
}

} // namespace chrono
