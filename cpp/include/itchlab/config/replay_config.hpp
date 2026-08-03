#pragma once

#include "itchlab/core/errors.hpp"
#include "itchlab/core/types.hpp"

#include <cstdint>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

namespace itchlab {

enum class ValidationMode : std::uint8_t {
  strict,
  permissive,
};

struct ReplayInputConfig {
  std::string path;
  std::optional<ContentHash> sha256;
  std::string trading_date;
  std::string exchange_timezone;
};

struct ReplaySelectionConfig {
  std::vector<std::string> symbols;
  TimestampNs session_start_ns{};
  TimestampNs session_end_ns{};
  bool require_trading_state{};
};

struct ReplayOutputConfig {
  std::uint16_t depth{};
  bool emit_unchanged_trade_snapshots{};
};

struct ReplayValidationConfig {
  ValidationMode mode{ValidationMode::strict};
  std::uint64_t max_skipped_messages{};
  std::uint64_t invariant_interval{};
};

struct ReplayConfig {
  std::uint16_t schema_version{};
  ReplayInputConfig input;
  ReplaySelectionConfig selection;
  ReplayOutputConfig output;
  ReplayValidationConfig validation;
};

struct ConfigIssue {
  ErrorCode code{ErrorCode::config_schema};
  std::string json_pointer;
  std::string message;

  friend bool operator==(const ConfigIssue&, const ConfigIssue&) = default;
};

struct ReplayConfigResult {
  std::optional<ReplayConfig> config;
  std::vector<ConfigIssue> issues;

  [[nodiscard]] bool valid() const noexcept { return config.has_value(); }
};

// Parses untrusted JSON, rejects duplicate/unknown fields and returns issues ordered by pointer.
[[nodiscard]] ReplayConfigResult parse_replay_config(std::string_view document);

} // namespace itchlab
