// toml.hpp — STUB PLACEHOLDER
//
// The real toml++ single-header (v3.4.0 or later) must be placed here.
// Obtain it from: https://github.com/marzer/tomlplusplus/releases
//   Direct download: https://raw.githubusercontent.com/marzer/tomlplusplus/master/toml.hpp
//
// Build instruction in Package.swift already passes:
//   -DTOML_ENABLE_UNRELEASED_FEATURES=1
// so that TOML v1.1.0 features (multiline inline tables, \e, \xHH, optional
// seconds in datetime) are available when you replace this stub.
//
// --- Minimal interface stub (compile-time reference only) ------------------
//
// This stub exposes only the declarations used by TomlEmitter.cpp so that
// editors and tooling can resolve types without the full 50 000-line header.
// It will NOT link correctly; replace with the real header before building.

#pragma once
#include <cstdint>
#include <optional>
#include <sstream>
#include <stdexcept>
#include <string>
#include <string_view>

namespace toml {

struct time_offset {
    int16_t minutes = 0;
    int16_t hours   = 0;  // convenience — not canonical
};

struct date {
    uint16_t year  = 0;
    uint8_t  month = 0;
    uint8_t  day   = 0;
};

struct time {
    uint8_t  hour       = 0;
    uint8_t  minute     = 0;
    uint8_t  second     = 0;
    uint32_t nanosecond = 0;
};

struct date_time {
    toml::date date;
    toml::time time;
    std::optional<time_offset> offset;

    date_time() = default;
    date_time(toml::date d, toml::time t,
              std::optional<time_offset> off = std::nullopt)
        : date(d), time(t), offset(off) {}
};

class parse_error : public std::runtime_error {
public:
    using std::runtime_error::runtime_error;
};

// Forward declarations sufficient for TomlEmitter.cpp
class node;
class table;
class array;
template<typename T> class node_view;

class table {
public:
    table() = default;
    template<typename K, typename V>
    void emplace(K&&, V&&) {}
    node_view<node> operator[](std::string_view) const noexcept;
    node_view<node> operator[](std::string_view) noexcept;
};

class array {
public:
    array() = default;
    template<typename V> void push_back(V&&) {}
    auto begin() const { return _dummy.begin(); }
    auto end()   const { return _dummy.end(); }
private:
    std::vector<int> _dummy;
};

template<typename T>
class node_view {
public:
    template<typename U> std::optional<U> value() const noexcept { return std::nullopt; }
    table* as_table() const noexcept { return nullptr; }
    array* as_array() const noexcept { return nullptr; }
    node_view<node> operator[](std::string_view) const noexcept { return {}; }
};

inline table parse(std::string_view) {
    throw parse_error("Stub — replace Vendor/tomlplusplus/toml.hpp with the real header");
}
inline table parse(const std::string& s) { return parse(std::string_view(s)); }

struct default_formatter {
    const table& tbl;
    explicit default_formatter(const table& t) : tbl(t) {}
};

inline std::ostream& operator<<(std::ostream& os, const default_formatter&) {
    return os << "# (toml++ stub — replace with real header)\n";
}

} // namespace toml
