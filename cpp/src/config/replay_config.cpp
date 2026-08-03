#include "itchlab/config/replay_config.hpp"

#include "itchlab/core/sha256.hpp"

#include <nlohmann/json.hpp>

#include <algorithm>
#include <array>
#include <cctype>
#include <cstdint>
#include <exception>
#include <string>
#include <string_view>
#include <unordered_set>
#include <utility>
#include <vector>

namespace itchlab {
namespace {

using Json = nlohmann::json;

void add_issue(std::vector<ConfigIssue>& issues, const ErrorCode code, std::string pointer,
               std::string message) {
  issues.push_back(ConfigIssue{code, std::move(pointer), std::move(message)});
}

template <std::size_t AllowedSize, std::size_t RequiredSize>
bool validate_object(const Json& value, const std::string_view pointer,
                     const std::array<std::string_view, AllowedSize>& allowed,
                     const std::array<std::string_view, RequiredSize>& required,
                     std::vector<ConfigIssue>& issues) {
  if (!value.is_object()) {
    add_issue(issues, ErrorCode::config_schema, std::string{pointer}, "Expected an object.");
    return false;
  }

  for (auto iterator = value.begin(); iterator != value.end(); ++iterator) {
    if (std::find(allowed.begin(), allowed.end(), iterator.key()) == allowed.end()) {
      add_issue(issues, ErrorCode::config_schema, std::string{pointer} + '/' + iterator.key(),
                "Unknown configuration property.");
    }
  }
  for (const auto key : required) {
    if (!value.contains(key)) {
      add_issue(issues, ErrorCode::config_schema, std::string{pointer} + '/' + std::string{key},
                "Required configuration property is missing.");
    }
  }
  return true;
}

std::optional<std::uint64_t> unsigned_integer(const Json& object, const std::string_view key,
                                              const std::string_view pointer,
                                              std::vector<ConfigIssue>& issues) {
  if (!object.contains(key)) {
    return std::nullopt;
  }
  const auto& value = object.at(key);
  if (value.is_number_unsigned()) {
    return value.get<std::uint64_t>();
  }
  if (value.is_number_integer()) {
    const auto signed_value = value.get<std::int64_t>();
    if (signed_value >= 0) {
      return static_cast<std::uint64_t>(signed_value);
    }
  }
  add_issue(issues, ErrorCode::config_schema, std::string{pointer} + '/' + std::string{key},
            "Expected a non-negative integer.");
  return std::nullopt;
}

std::optional<std::string> string_value(const Json& object, const std::string_view key,
                                        const std::string_view pointer,
                                        std::vector<ConfigIssue>& issues) {
  if (!object.contains(key)) {
    return std::nullopt;
  }
  const auto& value = object.at(key);
  if (!value.is_string()) {
    add_issue(issues, ErrorCode::config_schema, std::string{pointer} + '/' + std::string{key},
              "Expected a string.");
    return std::nullopt;
  }
  return value.get<std::string>();
}

std::optional<bool> boolean_value(const Json& object, const std::string_view key,
                                  const std::string_view pointer,
                                  std::vector<ConfigIssue>& issues) {
  if (!object.contains(key)) {
    return std::nullopt;
  }
  const auto& value = object.at(key);
  if (!value.is_boolean()) {
    add_issue(issues, ErrorCode::config_schema, std::string{pointer} + '/' + std::string{key},
              "Expected a boolean.");
    return std::nullopt;
  }
  return value.get<bool>();
}

bool is_decimal_digit(const char character) {
  return std::isdigit(static_cast<unsigned char>(character)) != 0;
}

bool valid_trading_date(const std::string_view value) {
  if (value.size() != 10 || value[4] != '-' || value[7] != '-') {
    return false;
  }
  for (const auto index : {std::size_t{0}, std::size_t{1}, std::size_t{2}, std::size_t{3},
                           std::size_t{5}, std::size_t{6}, std::size_t{8}, std::size_t{9}}) {
    if (!is_decimal_digit(value[index])) {
      return false;
    }
  }

  const auto parse_two = [&value](const std::size_t offset) {
    return static_cast<unsigned>((value[offset] - '0') * 10 + (value[offset + 1] - '0'));
  };
  const auto year = static_cast<unsigned>((value[0] - '0') * 1000 + (value[1] - '0') * 100 +
                                          (value[2] - '0') * 10 + (value[3] - '0'));
  const auto month = parse_two(5);
  const auto day = parse_two(8);
  if (year == 0 || month == 0 || month > 12 || day == 0) {
    return false;
  }
  constexpr std::array<unsigned, 12> days_per_month{31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31};
  auto maximum_day = days_per_month[month - 1];
  const auto leap_year = (year % 4 == 0 && year % 100 != 0) || year % 400 == 0;
  if (month == 2 && leap_year) {
    maximum_day = 29;
  }
  return day <= maximum_day;
}

bool valid_symbol(const std::string_view value) {
  if (value.empty() || value.size() > 8 || value.front() == ' ' || value.back() == ' ') {
    return false;
  }
  return std::all_of(value.begin(), value.end(), [](const char character) {
    const auto byte = static_cast<unsigned char>(character);
    return byte >= 0x20U && byte <= 0x7eU;
  });
}

std::optional<Json> parse_json(const std::string_view document, std::vector<ConfigIssue>& issues) {
  bool duplicate_key = false;
  std::vector<std::unordered_set<std::string>> keys_by_depth;
  const auto callback = [&duplicate_key, &keys_by_depth](
                            const int depth, const Json::parse_event_t event, Json& parsed) {
    const auto index = static_cast<std::size_t>(std::max(depth, 0));
    if (event == Json::parse_event_t::object_start) {
      if (keys_by_depth.size() <= index) {
        keys_by_depth.resize(index + 1);
      }
      keys_by_depth[index].clear();
    } else if (event == Json::parse_event_t::key) {
      if (keys_by_depth.size() <= index) {
        keys_by_depth.resize(index + 1);
      }
      if (!keys_by_depth[index].insert(parsed.get<std::string>()).second) {
        duplicate_key = true;
      }
    } else if (event == Json::parse_event_t::object_end && keys_by_depth.size() > index) {
      keys_by_depth[index].clear();
    }
    return true;
  };

  try {
    auto parsed = Json::parse(document.begin(), document.end(), callback, true, false);
    if (duplicate_key) {
      add_issue(issues, ErrorCode::config_schema, "",
                "Duplicate object property names are invalid.");
      return std::nullopt;
    }
    return parsed;
  } catch (const std::exception&) {
    add_issue(issues, ErrorCode::config_schema, "", "Configuration is not valid JSON/I-JSON.");
    return std::nullopt;
  }
}

void sort_issues(std::vector<ConfigIssue>& issues) {
  std::sort(issues.begin(), issues.end(), [](const ConfigIssue& lhs, const ConfigIssue& rhs) {
    if (lhs.json_pointer != rhs.json_pointer) {
      return lhs.json_pointer < rhs.json_pointer;
    }
    if (lhs.code != rhs.code) {
      return lhs.code < rhs.code;
    }
    return lhs.message < rhs.message;
  });
}

} // namespace

ReplayConfigResult parse_replay_config(const std::string_view document) {
  ReplayConfigResult result;
  const auto parsed = parse_json(document, result.issues);
  if (!parsed) {
    return result;
  }

  constexpr std::array<std::string_view, 5> root_keys{"schema_version", "input", "selection",
                                                      "output", "validation"};
  if (!validate_object(*parsed, "", root_keys, root_keys, result.issues)) {
    sort_issues(result.issues);
    return result;
  }

  ReplayConfig config;
  if (const auto value = unsigned_integer(*parsed, "schema_version", "", result.issues)) {
    if (*value != 1) {
      add_issue(result.issues, ErrorCode::schema_version, "/schema_version",
                "Only replay config schema version 1 is supported.");
    } else {
      config.schema_version = 1;
    }
  }

  constexpr std::array<std::string_view, 4> input_keys{"path", "sha256", "trading_date",
                                                       "exchange_timezone"};
  if (parsed->contains("input") &&
      validate_object(parsed->at("input"), "/input", input_keys, input_keys, result.issues)) {
    const auto& input = parsed->at("input");
    if (const auto value = string_value(input, "path", "/input", result.issues)) {
      if (value->empty()) {
        add_issue(result.issues, ErrorCode::input_path, "/input/path", "Input path is empty.");
      } else {
        config.input.path = *value;
      }
    }
    if (input.contains("sha256")) {
      const auto& hash = input.at("sha256");
      if (hash.is_null()) {
        config.input.sha256 = std::nullopt;
      } else if (hash.is_string()) {
        config.input.sha256 = content_hash_from_hex(hash.get<std::string>());
        if (!config.input.sha256) {
          add_issue(result.issues, ErrorCode::config_schema, "/input/sha256",
                    "Expected null or a lowercase 64-character SHA-256 value.");
        }
      } else {
        add_issue(result.issues, ErrorCode::config_schema, "/input/sha256",
                  "Expected null or a lowercase 64-character SHA-256 value.");
      }
    }
    if (const auto value = string_value(input, "trading_date", "/input", result.issues)) {
      if (!valid_trading_date(*value)) {
        add_issue(result.issues, ErrorCode::trading_date, "/input/trading_date",
                  "Expected a valid ISO 8601 calendar date.");
      } else {
        config.input.trading_date = *value;
      }
    }
    if (const auto value = string_value(input, "exchange_timezone", "/input", result.issues)) {
      if (*value != "America/New_York") {
        add_issue(result.issues, ErrorCode::timezone, "/input/exchange_timezone",
                  "Nasdaq MVP timezone must be America/New_York.");
      } else {
        config.input.exchange_timezone = *value;
      }
    }
  }

  constexpr std::array<std::string_view, 4> selection_keys{
      "symbols", "session_start_ns", "session_end_ns", "require_trading_state"};
  if (parsed->contains("selection") &&
      validate_object(parsed->at("selection"), "/selection", selection_keys, selection_keys,
                      result.issues)) {
    const auto& selection = parsed->at("selection");
    if (selection.contains("symbols")) {
      const auto& symbols = selection.at("symbols");
      if (!symbols.is_array() || symbols.empty()) {
        add_issue(result.issues, ErrorCode::config_schema, "/selection/symbols",
                  "Expected a non-empty array of symbols.");
      } else {
        std::unordered_set<std::string> unique_symbols;
        for (std::size_t index = 0; index < symbols.size(); ++index) {
          const auto pointer = "/selection/symbols/" + std::to_string(index);
          if (!symbols[index].is_string()) {
            add_issue(result.issues, ErrorCode::config_schema, pointer,
                      "Expected a symbol string.");
            continue;
          }
          const auto symbol = symbols[index].get<std::string>();
          if (!valid_symbol(symbol)) {
            add_issue(result.issues, ErrorCode::config_schema, pointer,
                      "Symbol must contain 1 to 8 printable ASCII bytes without edge spaces.");
          } else if (!unique_symbols.insert(symbol).second) {
            add_issue(result.issues, ErrorCode::config_schema, pointer, "Duplicate symbol.");
          } else {
            config.selection.symbols.push_back(symbol);
          }
        }
      }
    }
    const auto start = unsigned_integer(selection, "session_start_ns", "/selection", result.issues);
    const auto end = unsigned_integer(selection, "session_end_ns", "/selection", result.issues);
    if (start && end) {
      if (*start >= *end || *start >= kNanosecondsPerDay || *end > kNanosecondsPerDay) {
        add_issue(result.issues, ErrorCode::session_window, "/selection",
                  "Session must be a non-empty half-open interval within one exchange day.");
      } else {
        config.selection.session_start_ns = *start;
        config.selection.session_end_ns = *end;
      }
    }
    if (const auto value =
            boolean_value(selection, "require_trading_state", "/selection", result.issues)) {
      config.selection.require_trading_state = *value;
    }
  }

  constexpr std::array<std::string_view, 2> output_keys{"depth", "emit_unchanged_trade_snapshots"};
  if (parsed->contains("output") &&
      validate_object(parsed->at("output"), "/output", output_keys, output_keys, result.issues)) {
    const auto& output = parsed->at("output");
    if (const auto value = unsigned_integer(output, "depth", "/output", result.issues)) {
      if (*value < 1 || *value > 50) {
        add_issue(result.issues, ErrorCode::depth, "/output/depth",
                  "Depth must be between 1 and 50.");
      } else {
        config.output.depth = static_cast<std::uint16_t>(*value);
      }
    }
    if (const auto value =
            boolean_value(output, "emit_unchanged_trade_snapshots", "/output", result.issues)) {
      config.output.emit_unchanged_trade_snapshots = *value;
    }
  }

  constexpr std::array<std::string_view, 3> validation_keys{"mode", "max_skipped_messages",
                                                            "invariant_interval"};
  if (parsed->contains("validation") &&
      validate_object(parsed->at("validation"), "/validation", validation_keys, validation_keys,
                      result.issues)) {
    const auto& validation = parsed->at("validation");
    if (const auto value = string_value(validation, "mode", "/validation", result.issues)) {
      if (*value == "strict") {
        config.validation.mode = ValidationMode::strict;
      } else if (*value == "permissive") {
        config.validation.mode = ValidationMode::permissive;
      } else {
        add_issue(result.issues, ErrorCode::config_schema, "/validation/mode",
                  "Mode must be strict or permissive.");
      }
    }
    if (const auto value =
            unsigned_integer(validation, "max_skipped_messages", "/validation", result.issues)) {
      if (*value > kMaxJsonInteger) {
        add_issue(result.issues, ErrorCode::config_schema, "/validation/max_skipped_messages",
                  "Integer exceeds the RFC 8785/I-JSON exact range.");
      } else {
        config.validation.max_skipped_messages = *value;
      }
    }
    if (const auto value =
            unsigned_integer(validation, "invariant_interval", "/validation", result.issues)) {
      if (*value == 0 || *value > kMaxJsonInteger) {
        add_issue(result.issues, ErrorCode::config_schema, "/validation/invariant_interval",
                  "Invariant interval must be a positive I-JSON integer.");
      } else {
        config.validation.invariant_interval = *value;
      }
    }
    if (config.validation.mode == ValidationMode::strict &&
        config.validation.max_skipped_messages != 0) {
      add_issue(result.issues, ErrorCode::config_schema, "/validation/max_skipped_messages",
                "Strict mode requires a zero skipped-message budget.");
    }
  }

  sort_issues(result.issues);
  if (result.issues.empty()) {
    result.config = std::move(config);
  }
  return result;
}

} // namespace itchlab
