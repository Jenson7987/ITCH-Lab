#include "itchlab/validation/validator.hpp"

#include "itchlab/book/order_book.hpp"
#include "itchlab/config/canonical_json.hpp"
#include "itchlab/config/replay_config.hpp"
#include "itchlab/core/sha256.hpp"
#include "itchlab/output/event_writer.hpp"
#include "itchlab/output/manifest.hpp"
#include "itchlab/output/snapshot_writer.hpp"

#include <nlohmann/json.hpp>

#include <algorithm>
#include <array>
#include <cctype>
#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <limits>
#include <map>
#include <memory>
#include <optional>
#include <set>
#include <span>
#include <string>
#include <string_view>
#include <unordered_set>
#include <utility>
#include <vector>

namespace itchlab {
namespace {

using Json = nlohmann::json;

constexpr std::uintmax_t kMaximumManifestBytes = 1U << 20U;
constexpr std::string_view kManifestFilename{"replay-manifest.json"};
constexpr std::string_view kEventFilename{"events.ilb"};
constexpr std::string_view kSnapshotFilename{"snapshots.ilb"};
constexpr std::array<std::byte, 8> kEventMagic{
    std::byte{'I'}, std::byte{'T'}, std::byte{'C'}, std::byte{'H'},
    std::byte{'L'}, std::byte{'E'}, std::byte{'1'}, std::byte{0},
};
constexpr std::array<std::byte, 8> kSnapshotMagic{
    std::byte{'I'}, std::byte{'T'}, std::byte{'C'}, std::byte{'H'},
    std::byte{'L'}, std::byte{'S'}, std::byte{'1'}, std::byte{0},
};

enum class InterchangeKind : std::uint8_t {
  events,
  snapshots,
};

struct DictionaryEntry {
  SymbolId symbol_id{};
  StockLocate stock_locate{};
  std::string symbol;
  std::uint32_t round_lot_size{};

  friend bool operator==(const DictionaryEntry&, const DictionaryEntry&) = default;
};

struct BinaryMetadata {
  InterchangeKind kind{InterchangeKind::events};
  std::uint16_t schema_version{};
  std::uint16_t record_size{};
  std::uint16_t depth{};
  TradingDate trading_date{};
  bool degraded{};
  std::uint64_t record_count{};
  ContentHash config_sha256{};
  ContentHash source_sha256{};
  std::vector<DictionaryEntry> dictionary;
  HashedFile file;
};

struct ManifestArtefact {
  InterchangeKind kind{InterchangeKind::events};
  std::string path;
  std::uint16_t schema_version{};
  std::uint16_t record_size{};
  std::uint16_t depth{};
  std::uint64_t record_count{};
  HashedFile file;
};

struct ManifestInstrument {
  DictionaryEntry dictionary;
  std::uint64_t final_order_count{};
  ContentHash final_book_digest{};
  std::string final_trading_state;
};

struct ManifestData {
  std::string replay_id;
  std::string status;
  bool publishable{};
  ReplayConfig config;
  ConfigHashes config_hashes{};
  ContentHash identity_sha256{};
  ContentHash executable_sha256{};
  HashedFile source;
  std::string source_name;
  std::string source_trading_date;
  std::vector<ManifestInstrument> instruments;
  ManifestArtefact events;
  ManifestArtefact snapshots;
  std::uint64_t selected_events{};
  std::uint64_t snapshots_written{};
};

struct LocalError {
  ErrorCode code{ErrorCode::invariant};
  std::string message;
  std::optional<std::uint64_t> record_index;
  std::optional<std::string> expected;
  std::optional<std::string> actual;

  LocalError() = default;
  LocalError(ErrorCode error_code, std::string error_message,
             std::optional<std::uint64_t> error_record_index = std::nullopt,
             std::optional<std::string> expected_value = std::nullopt,
             std::optional<std::string> actual_value = std::nullopt)
      : code{error_code}, message{std::move(error_message)}, record_index{error_record_index},
        expected{std::move(expected_value)}, actual{std::move(actual_value)} {}
};

struct BinaryResult {
  std::optional<BinaryMetadata> metadata;
  std::optional<LocalError> error;

  [[nodiscard]] bool valid() const noexcept { return metadata.has_value() && !error.has_value(); }
};

struct DeepResult {
  std::uint64_t records_examined{};
  std::optional<LocalError> error;

  [[nodiscard]] bool valid() const noexcept { return !error.has_value(); }
};

[[nodiscard]] std::string kind_name(const InterchangeKind kind) {
  return kind == InterchangeKind::events ? "events" : "snapshots";
}

[[nodiscard]] bool all_zero(const std::span<const std::byte> bytes) noexcept {
  return std::ranges::all_of(bytes, [](const std::byte value) { return value == std::byte{0}; });
}

[[nodiscard]] bool all_zero(const ContentHash& hash) noexcept { return all_zero(std::span{hash}); }

[[nodiscard]] bool bytes_equal(const std::span<const std::byte> lhs,
                               const std::span<const std::byte> rhs) noexcept {
  return lhs.size() == rhs.size() && std::ranges::equal(lhs, rhs);
}

[[nodiscard]] bool bytes_are_zero(const std::span<const std::byte> bytes, const std::size_t offset,
                                  const std::size_t count) noexcept {
  if (offset > bytes.size() || count > bytes.size() - offset) {
    return false;
  }
  return all_zero(bytes.subspan(offset, count));
}

[[nodiscard]] std::optional<std::uint16_t> little_u16(const std::span<const std::byte> bytes,
                                                      const std::size_t offset) noexcept {
  if (offset > bytes.size() || 2 > bytes.size() - offset) {
    return std::nullopt;
  }
  return static_cast<std::uint16_t>(std::to_integer<std::uint8_t>(bytes[offset])) |
         static_cast<std::uint16_t>(
             static_cast<std::uint16_t>(std::to_integer<std::uint8_t>(bytes[offset + 1])) << 8U);
}

[[nodiscard]] std::optional<std::uint32_t> little_u32(const std::span<const std::byte> bytes,
                                                      const std::size_t offset) noexcept {
  if (offset > bytes.size() || 4 > bytes.size() - offset) {
    return std::nullopt;
  }
  std::uint32_t value{};
  for (std::size_t index = 0; index < 4; ++index) {
    value |= static_cast<std::uint32_t>(std::to_integer<std::uint8_t>(bytes[offset + index]))
             << static_cast<unsigned>(index * 8U);
  }
  return value;
}

[[nodiscard]] std::optional<std::uint64_t> little_u64(const std::span<const std::byte> bytes,
                                                      const std::size_t offset) noexcept {
  if (offset > bytes.size() || 8 > bytes.size() - offset) {
    return std::nullopt;
  }
  std::uint64_t value{};
  for (std::size_t index = 0; index < 8; ++index) {
    value |= static_cast<std::uint64_t>(std::to_integer<std::uint8_t>(bytes[offset + index]))
             << static_cast<unsigned>(index * 8U);
  }
  return value;
}

[[nodiscard]] bool valid_trading_date(const TradingDate value) noexcept {
  const auto year = value / 10'000U;
  const auto month = value / 100U % 100U;
  const auto day = value % 100U;
  if (year == 0 || month == 0 || month > 12 || day == 0) {
    return false;
  }
  constexpr std::array<std::uint32_t, 12> days_per_month{31, 28, 31, 30, 31, 30,
                                                         31, 31, 30, 31, 30, 31};
  auto maximum_day = days_per_month[month - 1];
  if (month == 2 && ((year % 4U == 0 && year % 100U != 0) || year % 400U == 0)) {
    maximum_day = 29;
  }
  return day <= maximum_day;
}

[[nodiscard]] std::optional<TradingDate> compact_date(const std::string_view value) noexcept {
  if (value.size() != 10 || value[4] != '-' || value[7] != '-') {
    return std::nullopt;
  }
  TradingDate result{};
  for (std::size_t index = 0; index < value.size(); ++index) {
    if (index == 4 || index == 7) {
      continue;
    }
    if (value[index] < '0' || value[index] > '9') {
      return std::nullopt;
    }
    result = static_cast<TradingDate>(result * 10U + static_cast<unsigned>(value[index] - '0'));
  }
  return valid_trading_date(result) ? std::optional<TradingDate>{result} : std::nullopt;
}

[[nodiscard]] std::optional<std::uint32_t> decimal_component(const std::string_view value,
                                                             const std::size_t offset,
                                                             const std::size_t length) noexcept {
  if (offset > value.size() || length > value.size() - offset) {
    return std::nullopt;
  }
  std::uint32_t result{};
  for (std::size_t index = 0; index < length; ++index) {
    const auto character = value[offset + index];
    if (character < '0' || character > '9') {
      return std::nullopt;
    }
    result = result * 10U + static_cast<unsigned>(character - '0');
  }
  return result;
}

[[nodiscard]] bool valid_rfc3339_datetime(const std::string_view value) noexcept {
  if (value.size() < 20 || !compact_date(value.substr(0, 10)) ||
      (value[10] != 'T' && value[10] != 't') || value[13] != ':' || value[16] != ':') {
    return false;
  }
  const auto hour = decimal_component(value, 11, 2);
  const auto minute = decimal_component(value, 14, 2);
  const auto second = decimal_component(value, 17, 2);
  if (!hour || !minute || !second || *hour > 23 || *minute > 59 || *second > 60) {
    return false;
  }

  std::size_t offset = 19;
  if (offset < value.size() && value[offset] == '.') {
    ++offset;
    const auto fraction_start = offset;
    while (offset < value.size() && value[offset] >= '0' && value[offset] <= '9') {
      ++offset;
    }
    if (offset == fraction_start) {
      return false;
    }
  }
  if (offset == value.size() - 1 && (value[offset] == 'Z' || value[offset] == 'z')) {
    return true;
  }
  if (offset + 6 != value.size() || (value[offset] != '+' && value[offset] != '-') ||
      value[offset + 3] != ':') {
    return false;
  }
  const auto offset_hour = decimal_component(value, offset + 1, 2);
  const auto offset_minute = decimal_component(value, offset + 4, 2);
  return offset_hour && offset_minute && *offset_hour <= 23 && *offset_minute <= 59;
}

[[nodiscard]] bool valid_printable_ascii(const std::string_view value) noexcept {
  return std::ranges::all_of(value, [](const char character) {
    const auto byte = static_cast<unsigned char>(character);
    return byte >= 0x20U && byte <= 0x7eU;
  });
}

[[nodiscard]] bool valid_symbol(const std::string_view value) noexcept {
  return !value.empty() && value.size() <= 8 && value.front() != ' ' && value.back() != ' ' &&
         valid_printable_ascii(value);
}

[[nodiscard]] bool valid_sha256_text(const std::string_view value) noexcept {
  return value.size() == 64 && std::ranges::all_of(value, [](const char character) {
           return (character >= '0' && character <= '9') || (character >= 'a' && character <= 'f');
         });
}

[[nodiscard]] bool safe_basename(const std::string_view value) {
  if (value.empty() || value == "." || value == ".." || value.find('/') != std::string_view::npos ||
      value.find('\\') != std::string_view::npos) {
    return false;
  }
  const std::filesystem::path path{value};
  return !path.has_root_path() && path.filename() == path;
}

[[nodiscard]] bool valid_run_id(const std::string_view value) noexcept {
  if (value.size() != 39 || value[8] != 'T' || value[15] != '.' || value[25] != 'Z' ||
      value[26] != '-') {
    return false;
  }
  for (std::size_t index = 0; index < 26; ++index) {
    if (index == 8 || index == 15 || index == 25) {
      continue;
    }
    if (value[index] < '0' || value[index] > '9') {
      return false;
    }
  }
  return std::ranges::all_of(value.substr(27), [](const char character) {
    return (character >= '0' && character <= '9') || (character >= 'a' && character <= 'f');
  });
}

void append_check(ArtefactValidationReport& report, std::string name,
                  const ValidationCheckStatus status, std::string message,
                  std::optional<std::string> expected = std::nullopt,
                  std::optional<std::string> actual = std::nullopt,
                  const std::uint64_t records_examined = 0) {
  report.checks.push_back(ValidationCheck{std::move(name), status, std::move(message),
                                          std::move(expected), std::move(actual),
                                          records_examined});
}

[[nodiscard]] ArtefactValidationResult
fail(ArtefactValidationReport report, const ErrorCode code, std::string check, std::string message,
     std::optional<std::uint64_t> record_index = std::nullopt,
     std::optional<std::string> expected = std::nullopt,
     std::optional<std::string> actual = std::nullopt) {
  append_check(report, check, ValidationCheckStatus::failed, message, expected, actual,
               record_index.value_or(0));
  return ArtefactValidationResult{std::move(report),
                                  ArtefactValidationError{code, std::move(message),
                                                          std::move(check), record_index,
                                                          std::move(expected), std::move(actual)}};
}

[[nodiscard]] ArtefactValidationResult fail(ArtefactValidationReport report, std::string check,
                                            const LocalError& error) {
  return fail(std::move(report), error.code, std::move(check), error.message, error.record_index,
              error.expected, error.actual);
}

void append_deep_not_run(ArtefactValidationReport& report, const bool run_target) {
  if (!report.deep) {
    return;
  }
  append_check(report, "events_records", ValidationCheckStatus::not_run,
               "Deep event validation was not run because a prerequisite failed.");
  if (run_target) {
    append_check(report, "snapshots_records", ValidationCheckStatus::not_run,
                 "Deep snapshot validation was not run because a prerequisite failed.");
    append_check(report, "final_book_digests", ValidationCheckStatus::not_run,
                 "Final book reconstruction was not run because a prerequisite failed.");
  }
}

[[nodiscard]] std::optional<std::string>
read_bounded_regular_file(const std::filesystem::path& path, const std::uintmax_t maximum_size) {
  std::error_code error;
  const auto status = std::filesystem::symlink_status(path, error);
  if (error || !std::filesystem::is_regular_file(status)) {
    return std::nullopt;
  }
  const auto size = std::filesystem::file_size(path, error);
  if (error || size > maximum_size) {
    return std::nullopt;
  }
  std::ifstream stream{path, std::ios::binary};
  if (!stream.is_open()) {
    return std::nullopt;
  }
  return std::string{std::istreambuf_iterator<char>{stream}, std::istreambuf_iterator<char>{}};
}

[[nodiscard]] std::optional<Json> parse_strict_json(const std::string_view document,
                                                    LocalError& error) {
  bool duplicate_key{};
  std::vector<std::unordered_set<std::string>> keys_by_depth;
  const auto callback = [&duplicate_key, &keys_by_depth](
                            const int depth, const Json::parse_event_t event, Json& parsed) {
    const auto index = static_cast<std::size_t>(std::max(depth, 0));
    if (event == Json::parse_event_t::object_start) {
      const auto key_index = index + 1;
      if (keys_by_depth.size() <= key_index) {
        keys_by_depth.resize(key_index + 1);
      }
      keys_by_depth[key_index].clear();
    } else if (event == Json::parse_event_t::key) {
      if (keys_by_depth.size() <= index) {
        keys_by_depth.resize(index + 1);
      }
      if (!keys_by_depth[index].insert(parsed.get<std::string>()).second) {
        duplicate_key = true;
      }
    } else if (event == Json::parse_event_t::object_end && keys_by_depth.size() > index + 1) {
      keys_by_depth[index + 1].clear();
    }
    return true;
  };

  try {
    auto parsed = Json::parse(document.begin(), document.end(), callback, true, false);
    if (duplicate_key) {
      error = LocalError{ErrorCode::invariant,
                         "Replay manifest contains a duplicate object property name."};
      return std::nullopt;
    }
    return parsed;
  } catch (const std::exception&) {
    error = LocalError{ErrorCode::invariant, "Replay manifest is not valid JSON/I-JSON."};
    return std::nullopt;
  }
}

[[nodiscard]] bool exact_object(const Json& value,
                                const std::initializer_list<std::string_view> keys,
                                std::string_view context, LocalError& error) {
  if (!value.is_object()) {
    error = LocalError{ErrorCode::invariant, std::string{context} + " must be an object."};
    return false;
  }
  std::set<std::string_view> expected{keys};
  for (auto iterator = value.begin(); iterator != value.end(); ++iterator) {
    if (!expected.contains(iterator.key())) {
      error = LocalError{ErrorCode::invariant,
                         std::string{context} + " contains an unknown property: " + iterator.key() +
                             '.'};
      return false;
    }
  }
  for (const auto key : expected) {
    if (!value.contains(key)) {
      error =
          LocalError{ErrorCode::invariant, std::string{context} + " is missing required property " +
                                               std::string{key} + '.'};
      return false;
    }
  }
  return true;
}

[[nodiscard]] std::optional<std::uint64_t> json_count(const Json& value) {
  if (value.is_number_unsigned()) {
    const auto number = value.get<std::uint64_t>();
    return number <= kMaxJsonInteger ? std::optional<std::uint64_t>{number} : std::nullopt;
  }
  if (value.is_number_integer()) {
    const auto number = value.get<std::int64_t>();
    if (number >= 0 && static_cast<std::uint64_t>(number) <= kMaxJsonInteger) {
      return static_cast<std::uint64_t>(number);
    }
  }
  return std::nullopt;
}

[[nodiscard]] bool validate_count_map(const Json& value, const bool error_codes,
                                      std::string_view context, LocalError& error) {
  if (!value.is_object()) {
    error = LocalError{ErrorCode::invariant, std::string{context} + " must be an object."};
    return false;
  }
  for (auto iterator = value.begin(); iterator != value.end(); ++iterator) {
    const auto valid_key =
        error_codes ? iterator.key().starts_with("ERR_") && iterator.key().size() > 4 &&
                          std::ranges::all_of(iterator.key().substr(4),
                                              [](const char c) {
                                                return (c >= 'A' && c <= 'Z') ||
                                                       (c >= '0' && c <= '9') || c == '_';
                                              })
                    : iterator.key().size() == 1 && iterator.key().front() >= 'A' &&
                          iterator.key().front() <= 'Z';
    if (!valid_key || !json_count(iterator.value())) {
      error = LocalError{ErrorCode::invariant,
                         std::string{context} + " contains an invalid key or count."};
      return false;
    }
  }
  return true;
}

[[nodiscard]] std::optional<ContentHash> json_hash(const Json& value) {
  if (!value.is_string()) {
    return std::nullopt;
  }
  const auto text = value.get<std::string>();
  return valid_sha256_text(text) ? content_hash_from_hex(text) : std::nullopt;
}

[[nodiscard]] std::optional<std::string> json_nonempty_string(const Json& value) {
  if (!value.is_string()) {
    return std::nullopt;
  }
  auto text = value.get<std::string>();
  return text.empty() ? std::nullopt : std::optional<std::string>{std::move(text)};
}

[[nodiscard]] bool valid_code_revision(const std::string_view value) noexcept {
  auto base = value;
  if (base.ends_with("+dirty")) {
    base.remove_suffix(6);
  }
  if (base == "unknown") {
    return true;
  }
  return base.size() == 40 && std::ranges::all_of(base, [](const char character) {
           return (character >= '0' && character <= '9') || (character >= 'a' && character <= 'f');
         });
}

[[nodiscard]] std::optional<ManifestArtefact>
parse_manifest_artefact(const Json& value, const InterchangeKind kind, LocalError& error) {
  const auto snapshots = kind == InterchangeKind::snapshots;
  if (!exact_object(value,
                    snapshots
                        ? std::initializer_list<std::string_view>{"depth", "kind", "path",
                                                                  "record_count", "record_size",
                                                                  "schema_version", "sha256",
                                                                  "size_bytes"}
                        : std::initializer_list<std::string_view>{"kind", "path", "record_count",
                                                                  "record_size", "schema_version",
                                                                  "sha256", "size_bytes"},
                    snapshots ? "Snapshot artefact" : "Event artefact", error)) {
    return std::nullopt;
  }
  const auto expected_kind = kind_name(kind);
  const auto expected_path = snapshots ? kSnapshotFilename : kEventFilename;
  if (!value.at("kind").is_string() || value.at("kind").get<std::string>() != expected_kind ||
      !value.at("path").is_string() || value.at("path").get<std::string>() != expected_path) {
    error =
        LocalError{ErrorCode::invariant, "Replay manifest artefact kind or fixed path is invalid."};
    return std::nullopt;
  }
  const auto schema_version = json_count(value.at("schema_version"));
  if (!schema_version) {
    error = LocalError{ErrorCode::invariant, "Artefact schema version must be an integer."};
    return std::nullopt;
  }
  if (*schema_version != kInterchangeSchemaVersion) {
    error = LocalError{ErrorCode::schema_version,
                       "Replay manifest declares an unsupported interchange schema version.",
                       std::nullopt, std::to_string(kInterchangeSchemaVersion),
                       std::to_string(*schema_version)};
    return std::nullopt;
  }
  const auto record_count = json_count(value.at("record_count"));
  const auto record_size_value = json_count(value.at("record_size"));
  const auto size_bytes = json_count(value.at("size_bytes"));
  const auto hash = json_hash(value.at("sha256"));
  if (!record_count || !record_size_value || !size_bytes || !hash) {
    error = LocalError{ErrorCode::invariant,
                       "Replay manifest artefact counts, size or hash are invalid."};
    return std::nullopt;
  }
  const auto record_size = checked_integral_cast<std::uint16_t>(*record_size_value);
  std::uint16_t depth{};
  if (snapshots) {
    const auto depth_value = json_count(value.at("depth"));
    const auto converted =
        depth_value ? checked_integral_cast<std::uint16_t>(*depth_value) : std::nullopt;
    if (!converted || !snapshot_record_size(*converted)) {
      error = LocalError{ErrorCode::depth, "Snapshot artefact depth is outside version-1 bounds."};
      return std::nullopt;
    }
    depth = *converted;
  }
  const auto expected_record_size =
      snapshots ? snapshot_record_size(depth) : std::optional<std::uint16_t>{kEventRecordSize};
  if (!record_size || !expected_record_size || *record_size != *expected_record_size) {
    error = LocalError{ErrorCode::schema_version,
                       "Replay manifest artefact record size is unsupported."};
    return std::nullopt;
  }
  return ManifestArtefact{kind,
                          value.at("path").get<std::string>(),
                          static_cast<std::uint16_t>(*schema_version),
                          *record_size,
                          depth,
                          *record_count,
                          HashedFile{*hash, *size_bytes}};
}

[[nodiscard]] std::optional<ManifestData> parse_manifest(const std::string_view document,
                                                         LocalError& error) {
  const auto parsed = parse_strict_json(document, error);
  if (!parsed) {
    return std::nullopt;
  }
  const auto& root = *parsed;
  if (!root.is_object()) {
    error = LocalError{ErrorCode::invariant, "Replay manifest root must be an object."};
    return std::nullopt;
  }
  if (!root.contains("schema_version")) {
    error = LocalError{ErrorCode::invariant, "Replay manifest schema version is missing."};
    return std::nullopt;
  }
  const auto manifest_version = json_count(root.at("schema_version"));
  if (!manifest_version) {
    error = LocalError{ErrorCode::invariant, "Replay manifest schema version is invalid."};
    return std::nullopt;
  }
  if (*manifest_version != kReplayManifestSchemaVersion) {
    error = LocalError{ErrorCode::schema_version, "Replay manifest schema version is unsupported.",
                       std::nullopt, std::to_string(kReplayManifestSchemaVersion),
                       std::to_string(*manifest_version)};
    return std::nullopt;
  }
  if (root.contains("status") && root.at("status").is_string()) {
    const auto status = root.at("status").get<std::string>();
    if (status == "running" || status == "failed" || status == "cancelled") {
      error = LocalError{ErrorCode::partial_artefact,
                         "Replay manifest does not declare a completed artefact."};
      return std::nullopt;
    }
  }
  if (!exact_object(root,
                    {"artefacts", "build", "code_revision", "completed_at", "config",
                     "config_sha256", "counts", "error_summary", "executable_sha256",
                     "global_session_events", "identity_config_sha256", "identity_sha256",
                     "instruments", "publishable", "replay_id", "schema_version", "source",
                     "started_at", "status"},
                    "Replay manifest", error)) {
    return std::nullopt;
  }

  ManifestData result;
  if (!root.at("replay_id").is_string() || !valid_run_id(root.at("replay_id").get<std::string>())) {
    error = LocalError{ErrorCode::invariant, "Replay manifest has an invalid replay ID."};
    return std::nullopt;
  }
  result.replay_id = root.at("replay_id").get<std::string>();
  if (!root.at("status").is_string()) {
    error = LocalError{ErrorCode::invariant, "Replay manifest status must be a string."};
    return std::nullopt;
  }
  result.status = root.at("status").get<std::string>();
  if (result.status != "completed" && result.status != "degraded") {
    error = LocalError{ErrorCode::invariant, "Replay manifest status is unsupported."};
    return std::nullopt;
  }
  if (!root.at("publishable").is_boolean()) {
    error = LocalError{ErrorCode::invariant, "Replay manifest publishable flag is invalid."};
    return std::nullopt;
  }
  result.publishable = root.at("publishable").get<bool>();
  if (!root.at("started_at").is_string() || !root.at("completed_at").is_string() ||
      !valid_rfc3339_datetime(root.at("started_at").get<std::string>()) ||
      !valid_rfc3339_datetime(root.at("completed_at").get<std::string>())) {
    error = LocalError{ErrorCode::invariant,
                       "Replay manifest timestamps are not valid RFC 3339 date-times."};
    return std::nullopt;
  }

  const auto config_document =
      root.at("config").dump(-1, ' ', false, Json::error_handler_t::strict);
  const auto parsed_config = parse_replay_config(config_document);
  if (!parsed_config.valid()) {
    error = LocalError{parsed_config.issues.empty() ? ErrorCode::invariant
                                                    : parsed_config.issues.front().code,
                       "Replay manifest effective config is invalid."};
    return std::nullopt;
  }
  result.config = *parsed_config.config;
  if (!safe_basename(result.config.input.path) || !result.config.input.sha256) {
    error = LocalError{ErrorCode::invariant,
                       "Replay manifest config must contain a basename and source hash."};
    return std::nullopt;
  }
  const auto config_hash = json_hash(root.at("config_sha256"));
  const auto identity_config_hash = json_hash(root.at("identity_config_sha256"));
  const auto identity_hash = json_hash(root.at("identity_sha256"));
  const auto executable_hash = json_hash(root.at("executable_sha256"));
  if (!config_hash || !identity_config_hash || !identity_hash || !executable_hash) {
    error = LocalError{ErrorCode::invariant, "Replay manifest contains an invalid identity hash."};
    return std::nullopt;
  }
  result.config_hashes = ConfigHashes{*config_hash, *identity_config_hash};
  result.identity_sha256 = *identity_hash;
  result.executable_sha256 = *executable_hash;

  if (!exact_object(root.at("source"),
                    {"canonical_name", "compression", "exchange_timezone", "framing", "sha256",
                     "size_bytes", "trading_date"},
                    "Replay source", error)) {
    return std::nullopt;
  }
  const auto& source = root.at("source");
  if (!source.at("canonical_name").is_string() ||
      !safe_basename(source.at("canonical_name").get<std::string>()) ||
      !source.at("compression").is_string() ||
      (source.at("compression") != "none" && source.at("compression") != "gzip") ||
      source.at("exchange_timezone") != "America/New_York" ||
      source.at("framing") != "itch-length-v1" || !source.at("trading_date").is_string() ||
      !compact_date(source.at("trading_date").get<std::string>())) {
    error = LocalError{ErrorCode::invariant, "Replay manifest source metadata is invalid."};
    return std::nullopt;
  }
  const auto source_hash = json_hash(source.at("sha256"));
  const auto source_size = json_count(source.at("size_bytes"));
  if (!source_hash || !source_size) {
    error = LocalError{ErrorCode::invariant, "Replay manifest source hash or size is invalid."};
    return std::nullopt;
  }
  result.source = HashedFile{*source_hash, *source_size};
  result.source_name = source.at("canonical_name").get<std::string>();
  result.source_trading_date = source.at("trading_date").get<std::string>();

  if (!exact_object(root.at("build"),
                    {"application_version", "build_type", "compiler", "compiler_version", "target"},
                    "Replay build", error)) {
    return std::nullopt;
  }
  const auto& build = root.at("build");
  for (const auto key :
       {"application_version", "build_type", "compiler", "compiler_version", "target"}) {
    if (!json_nonempty_string(build.at(key))) {
      error = LocalError{ErrorCode::invariant, "Replay manifest build metadata is incomplete."};
      return std::nullopt;
    }
  }
  if (!root.at("code_revision").is_string() ||
      !valid_code_revision(root.at("code_revision").get<std::string>())) {
    error = LocalError{ErrorCode::invariant, "Replay manifest code revision is invalid."};
    return std::nullopt;
  }
  if (result.publishable && (build.at("build_type") != "Release" ||
                             root.at("code_revision").get<std::string>().ends_with("+dirty") ||
                             root.at("code_revision") == "unknown")) {
    error = LocalError{ErrorCode::invariant,
                       "Publishable replay metadata does not describe a clean Release build."};
    return std::nullopt;
  }

  if (!exact_object(root.at("counts"),
                    {"all_by_type", "decoded_messages", "directory_messages", "errors_observed",
                     "filtered_instrument_messages", "global_system_messages", "messages_processed",
                     "selected_by_type", "selected_events", "selected_instrument_messages",
                     "skipped_messages", "snapshots_written"},
                    "Replay counts", error) ||
      !validate_count_map(root.at("counts").at("all_by_type"), false, "all_by_type", error) ||
      !validate_count_map(root.at("counts").at("selected_by_type"), false, "selected_by_type",
                          error) ||
      !validate_count_map(root.at("error_summary"), true, "error_summary", error)) {
    return std::nullopt;
  }
  for (const auto key :
       {"decoded_messages", "directory_messages", "errors_observed", "filtered_instrument_messages",
        "global_system_messages", "messages_processed", "selected_events",
        "selected_instrument_messages", "skipped_messages", "snapshots_written"}) {
    if (!json_count(root.at("counts").at(key))) {
      error = LocalError{ErrorCode::invariant, "Replay manifest contains an invalid count."};
      return std::nullopt;
    }
  }
  result.selected_events = *json_count(root.at("counts").at("selected_events"));
  result.snapshots_written = *json_count(root.at("counts").at("snapshots_written"));

  if (!root.at("global_session_events").is_array()) {
    error = LocalError{ErrorCode::invariant, "Global session events must be an array."};
    return std::nullopt;
  }
  std::optional<std::uint64_t> last_global_index;
  std::optional<std::uint64_t> last_global_timestamp;
  constexpr std::string_view allowed_global_codes{"OSQMEC"};
  for (const auto& item : root.at("global_session_events")) {
    if (!exact_object(item, {"event_code", "message_index", "timestamp_ns"}, "Global session event",
                      error) ||
        !item.at("event_code").is_string() ||
        item.at("event_code").get<std::string>().size() != 1 ||
        allowed_global_codes.find(item.at("event_code").get<std::string>().front()) ==
            std::string_view::npos) {
      error = LocalError{ErrorCode::invariant, "Global session event is invalid."};
      return std::nullopt;
    }
    const auto index = json_count(item.at("message_index"));
    const auto timestamp = json_count(item.at("timestamp_ns"));
    if (!index || !timestamp || !is_valid_timestamp(*timestamp) ||
        (last_global_index && *index <= *last_global_index) ||
        (last_global_timestamp && *timestamp < *last_global_timestamp)) {
      error = LocalError{ErrorCode::invariant, "Global session event ordering is invalid."};
      return std::nullopt;
    }
    last_global_index = *index;
    last_global_timestamp = *timestamp;
  }

  if (!root.at("instruments").is_array() || root.at("instruments").empty()) {
    error = LocalError{ErrorCode::invariant,
                       "Replay manifest must contain at least one selected instrument."};
    return std::nullopt;
  }
  std::unordered_set<StockLocate> locates;
  std::unordered_set<std::string> symbols;
  for (std::size_t index = 0; index < root.at("instruments").size(); ++index) {
    const auto& item = root.at("instruments").at(index);
    if (!exact_object(item,
                      {"final_book_digest", "final_order_count", "final_trading_state",
                       "financial_status", "market_category", "round_lot_size", "round_lots_only",
                       "stock_locate", "symbol", "symbol_id"},
                      "Replay instrument", error)) {
      return std::nullopt;
    }
    const auto symbol_id_value = json_count(item.at("symbol_id"));
    const auto locate_value = json_count(item.at("stock_locate"));
    const auto round_lot_value = json_count(item.at("round_lot_size"));
    const auto final_count = json_count(item.at("final_order_count"));
    const auto digest = json_hash(item.at("final_book_digest"));
    const auto symbol_id =
        symbol_id_value ? checked_integral_cast<SymbolId>(*symbol_id_value) : std::nullopt;
    const auto stock_locate =
        locate_value ? checked_integral_cast<StockLocate>(*locate_value) : std::nullopt;
    const auto round_lot =
        round_lot_value ? checked_integral_cast<std::uint32_t>(*round_lot_value) : std::nullopt;
    const auto expected_id = checked_integral_cast<SymbolId>(index + 1);
    if (!symbol_id || !expected_id || *symbol_id != *expected_id || !stock_locate ||
        *stock_locate == 0 || !round_lot || !final_count || !digest ||
        !item.at("symbol").is_string() || !valid_symbol(item.at("symbol").get<std::string>()) ||
        !item.at("round_lots_only").is_boolean() || !item.at("market_category").is_string() ||
        item.at("market_category").get<std::string>().size() != 1 ||
        !valid_printable_ascii(item.at("market_category").get<std::string>()) ||
        !item.at("financial_status").is_string() ||
        item.at("financial_status").get<std::string>().size() != 1 ||
        !valid_printable_ascii(item.at("financial_status").get<std::string>()) ||
        !item.at("final_trading_state").is_string()) {
      error = LocalError{ErrorCode::invariant, "Replay manifest instrument is invalid."};
      return std::nullopt;
    }
    const auto symbol = item.at("symbol").get<std::string>();
    const auto state = item.at("final_trading_state").get<std::string>();
    constexpr std::array<std::string_view, 7> states{
        "unknown", "preopen", "trading", "halted", "paused", "quotation_only", "closed"};
    if (!locates.insert(*stock_locate).second || !symbols.insert(symbol).second ||
        std::ranges::find(states, state) == states.end()) {
      error = LocalError{ErrorCode::invariant,
                         "Replay manifest instrument identities or state are invalid."};
      return std::nullopt;
    }
    result.instruments.push_back(
        ManifestInstrument{DictionaryEntry{*symbol_id, *stock_locate, symbol, *round_lot},
                           *final_count, *digest, state});
  }

  if (!root.at("artefacts").is_array() || root.at("artefacts").size() != 2) {
    error = LocalError{ErrorCode::invariant,
                       "Replay manifest must contain exactly two ordered artefacts."};
    return std::nullopt;
  }
  const auto events =
      parse_manifest_artefact(root.at("artefacts").at(0), InterchangeKind::events, error);
  if (!events) {
    return std::nullopt;
  }
  const auto snapshots =
      parse_manifest_artefact(root.at("artefacts").at(1), InterchangeKind::snapshots, error);
  if (!snapshots) {
    return std::nullopt;
  }
  result.events = *events;
  result.snapshots = *snapshots;
  return result;
}

[[nodiscard]] BinaryResult
parse_binary_metadata(const std::filesystem::path& path, const HashedFile& hashed,
                      const std::optional<InterchangeKind> expected_kind) {
  std::ifstream stream{path, std::ios::binary};
  if (!stream.is_open()) {
    return BinaryResult{std::nullopt,
                        LocalError{ErrorCode::input_path,
                                   "Interchange artefact could not be opened for validation."}};
  }
  std::array<std::byte, kInterchangeHeaderSize> header{};
  stream.read(reinterpret_cast<char*>(header.data()), static_cast<std::streamsize>(header.size()));
  if (stream.gcount() != static_cast<std::streamsize>(header.size())) {
    return BinaryResult{std::nullopt,
                        LocalError{ErrorCode::partial_artefact,
                                   "Interchange artefact does not contain a complete header."}};
  }
  if (all_zero(std::span{header})) {
    return BinaryResult{std::nullopt,
                        LocalError{ErrorCode::partial_artefact,
                                   "Interchange artefact contains a placeholder partial header."}};
  }

  InterchangeKind kind;
  if (bytes_equal(std::span{header}.first(kEventMagic.size()), std::span{kEventMagic})) {
    kind = InterchangeKind::events;
  } else if (bytes_equal(std::span{header}.first(kSnapshotMagic.size()),
                         std::span{kSnapshotMagic})) {
    kind = InterchangeKind::snapshots;
  } else {
    return BinaryResult{std::nullopt,
                        LocalError{ErrorCode::schema_version,
                                   "Interchange magic is not a supported event-v1 or snapshot-v1 "
                                   "identifier."}};
  }
  if (expected_kind && kind != *expected_kind) {
    return BinaryResult{std::nullopt,
                        LocalError{ErrorCode::schema_version,
                                   "Interchange file kind does not match its manifest entry."}};
  }

  const auto schema_version = little_u16(header, 8);
  const auto header_size = little_u16(header, 10);
  const auto record_size = little_u16(header, 12);
  const auto depth = little_u16(header, 14);
  const auto price_scale = little_u32(header, 16);
  const auto trading_date = little_u32(header, 20);
  const auto symbol_count = little_u16(header, 24);
  const auto header_flags = little_u16(header, 26);
  const auto record_count = little_u64(header, 28);
  if (!schema_version || !header_size || !record_size || !depth || !price_scale || !trading_date ||
      !symbol_count || !header_flags || !record_count) {
    return BinaryResult{
        std::nullopt,
        LocalError{ErrorCode::internal, "Interchange header decoder bounds are inconsistent."}};
  }
  if (*schema_version != kInterchangeSchemaVersion) {
    return BinaryResult{std::nullopt,
                        LocalError{ErrorCode::schema_version,
                                   "Interchange schema version is unsupported.", std::nullopt,
                                   std::to_string(kInterchangeSchemaVersion),
                                   std::to_string(*schema_version)}};
  }
  if (*header_size != kInterchangeHeaderSize || *price_scale != kInterchangePriceScale) {
    return BinaryResult{std::nullopt,
                        LocalError{ErrorCode::schema_version,
                                   "Interchange header size or price scale is unsupported."}};
  }
  if (*symbol_count == 0 || !valid_trading_date(*trading_date) || (*header_flags & 0xfffeU) != 0 ||
      !bytes_are_zero(header, 100, 4)) {
    return BinaryResult{std::nullopt,
                        LocalError{ErrorCode::invariant,
                                   "Interchange header date, flags, dictionary count or reserved "
                                   "bytes are invalid."}};
  }
  const auto expected_record_size = kind == InterchangeKind::events
                                        ? std::optional<std::uint16_t>{kEventRecordSize}
                                        : snapshot_record_size(*depth);
  if ((kind == InterchangeKind::events && *depth != 0) ||
      (kind == InterchangeKind::snapshots && !expected_record_size) || !expected_record_size ||
      *record_size != *expected_record_size) {
    return BinaryResult{std::nullopt,
                        LocalError{ErrorCode::schema_version,
                                   "Interchange depth and record size do not match version 1."}};
  }

  ContentHash config_hash{};
  ContentHash source_hash{};
  std::ranges::copy(std::span{header}.subspan(36, config_hash.size()), config_hash.begin());
  std::ranges::copy(std::span{header}.subspan(68, source_hash.size()), source_hash.begin());
  if (all_zero(config_hash) || all_zero(source_hash)) {
    return BinaryResult{std::nullopt,
                        LocalError{ErrorCode::partial_artefact,
                                   "Interchange header contains placeholder identity hashes."}};
  }

  std::vector<DictionaryEntry> dictionary;
  dictionary.reserve(*symbol_count);
  std::unordered_set<StockLocate> locates;
  std::unordered_set<std::string> symbols;
  std::array<std::byte, kInterchangeSymbolEntrySize> encoded_entry{};
  for (std::uint32_t index = 0; index < *symbol_count; ++index) {
    stream.read(reinterpret_cast<char*>(encoded_entry.data()),
                static_cast<std::streamsize>(encoded_entry.size()));
    if (stream.gcount() != static_cast<std::streamsize>(encoded_entry.size())) {
      return BinaryResult{std::nullopt,
                          LocalError{ErrorCode::partial_artefact,
                                     "Interchange artefact has an incomplete symbol dictionary."}};
    }
    const auto symbol_id = little_u16(encoded_entry, 0);
    const auto stock_locate = little_u16(encoded_entry, 2);
    const auto round_lot = little_u32(encoded_entry, 12);
    const auto expected_id = checked_integral_cast<SymbolId>(index + 1U);
    std::string padded_symbol;
    padded_symbol.reserve(8);
    for (std::size_t offset = 4; offset < 12; ++offset) {
      padded_symbol.push_back(
          static_cast<char>(std::to_integer<unsigned char>(encoded_entry[offset])));
    }
    while (!padded_symbol.empty() && padded_symbol.back() == ' ') {
      padded_symbol.pop_back();
    }
    if (!symbol_id || !stock_locate || !round_lot || !expected_id || *symbol_id != *expected_id ||
        *stock_locate == 0 || !valid_symbol(padded_symbol) ||
        !locates.insert(*stock_locate).second || !symbols.insert(padded_symbol).second) {
      return BinaryResult{std::nullopt,
                          LocalError{ErrorCode::invariant,
                                     "Interchange symbol dictionary is not canonical or unique."}};
    }
    dictionary.push_back(
        DictionaryEntry{*symbol_id, *stock_locate, std::move(padded_symbol), *round_lot});
  }

  const auto dictionary_bytes =
      checked_multiply<std::uint64_t>(static_cast<std::uint64_t>(*symbol_count),
                                      static_cast<std::uint64_t>(kInterchangeSymbolEntrySize));
  const auto records_bytes =
      checked_multiply<std::uint64_t>(*record_count, static_cast<std::uint64_t>(*record_size));
  const auto prefix_bytes =
      dictionary_bytes ? checked_add<std::uint64_t>(kInterchangeHeaderSize, *dictionary_bytes)
                       : std::nullopt;
  const auto expected_size =
      prefix_bytes && records_bytes ? checked_add(*prefix_bytes, *records_bytes) : std::nullopt;
  if (!expected_size || hashed.size_bytes != *expected_size) {
    return BinaryResult{
        std::nullopt,
        LocalError{ErrorCode::partial_artefact,
                   "Interchange file size does not match its dictionary and record count.",
                   std::nullopt,
                   expected_size ? std::optional<std::string>{std::to_string(*expected_size)}
                                 : std::optional<std::string>{"representable size"},
                   std::to_string(hashed.size_bytes)}};
  }

  return BinaryResult{BinaryMetadata{kind, *schema_version, *record_size, *depth, *trading_date,
                                     (*header_flags & 1U) != 0, *record_count, config_hash,
                                     source_hash, std::move(dictionary), hashed},
                      std::nullopt};
}

[[nodiscard]] std::uint16_t event_required_flags(const std::uint8_t kind,
                                                 const char source_type) noexcept {
  constexpr std::uint16_t primary = event_primary_reference_valid;
  constexpr std::uint16_t secondary = event_secondary_reference_valid;
  constexpr std::uint16_t side = event_side_valid;
  constexpr std::uint16_t price = event_price4_valid;
  constexpr std::uint16_t quantity = event_quantity_valid;
  constexpr std::uint16_t remaining = event_remaining_quantity_valid;
  constexpr std::uint16_t execution_price = event_execution_price4_valid;
  constexpr std::uint16_t auxiliary = event_aux_code_valid;
  constexpr std::uint16_t subtype = event_subtype_valid;
  switch (static_cast<EventKindCode>(kind)) {
  case EventKindCode::add:
    return static_cast<std::uint16_t>(primary | side | price | quantity | remaining |
                                      (source_type == 'F' ? auxiliary : 0));
  case EventKindCode::execute:
    return primary | secondary | side | price | quantity | remaining;
  case EventKindCode::execute_price:
    return primary | secondary | side | price | quantity | remaining | execution_price;
  case EventKindCode::cancel:
  case EventKindCode::delete_order:
    return primary | side | price | quantity | remaining;
  case EventKindCode::replace:
    return primary | secondary | side | price | quantity | remaining;
  case EventKindCode::trade:
    return primary | secondary | side | price | quantity;
  case EventKindCode::cross:
    return secondary | price | quantity | subtype;
  case EventKindCode::broken_trade:
    return primary;
  case EventKindCode::trading_state:
    return subtype;
  }
  return 0;
}

[[nodiscard]] bool kind_matches_source(const std::uint8_t kind, const char source_type) noexcept {
  switch (static_cast<EventKindCode>(kind)) {
  case EventKindCode::add:
    return source_type == 'A' || source_type == 'F';
  case EventKindCode::execute:
    return source_type == 'E';
  case EventKindCode::execute_price:
    return source_type == 'C';
  case EventKindCode::cancel:
    return source_type == 'X';
  case EventKindCode::delete_order:
    return source_type == 'D';
  case EventKindCode::replace:
    return source_type == 'U';
  case EventKindCode::trade:
    return source_type == 'P';
  case EventKindCode::cross:
    return source_type == 'Q';
  case EventKindCode::broken_trade:
    return source_type == 'B';
  case EventKindCode::trading_state:
    return source_type == 'H';
  }
  return false;
}

struct DecodedEventRecord {
  MessageIndex message_index{};
  TimestampNs timestamp_ns{};
  OrderReference primary_reference{};
  OrderReference secondary_reference{};
  Shares quantity{};
  Price4 price4{};
  Shares remaining_quantity{};
  SymbolId symbol_id{};
  std::uint8_t kind{};
  Side side{Side::not_applicable};
  char source_type{};
  std::uint16_t flags{};
  std::array<char, 4> auxiliary{};
};

[[nodiscard]] std::optional<LocalError>
decode_event_record(const std::span<const std::byte> bytes, const BinaryMetadata& metadata,
                    const std::uint64_t record_index, std::optional<MessageIndex>& last_index,
                    std::optional<TimestampNs>& last_timestamp, DecodedEventRecord& output) {
  const auto message_index = little_u64(bytes, 0);
  const auto timestamp = little_u64(bytes, 8);
  const auto primary = little_u64(bytes, 16);
  const auto secondary = little_u64(bytes, 24);
  const auto quantity = little_u64(bytes, 32);
  const auto price = little_u32(bytes, 40);
  const auto remaining = little_u32(bytes, 44);
  const auto execution_price = little_u32(bytes, 48);
  const auto symbol_id = little_u16(bytes, 52);
  const auto flags = little_u16(bytes, 57);
  if (!message_index || !timestamp || !primary || !secondary || !quantity || !price || !remaining ||
      !execution_price || !symbol_id || !flags) {
    return LocalError{ErrorCode::internal, "Event-v1 decoder bounds are inconsistent.",
                      record_index};
  }
  const auto kind = std::to_integer<std::uint8_t>(bytes[54]);
  const auto encoded_side = static_cast<std::int8_t>(std::to_integer<std::uint8_t>(bytes[55]));
  const auto source_type = static_cast<char>(std::to_integer<unsigned char>(bytes[56]));
  if (kind < static_cast<std::uint8_t>(EventKindCode::add) ||
      kind > static_cast<std::uint8_t>(EventKindCode::trading_state) ||
      !kind_matches_source(kind, source_type)) {
    return LocalError{ErrorCode::invariant,
                      "Event-v1 kind and source type are unsupported or inconsistent.",
                      record_index};
  }
  constexpr std::uint16_t allowed_flags = (1U << 10U) - 1U;
  const auto required_flags = event_required_flags(kind, source_type);
  const auto optional_flags = static_cast<std::uint16_t>(
      event_in_session |
      (kind == static_cast<std::uint8_t>(EventKindCode::trading_state) ? event_aux_code_valid : 0));
  if ((*flags & ~allowed_flags) != 0 || (*flags & required_flags) != required_flags ||
      (*flags & ~(required_flags | optional_flags)) != 0 || !bytes_are_zero(bytes, 59, 1) ||
      !bytes_are_zero(bytes, 65, 7)) {
    return LocalError{ErrorCode::invariant,
                      "Event-v1 validity flags or reserved bytes are invalid.", record_index};
  }
  if (*symbol_id == 0 || *symbol_id > metadata.dictionary.size() ||
      !is_valid_timestamp(*timestamp) || (last_index && *message_index <= *last_index) ||
      (last_timestamp && *timestamp < *last_timestamp)) {
    return LocalError{ErrorCode::invariant,
                      "Event-v1 symbol, timestamp or source ordering is invalid.", record_index};
  }

  const auto field_is_zero_when_absent =
      [bytes, flags](const std::uint16_t flag, const std::size_t offset, const std::size_t size) {
        return (*flags & flag) != 0 || bytes_are_zero(bytes, offset, size);
      };
  if (!field_is_zero_when_absent(event_primary_reference_valid, 16, 8) ||
      !field_is_zero_when_absent(event_secondary_reference_valid, 24, 8) ||
      !field_is_zero_when_absent(event_quantity_valid, 32, 8) ||
      !field_is_zero_when_absent(event_price4_valid, 40, 4) ||
      !field_is_zero_when_absent(event_remaining_quantity_valid, 44, 4) ||
      !field_is_zero_when_absent(event_execution_price4_valid, 48, 4) ||
      !field_is_zero_when_absent(event_side_valid, 55, 1) ||
      !field_is_zero_when_absent(event_aux_code_valid, 60, 4) ||
      !field_is_zero_when_absent(event_subtype_valid, 64, 1)) {
    return LocalError{ErrorCode::invariant,
                      "Event-v1 absent fields do not use the canonical zero representation.",
                      record_index};
  }
  if (((*flags & event_side_valid) != 0 && encoded_side != -1 && encoded_side != 1) ||
      ((*flags & event_side_valid) == 0 && encoded_side != 0)) {
    return LocalError{ErrorCode::invariant, "Event-v1 side encoding is invalid.", record_index};
  }
  if ((*flags & event_quantity_valid) != 0 && *quantity == 0) {
    return LocalError{ErrorCode::quantity, "Event-v1 valid quantity must be positive.",
                      record_index};
  }
  if (kind == static_cast<std::uint8_t>(EventKindCode::add) && *remaining != *quantity) {
    return LocalError{ErrorCode::quantity,
                      "Event-v1 add remaining quantity must equal its quantity.", record_index};
  }
  if (kind == static_cast<std::uint8_t>(EventKindCode::delete_order) && *remaining != 0) {
    return LocalError{ErrorCode::quantity, "Event-v1 delete must record a zero remaining quantity.",
                      record_index};
  }
  if (kind == static_cast<std::uint8_t>(EventKindCode::replace) && *remaining != *quantity) {
    return LocalError{ErrorCode::quantity,
                      "Event-v1 replacement remaining quantity must equal its new quantity.",
                      record_index};
  }
  if ((*flags & event_aux_code_valid) != 0) {
    for (std::size_t index = 60; index < 64; ++index) {
      const auto byte = std::to_integer<unsigned char>(bytes[index]);
      if (byte < 0x20U || byte > 0x7eU) {
        return LocalError{ErrorCode::invariant, "Event-v1 auxiliary code is not printable ASCII.",
                          record_index};
      }
    }
  }
  if ((*flags & event_subtype_valid) != 0) {
    const auto subtype = std::to_integer<unsigned char>(bytes[64]);
    if (subtype > 0x7fU || (kind == static_cast<std::uint8_t>(EventKindCode::trading_state) &&
                            subtype != 'H' && subtype != 'P' && subtype != 'Q' && subtype != 'T')) {
      return LocalError{ErrorCode::invariant, "Event-v1 subtype is invalid.", record_index};
    }
  }

  output = DecodedEventRecord{
      *message_index,
      *timestamp,
      *primary,
      *secondary,
      *quantity,
      *price,
      *remaining,
      *symbol_id,
      kind,
      encoded_side == -1 ? Side::sell : (encoded_side == 1 ? Side::buy : Side::not_applicable),
      source_type,
      *flags,
      {static_cast<char>(std::to_integer<unsigned char>(bytes[60])),
       static_cast<char>(std::to_integer<unsigned char>(bytes[61])),
       static_cast<char>(std::to_integer<unsigned char>(bytes[62])),
       static_cast<char>(std::to_integer<unsigned char>(bytes[63]))}};
  last_index = *message_index;
  last_timestamp = *timestamp;
  return std::nullopt;
}

[[nodiscard]] std::optional<LocalError>
apply_reconstructed_event(const DecodedEventRecord& event, const BinaryMetadata& metadata,
                          std::vector<std::unique_ptr<OrderBook>>& books,
                          const std::uint64_t record_index) {
  const auto dictionary_index = static_cast<std::size_t>(event.symbol_id - 1U);
  const auto stock_locate = metadata.dictionary[dictionary_index].stock_locate;
  std::optional<BookMessage> message;
  switch (static_cast<EventKindCode>(event.kind)) {
  case EventKindCode::add: {
    std::optional<BookAttribution> attribution;
    if (event.source_type == 'F') {
      attribution = event.auxiliary;
    }
    message = BookAdd{event.message_index, stock_locate,   event.primary_reference,
                      event.side,          event.quantity, event.price4,
                      attribution};
    break;
  }
  case EventKindCode::execute:
  case EventKindCode::execute_price:
    message =
        BookExecute{event.message_index, stock_locate, event.primary_reference, event.quantity};
    break;
  case EventKindCode::cancel:
    message =
        BookCancel{event.message_index, stock_locate, event.primary_reference, event.quantity};
    break;
  case EventKindCode::delete_order:
    message = BookDelete{event.message_index, stock_locate, event.primary_reference};
    break;
  case EventKindCode::replace:
    message = BookReplace{event.message_index,       stock_locate,   event.primary_reference,
                          event.secondary_reference, event.quantity, event.price4};
    break;
  case EventKindCode::trade:
  case EventKindCode::cross:
  case EventKindCode::broken_trade:
  case EventKindCode::trading_state:
    return std::nullopt;
  }

  const auto applied = books[dictionary_index]->apply(*message);
  if (!applied.valid()) {
    return LocalError{applied.error->code,
                      "Deep event reconstruction failed: " + applied.error->message, record_index};
  }
  const auto& delta = *applied.delta;
  if (delta.side != event.side || delta.price4 != event.price4 ||
      delta.remaining != event.remaining_quantity ||
      (delta.kind == BookMutationKind::delete_order &&
       delta.previous_remaining != event.quantity)) {
    return LocalError{ErrorCode::invariant,
                      "Event-v1 recorded mutation fields disagree with reconstructed book state.",
                      record_index};
  }
  return std::nullopt;
}

[[nodiscard]] DeepResult
deep_validate_events(const std::filesystem::path& path, const BinaryMetadata& metadata,
                     const std::vector<ManifestInstrument>* expected_instruments,
                     const ReplayConfig* expected_config) {
  std::ifstream stream{path, std::ios::binary};
  if (!stream.is_open()) {
    return DeepResult{0, LocalError{ErrorCode::input_path,
                                    "Event artefact could not be reopened for deep validation."}};
  }
  const auto dictionary_bytes =
      checked_multiply<std::uint64_t>(static_cast<std::uint64_t>(metadata.dictionary.size()),
                                      static_cast<std::uint64_t>(kInterchangeSymbolEntrySize));
  const auto records_offset =
      dictionary_bytes ? checked_add<std::uint64_t>(kInterchangeHeaderSize, *dictionary_bytes)
                       : std::nullopt;
  const auto converted_offset =
      records_offset ? checked_integral_cast<std::streamoff>(*records_offset) : std::nullopt;
  if (!converted_offset) {
    return DeepResult{0, LocalError{ErrorCode::internal,
                                    "Event record offset exceeds the supported stream range."}};
  }
  stream.seekg(*converted_offset, std::ios::beg);
  if (!stream.good()) {
    return DeepResult{
        0, LocalError{ErrorCode::partial_artefact, "Event record region could not be reached."}};
  }

  std::vector<std::unique_ptr<OrderBook>> books;
  if (expected_instruments != nullptr) {
    books.reserve(metadata.dictionary.size());
    for (const auto& item : metadata.dictionary) {
      books.push_back(std::make_unique<OrderBook>(item.stock_locate));
    }
  }

  std::array<std::byte, kEventRecordSize> record{};
  std::optional<MessageIndex> last_index;
  std::optional<TimestampNs> last_timestamp;
  std::uint64_t examined{};
  for (std::uint64_t index = 0; index < metadata.record_count; ++index) {
    stream.read(reinterpret_cast<char*>(record.data()),
                static_cast<std::streamsize>(record.size()));
    if (stream.gcount() != static_cast<std::streamsize>(record.size())) {
      return DeepResult{examined,
                        LocalError{ErrorCode::partial_artefact,
                                   "Event record region ended before the declared record count.",
                                   index}};
    }
    DecodedEventRecord decoded;
    if (const auto error =
            decode_event_record(record, metadata, index, last_index, last_timestamp, decoded)) {
      return DeepResult{examined, error};
    }
    if (expected_config != nullptr) {
      const auto expected_in_session =
          decoded.timestamp_ns >= expected_config->selection.session_start_ns &&
          decoded.timestamp_ns < expected_config->selection.session_end_ns;
      const auto actual_in_session = (decoded.flags & event_in_session) != 0;
      if (decoded.timestamp_ns >= expected_config->selection.session_end_ns ||
          actual_in_session != expected_in_session) {
        return DeepResult{
            examined,
            LocalError{ErrorCode::invariant,
                       "Event-v1 in-session flag disagrees with the effective replay config.",
                       index}};
      }
    }
    if (expected_instruments != nullptr) {
      if (const auto error = apply_reconstructed_event(decoded, metadata, books, index)) {
        return DeepResult{examined, error};
      }
    }
    ++examined;
  }

  if (expected_instruments != nullptr) {
    if (expected_instruments->size() != books.size()) {
      return DeepResult{examined,
                        LocalError{ErrorCode::invariant,
                                   "Manifest and reconstructed instrument counts disagree."}};
    }
    for (std::size_t index = 0; index < books.size(); ++index) {
      const auto actual_count = checked_integral_cast<std::uint64_t>(books[index]->order_count());
      if (!actual_count || *actual_count != expected_instruments->at(index).final_order_count) {
        return DeepResult{
            examined,
            LocalError{ErrorCode::invariant,
                       "Reconstructed final order count disagrees with the replay manifest.",
                       std::nullopt,
                       std::to_string(expected_instruments->at(index).final_order_count),
                       actual_count ? std::optional<std::string>{std::to_string(*actual_count)}
                                    : std::optional<std::string>{"unrepresentable"}}};
      }
      const auto actual_digest = books[index]->digest();
      if (actual_digest != expected_instruments->at(index).final_book_digest) {
        return DeepResult{
            examined,
            LocalError{ErrorCode::hash_mismatch,
                       "Reconstructed final book digest disagrees with the replay manifest.",
                       std::nullopt,
                       content_hash_to_hex(expected_instruments->at(index).final_book_digest),
                       content_hash_to_hex(actual_digest)}};
      }
    }
  }

  const auto rehashed = hash_file(path, ErrorCode::hash_mismatch);
  if (!rehashed.valid() || *rehashed.file != metadata.file) {
    const auto message =
        rehashed.error ? rehashed.error->message : "Event artefact changed during deep validation.";
    return DeepResult{examined, LocalError{ErrorCode::hash_mismatch, message}};
  }
  return DeepResult{examined, std::nullopt};
}

[[nodiscard]] std::optional<LocalError>
validate_snapshot_record(const std::span<const std::byte> bytes, const BinaryMetadata& metadata,
                         const std::uint64_t record_index, std::optional<MessageIndex>& last_index,
                         std::optional<TimestampNs>& last_timestamp) {
  const auto message_index = little_u64(bytes, 0);
  const auto timestamp = little_u64(bytes, 8);
  const auto symbol_id = little_u16(bytes, 16);
  const auto trigger_price = little_u32(bytes, 20);
  const auto trigger_quantity = little_u64(bytes, 24);
  const auto last_trade_price = little_u32(bytes, 32);
  const auto last_trade_quantity = little_u64(bytes, 40);
  if (!message_index || !timestamp || !symbol_id || !trigger_price || !trigger_quantity ||
      !last_trade_price || !last_trade_quantity) {
    return LocalError{ErrorCode::internal, "Snapshot-v1 decoder bounds are inconsistent.",
                      record_index};
  }
  const auto kind = std::to_integer<std::uint8_t>(bytes[18]);
  const auto flags = std::to_integer<std::uint8_t>(bytes[19]);
  const auto state = static_cast<std::uint8_t>((flags >> 3U) & 0x07U);
  if (kind < static_cast<std::uint8_t>(EventKindCode::add) ||
      kind > static_cast<std::uint8_t>(EventKindCode::trading_state) || (flags & 0x80U) != 0 ||
      state > static_cast<std::uint8_t>(TradingState::closed) || !bytes_are_zero(bytes, 36, 4)) {
    return LocalError{ErrorCode::invariant,
                      "Snapshot-v1 kind, flags, state or reserved prefix is invalid.",
                      record_index};
  }
  if (*symbol_id == 0 || *symbol_id > metadata.dictionary.size() ||
      !is_valid_timestamp(*timestamp) || (last_index && *message_index <= *last_index) ||
      (last_timestamp && *timestamp < *last_timestamp)) {
    return LocalError{ErrorCode::invariant,
                      "Snapshot-v1 symbol, timestamp or source ordering is invalid.", record_index};
  }
  if (((flags & snapshot_trigger_price_valid) == 0 && *trigger_price != 0) ||
      ((flags & snapshot_trigger_quantity_valid) == 0 && *trigger_quantity != 0) ||
      ((flags & snapshot_trigger_quantity_valid) != 0 && *trigger_quantity == 0) ||
      ((flags & snapshot_last_trade_valid) == 0 &&
       (*last_trade_price != 0 || *last_trade_quantity != 0)) ||
      ((flags & snapshot_last_trade_valid) != 0 && *last_trade_quantity == 0)) {
    return LocalError{ErrorCode::invariant, "Snapshot-v1 nullable prefix fields are not canonical.",
                      record_index};
  }
  const auto trigger_fields = static_cast<std::uint8_t>(
      flags & (snapshot_trigger_price_valid | snapshot_trigger_quantity_valid));
  const auto both_trigger_fields =
      static_cast<std::uint8_t>(snapshot_trigger_price_valid | snapshot_trigger_quantity_valid);
  const auto top_changed = (flags & snapshot_top_n_changed) != 0;
  const auto event_kind = static_cast<EventKindCode>(kind);
  const auto book_mutation =
      event_kind >= EventKindCode::add && event_kind <= EventKindCode::replace;
  const auto unchanged_trade =
      event_kind == EventKindCode::trade || event_kind == EventKindCode::cross;
  const auto state_change = event_kind == EventKindCode::trading_state;
  if ((book_mutation && (trigger_fields != both_trigger_fields || !top_changed)) ||
      (unchanged_trade && (trigger_fields != both_trigger_fields || top_changed)) ||
      (state_change && (trigger_fields != 0 || top_changed)) ||
      event_kind == EventKindCode::broken_trade) {
    return LocalError{ErrorCode::invariant,
                      "Snapshot-v1 trigger fields or top-change flag disagree with its event kind.",
                      record_index};
  }

  bool bid_gap{};
  bool ask_gap{};
  std::optional<Price4> previous_bid;
  std::optional<Price4> previous_ask;
  for (std::size_t level = 0; level < metadata.depth; ++level) {
    const auto offset = static_cast<std::size_t>(kSnapshotFixedRecordSize) +
                        level * static_cast<std::size_t>(kSnapshotDepthEntrySize);
    const auto bid_valid = std::to_integer<std::uint8_t>(bytes[offset]);
    const auto ask_valid = std::to_integer<std::uint8_t>(bytes[offset + 1]);
    const auto bid_price = little_u32(bytes, offset + 4);
    const auto bid_quantity = little_u64(bytes, offset + 8);
    const auto ask_price = little_u32(bytes, offset + 16);
    const auto ask_quantity = little_u64(bytes, offset + 20);
    if (!bid_price || !bid_quantity || !ask_price || !ask_quantity || bid_valid > 1 ||
        ask_valid > 1 || !bytes_are_zero(bytes, offset + 2, 2)) {
      return LocalError{ErrorCode::invariant,
                        "Snapshot-v1 depth validity or reserved bytes are invalid.", record_index};
    }
    if ((bid_valid == 0 && (*bid_price != 0 || *bid_quantity != 0)) ||
        (ask_valid == 0 && (*ask_price != 0 || *ask_quantity != 0)) ||
        (bid_valid == 1 &&
         (*bid_quantity == 0 || bid_gap || (previous_bid && *bid_price >= *previous_bid))) ||
        (ask_valid == 1 &&
         (*ask_quantity == 0 || ask_gap || (previous_ask && *ask_price <= *previous_ask)))) {
      return LocalError{ErrorCode::invariant,
                        "Snapshot-v1 depth values are non-canonical or not best-to-worst.",
                        record_index};
    }
    if (bid_valid == 0) {
      bid_gap = true;
    } else {
      previous_bid = *bid_price;
    }
    if (ask_valid == 0) {
      ask_gap = true;
    } else {
      previous_ask = *ask_price;
    }
  }

  last_index = *message_index;
  last_timestamp = *timestamp;
  return std::nullopt;
}

[[nodiscard]] DeepResult deep_validate_snapshots(const std::filesystem::path& path,
                                                 const BinaryMetadata& metadata) {
  std::ifstream stream{path, std::ios::binary};
  if (!stream.is_open()) {
    return DeepResult{0,
                      LocalError{ErrorCode::input_path,
                                 "Snapshot artefact could not be reopened for deep validation."}};
  }
  const auto dictionary_bytes =
      checked_multiply<std::uint64_t>(static_cast<std::uint64_t>(metadata.dictionary.size()),
                                      static_cast<std::uint64_t>(kInterchangeSymbolEntrySize));
  const auto records_offset =
      dictionary_bytes ? checked_add<std::uint64_t>(kInterchangeHeaderSize, *dictionary_bytes)
                       : std::nullopt;
  const auto converted_offset =
      records_offset ? checked_integral_cast<std::streamoff>(*records_offset) : std::nullopt;
  if (!converted_offset) {
    return DeepResult{
        0, LocalError{ErrorCode::internal, "Snapshot record offset exceeds the stream range."}};
  }
  stream.seekg(*converted_offset, std::ios::beg);
  if (!stream.good()) {
    return DeepResult{
        0, LocalError{ErrorCode::partial_artefact, "Snapshot record region could not be reached."}};
  }

  std::vector<std::byte> record(metadata.record_size);
  std::optional<MessageIndex> last_index;
  std::optional<TimestampNs> last_timestamp;
  std::uint64_t examined{};
  for (std::uint64_t index = 0; index < metadata.record_count; ++index) {
    stream.read(reinterpret_cast<char*>(record.data()),
                static_cast<std::streamsize>(record.size()));
    if (stream.gcount() != static_cast<std::streamsize>(record.size())) {
      return DeepResult{
          examined, LocalError{ErrorCode::partial_artefact,
                               "Snapshot region ended before the declared record count.", index}};
    }
    if (const auto error =
            validate_snapshot_record(record, metadata, index, last_index, last_timestamp)) {
      return DeepResult{examined, error};
    }
    ++examined;
  }
  const auto rehashed = hash_file(path, ErrorCode::hash_mismatch);
  if (!rehashed.valid() || *rehashed.file != metadata.file) {
    const auto message = rehashed.error ? rehashed.error->message
                                        : "Snapshot artefact changed during deep validation.";
    return DeepResult{examined, LocalError{ErrorCode::hash_mismatch, message}};
  }
  return DeepResult{examined, std::nullopt};
}

[[nodiscard]] std::optional<LocalError> validate_manifest_lineage(const ManifestData& manifest,
                                                                  std::string_view directory_name) {
  const auto calculated_hashes = replay_config_hashes(manifest.config);
  if (calculated_hashes.config_sha256 != manifest.config_hashes.config_sha256 ||
      calculated_hashes.identity_config_sha256 != manifest.config_hashes.identity_config_sha256) {
    return LocalError{ErrorCode::hash_mismatch,
                      "Replay manifest config hashes do not match its canonical effective config."};
  }
  if (!manifest.config.input.sha256 || *manifest.config.input.sha256 != manifest.source.sha256 ||
      manifest.config.input.path != manifest.source_name ||
      manifest.config.input.trading_date != manifest.source_trading_date) {
    return LocalError{ErrorCode::invariant, "Replay manifest config and source metadata disagree."};
  }
  const auto identity =
      replay_identity_hash(manifest.source.sha256, manifest.config_hashes.identity_config_sha256,
                           manifest.executable_sha256);
  const auto identity_text = content_hash_to_hex(identity);
  if (identity != manifest.identity_sha256 ||
      !manifest.replay_id.ends_with(identity_text.substr(0, 12)) ||
      manifest.replay_id != directory_name) {
    return LocalError{ErrorCode::hash_mismatch,
                      "Replay identity, replay ID and directory name are inconsistent."};
  }
  if (manifest.events.record_count != manifest.selected_events ||
      manifest.snapshots.record_count != manifest.snapshots_written ||
      manifest.snapshots.depth != manifest.config.output.depth ||
      manifest.instruments.size() != manifest.config.selection.symbols.size()) {
    return LocalError{ErrorCode::invariant,
                      "Replay manifest counts, depth or selected-instrument cardinality disagree."};
  }
  for (std::size_t index = 0; index < manifest.instruments.size(); ++index) {
    if (manifest.instruments[index].dictionary.symbol != manifest.config.selection.symbols[index]) {
      return LocalError{ErrorCode::invariant,
                        "Replay manifest instrument order disagrees with the effective config."};
    }
  }
  return std::nullopt;
}

[[nodiscard]] std::optional<LocalError>
validate_cross_file_metadata(const ManifestData& manifest, const BinaryMetadata& events,
                             const BinaryMetadata& snapshots) {
  const auto expected_date = compact_date(manifest.source_trading_date);
  if (!expected_date) {
    return LocalError{ErrorCode::trading_date,
                      "Replay manifest trading date cannot be compared with binary headers."};
  }
  if (events.kind != InterchangeKind::events || snapshots.kind != InterchangeKind::snapshots ||
      events.schema_version != manifest.events.schema_version ||
      snapshots.schema_version != manifest.snapshots.schema_version ||
      events.record_size != manifest.events.record_size ||
      snapshots.record_size != manifest.snapshots.record_size ||
      events.record_count != manifest.events.record_count ||
      snapshots.record_count != manifest.snapshots.record_count ||
      snapshots.depth != manifest.snapshots.depth || events.depth != 0 ||
      events.file != manifest.events.file || snapshots.file != manifest.snapshots.file) {
    return LocalError{ErrorCode::invariant,
                      "Binary headers disagree with manifest artefact metadata."};
  }
  const auto degraded = manifest.status == "degraded";
  if (events.trading_date != *expected_date || snapshots.trading_date != *expected_date ||
      events.config_sha256 != manifest.config_hashes.config_sha256 ||
      snapshots.config_sha256 != manifest.config_hashes.config_sha256 ||
      events.source_sha256 != manifest.source.sha256 ||
      snapshots.source_sha256 != manifest.source.sha256 || events.degraded != degraded ||
      snapshots.degraded != degraded) {
    return LocalError{ErrorCode::hash_mismatch,
                      "Binary identity headers disagree with the replay manifest."};
  }
  if (events.dictionary != snapshots.dictionary ||
      events.dictionary.size() != manifest.instruments.size()) {
    return LocalError{ErrorCode::invariant,
                      "Event, snapshot and manifest symbol dictionaries disagree."};
  }
  for (std::size_t index = 0; index < events.dictionary.size(); ++index) {
    if (events.dictionary[index] != manifest.instruments[index].dictionary) {
      return LocalError{ErrorCode::invariant,
                        "Binary dictionary entry disagrees with the replay manifest."};
    }
  }
  return std::nullopt;
}

[[nodiscard]] std::optional<LocalError>
verify_source_file(const std::filesystem::path& source_path, const HashedFile& expected,
                   const std::optional<ContentHash>& embedded_source_hash = std::nullopt) {
  const auto actual = hash_file(source_path, ErrorCode::input_path);
  if (!actual.valid()) {
    return LocalError{actual.error->code, actual.error->message};
  }
  if (*actual.file != expected ||
      (embedded_source_hash && actual.file->sha256 != *embedded_source_hash)) {
    return LocalError{ErrorCode::hash_mismatch, "Verified source bytes do not match the artefact.",
                      std::nullopt, content_hash_to_hex(expected.sha256),
                      content_hash_to_hex(actual.file->sha256)};
  }
  return std::nullopt;
}

[[nodiscard]] bool final_path_is_partial(const std::filesystem::path& path) {
  return path.extension() == ".partial" || path.filename().string().ends_with(".partial");
}

[[nodiscard]] bool regular_file_without_symlink(const std::filesystem::path& path) {
  std::error_code error;
  const auto status = std::filesystem::symlink_status(path, error);
  return !error && std::filesystem::is_regular_file(status);
}

[[nodiscard]] ArtefactValidationResult failure_with_deep_not_run(ArtefactValidationReport report,
                                                                 std::string check,
                                                                 const LocalError& error,
                                                                 const bool run_target) {
  auto result = fail(std::move(report), std::move(check), error);
  append_deep_not_run(result.report, run_target);
  return result;
}

[[nodiscard]] ArtefactValidationResult
validate_standalone(const std::filesystem::path& file_path, const bool deep,
                    const std::optional<std::filesystem::path>& source_path) {
  ArtefactValidationReport report{"file", file_path.filename().generic_string(), deep, {}, {}};
  if (file_path.empty() || final_path_is_partial(file_path)) {
    return failure_with_deep_not_run(
        std::move(report), "target",
        LocalError{ErrorCode::partial_artefact,
                   "A partial interchange pathname is not a completed validation target."},
        false);
  }
  std::error_code filesystem_error;
  if (!std::filesystem::is_regular_file(file_path, filesystem_error) || filesystem_error) {
    return failure_with_deep_not_run(
        std::move(report), "target",
        LocalError{ErrorCode::input_path,
                   "Validation target is not a readable regular interchange file."},
        false);
  }
  append_check(report, "target", ValidationCheckStatus::passed,
               "Standalone interchange target is a regular completed path.");

  const auto hashed = hash_file(file_path, ErrorCode::input_path);
  if (!hashed.valid()) {
    return failure_with_deep_not_run(std::move(report), "file_hash",
                                     LocalError{hashed.error->code, hashed.error->message}, false);
  }
  append_check(report, "file_hash", ValidationCheckStatus::passed,
               "Standalone artefact SHA-256 was computed.", std::nullopt,
               content_hash_to_hex(hashed.file->sha256));

  const auto binary = parse_binary_metadata(file_path, *hashed.file, std::nullopt);
  if (!binary.valid()) {
    return failure_with_deep_not_run(std::move(report), "header", *binary.error, false);
  }
  append_check(report, "header", ValidationCheckStatus::passed,
               "Interchange header, dictionary, declared count and file size are valid.");

  if (source_path) {
    const auto expected_source = hash_file(*source_path, ErrorCode::input_path);
    if (!expected_source.valid()) {
      return failure_with_deep_not_run(
          std::move(report), "source_hash",
          LocalError{expected_source.error->code, expected_source.error->message}, false);
    }
    if (expected_source.file->sha256 != binary.metadata->source_sha256) {
      return failure_with_deep_not_run(
          std::move(report), "source_hash",
          LocalError{ErrorCode::hash_mismatch,
                     "Verified source SHA-256 does not match the binary header.", std::nullopt,
                     content_hash_to_hex(binary.metadata->source_sha256),
                     content_hash_to_hex(expected_source.file->sha256)},
          false);
    }
    append_check(report, "source_hash", ValidationCheckStatus::passed,
                 "Optional source SHA-256 matches the binary header.",
                 content_hash_to_hex(binary.metadata->source_sha256),
                 content_hash_to_hex(expected_source.file->sha256));
  }

  ValidatedArtefact artefact{
      kind_name(binary.metadata->kind), file_path.filename().generic_string(), hashed.file->sha256,
      hashed.file->size_bytes,          binary.metadata->record_count,         0};
  if (deep) {
    const auto checked = binary.metadata->kind == InterchangeKind::events
                             ? deep_validate_events(file_path, *binary.metadata, nullptr, nullptr)
                             : deep_validate_snapshots(file_path, *binary.metadata);
    if (!checked.valid()) {
      return fail(std::move(report), "records", *checked.error);
    }
    artefact.records_examined = checked.records_examined;
    append_check(report, "records", ValidationCheckStatus::passed,
                 "All declared records passed deep streamed validation.", std::nullopt,
                 std::nullopt, checked.records_examined);
  }
  report.artefacts.push_back(std::move(artefact));
  return ArtefactValidationResult{std::move(report), std::nullopt};
}

[[nodiscard]] ArtefactValidationResult
validate_run(const std::filesystem::path& run_directory, const bool deep,
             const std::optional<std::filesystem::path>& source_path) {
  ArtefactValidationReport report{"run", run_directory.filename().generic_string(), deep, {}, {}};
  if (run_directory.empty() || final_path_is_partial(run_directory)) {
    return failure_with_deep_not_run(
        std::move(report), "target",
        LocalError{ErrorCode::partial_artefact,
                   "A partial replay directory is not a completed validation target."},
        true);
  }
  std::error_code filesystem_error;
  const auto run_status = std::filesystem::symlink_status(run_directory, filesystem_error);
  if (filesystem_error || !std::filesystem::is_directory(run_status)) {
    return failure_with_deep_not_run(
        std::move(report), "target",
        LocalError{ErrorCode::input_path, "Replay validation target is not a directory."}, true);
  }
  const auto manifest_path = run_directory / kManifestFilename;
  const auto event_path = run_directory / kEventFilename;
  const auto snapshot_path = run_directory / kSnapshotFilename;
  if (!regular_file_without_symlink(manifest_path) || !regular_file_without_symlink(event_path) ||
      !regular_file_without_symlink(snapshot_path)) {
    return failure_with_deep_not_run(
        std::move(report), "target",
        LocalError{ErrorCode::partial_artefact,
                   "Replay directory lacks regular final manifest or binary children."},
        true);
  }
  append_check(report, "target", ValidationCheckStatus::passed,
               "Replay directory contains regular final manifest and binary children.");

  const auto document = read_bounded_regular_file(manifest_path, kMaximumManifestBytes);
  if (!document) {
    return failure_with_deep_not_run(
        std::move(report), "manifest_schema",
        LocalError{ErrorCode::partial_artefact,
                   "Replay manifest is unreadable or exceeds the 1 MiB bound."},
        true);
  }
  LocalError manifest_error;
  const auto manifest = parse_manifest(*document, manifest_error);
  if (!manifest) {
    return failure_with_deep_not_run(std::move(report), "manifest_schema", manifest_error, true);
  }
  append_check(report, "manifest_schema", ValidationCheckStatus::passed,
               "Replay manifest is a strict completed version-1 document.");
  if (const auto error =
          validate_manifest_lineage(*manifest, run_directory.filename().generic_string())) {
    return failure_with_deep_not_run(std::move(report), "manifest_lineage", *error, true);
  }
  append_check(report, "manifest_lineage", ValidationCheckStatus::passed,
               "Manifest config, source, identity, counts and instrument lineage are consistent.");

  const auto event_hash = hash_file(event_path, ErrorCode::hash_mismatch);
  if (!event_hash.valid()) {
    return failure_with_deep_not_run(std::move(report), "events_hash",
                                     LocalError{event_hash.error->code, event_hash.error->message},
                                     true);
  }
  if (*event_hash.file != manifest->events.file) {
    return failure_with_deep_not_run(
        std::move(report), "events_hash",
        LocalError{ErrorCode::hash_mismatch,
                   "Event artefact size or SHA-256 does not match the replay manifest.",
                   std::nullopt, content_hash_to_hex(manifest->events.file.sha256),
                   content_hash_to_hex(event_hash.file->sha256)},
        true);
  }
  append_check(report, "events_hash", ValidationCheckStatus::passed,
               "Event artefact size and SHA-256 match the manifest.",
               content_hash_to_hex(manifest->events.file.sha256),
               content_hash_to_hex(event_hash.file->sha256));

  const auto snapshot_hash = hash_file(snapshot_path, ErrorCode::hash_mismatch);
  if (!snapshot_hash.valid()) {
    return failure_with_deep_not_run(
        std::move(report), "snapshots_hash",
        LocalError{snapshot_hash.error->code, snapshot_hash.error->message}, true);
  }
  if (*snapshot_hash.file != manifest->snapshots.file) {
    return failure_with_deep_not_run(
        std::move(report), "snapshots_hash",
        LocalError{ErrorCode::hash_mismatch,
                   "Snapshot artefact size or SHA-256 does not match the replay manifest.",
                   std::nullopt, content_hash_to_hex(manifest->snapshots.file.sha256),
                   content_hash_to_hex(snapshot_hash.file->sha256)},
        true);
  }
  append_check(report, "snapshots_hash", ValidationCheckStatus::passed,
               "Snapshot artefact size and SHA-256 match the manifest.",
               content_hash_to_hex(manifest->snapshots.file.sha256),
               content_hash_to_hex(snapshot_hash.file->sha256));

  if (source_path) {
    if (const auto error = verify_source_file(*source_path, manifest->source)) {
      return failure_with_deep_not_run(std::move(report), "source_hash", *error, true);
    }
    append_check(report, "source_hash", ValidationCheckStatus::passed,
                 "Optional source size and SHA-256 match the replay manifest.",
                 content_hash_to_hex(manifest->source.sha256),
                 content_hash_to_hex(manifest->source.sha256));
  }

  const auto event_binary =
      parse_binary_metadata(event_path, *event_hash.file, InterchangeKind::events);
  if (!event_binary.valid()) {
    return failure_with_deep_not_run(std::move(report), "events_header", *event_binary.error, true);
  }
  append_check(report, "events_header", ValidationCheckStatus::passed,
               "Event header, dictionary, record count and size are valid.");
  const auto snapshot_binary =
      parse_binary_metadata(snapshot_path, *snapshot_hash.file, InterchangeKind::snapshots);
  if (!snapshot_binary.valid()) {
    return failure_with_deep_not_run(std::move(report), "snapshots_header", *snapshot_binary.error,
                                     true);
  }
  append_check(report, "snapshots_header", ValidationCheckStatus::passed,
               "Snapshot header, dictionary, depth, record count and size are valid.");
  if (const auto error = validate_cross_file_metadata(*manifest, *event_binary.metadata,
                                                      *snapshot_binary.metadata)) {
    return failure_with_deep_not_run(std::move(report), "cross_file_identity", *error, true);
  }
  append_check(report, "cross_file_identity", ValidationCheckStatus::passed,
               "Manifest, event and snapshot identities and dictionaries agree.");

  ValidatedArtefact events{"events",
                           std::string{kEventFilename},
                           event_hash.file->sha256,
                           event_hash.file->size_bytes,
                           event_binary.metadata->record_count,
                           0};
  ValidatedArtefact snapshots{"snapshots",
                              std::string{kSnapshotFilename},
                              snapshot_hash.file->sha256,
                              snapshot_hash.file->size_bytes,
                              snapshot_binary.metadata->record_count,
                              0};
  if (deep) {
    const auto event_records = deep_validate_events(event_path, *event_binary.metadata,
                                                    &manifest->instruments, &manifest->config);
    if (!event_records.valid()) {
      const auto digest_failure =
          event_records.records_examined == event_binary.metadata->record_count;
      const auto check = digest_failure ? "final_book_digests" : "events_records";
      auto result = fail(std::move(report), check, *event_records.error);
      append_check(result.report, "snapshots_records", ValidationCheckStatus::not_run,
                   "Deep snapshot validation was not run because event validation failed.");
      return result;
    }
    events.records_examined = event_records.records_examined;
    append_check(report, "events_records", ValidationCheckStatus::passed,
                 "All event records passed deep streamed validation.", std::nullopt, std::nullopt,
                 event_records.records_examined);
    append_check(report, "final_book_digests", ValidationCheckStatus::passed,
                 "Reconstructed final order counts and book digests match the manifest.");

    const auto snapshot_records = deep_validate_snapshots(snapshot_path, *snapshot_binary.metadata);
    if (!snapshot_records.valid()) {
      return fail(std::move(report), "snapshots_records", *snapshot_records.error);
    }
    snapshots.records_examined = snapshot_records.records_examined;
    append_check(report, "snapshots_records", ValidationCheckStatus::passed,
                 "All snapshot records passed deep streamed validation.", std::nullopt,
                 std::nullopt, snapshot_records.records_examined);
  }
  report.artefacts.push_back(std::move(events));
  report.artefacts.push_back(std::move(snapshots));
  return ArtefactValidationResult{std::move(report), std::nullopt};
}

} // namespace

const char* validation_check_status_name(const ValidationCheckStatus status) noexcept {
  switch (status) {
  case ValidationCheckStatus::passed:
    return "pass";
  case ValidationCheckStatus::failed:
    return "fail";
  case ValidationCheckStatus::not_run:
    return "not_run";
  }
  return "not_run";
}

bool ArtefactValidationReport::passed() const noexcept {
  return std::ranges::none_of(checks, [](const ValidationCheck& check) {
    return check.status == ValidationCheckStatus::failed;
  });
}

ArtefactValidationResult
validate_replay_run(const std::filesystem::path& run_directory, const bool deep,
                    const std::optional<std::filesystem::path>& source_path) {
  return validate_run(run_directory, deep, source_path);
}

ArtefactValidationResult
validate_interchange_file(const std::filesystem::path& file_path, const bool deep,
                          const std::optional<std::filesystem::path>& source_path) {
  return validate_standalone(file_path, deep, source_path);
}

} // namespace itchlab
