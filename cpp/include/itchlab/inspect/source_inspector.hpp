#pragma once

#include "itchlab/config/replay_config.hpp"
#include "itchlab/core/errors.hpp"
#include "itchlab/core/types.hpp"
#include "itchlab/input/byte_source.hpp"

#include <cstdint>
#include <map>
#include <optional>
#include <string>
#include <vector>

namespace itchlab {

struct InspectionOptions {
  std::optional<std::uint64_t> message_limit{1'000'000};
  std::vector<std::string> requested_symbols;
  ValidationMode mode{ValidationMode::strict};
};

struct InspectionSummary {
  std::uint64_t messages_examined{};
  std::map<std::string, std::uint64_t> counts_by_type;
  std::optional<TimestampNs> first_timestamp_ns;
  std::optional<TimestampNs> last_timestamp_ns;
  std::uint64_t stock_directory_count{};
  std::vector<std::string> requested_symbols_found;
  std::map<std::string, std::uint64_t> selected_counts_by_type;
  std::map<std::string, std::uint64_t> parse_errors_by_code;
  bool input_complete{};
  SourceProgress source_progress;
};

struct InspectionError {
  ErrorCode code{ErrorCode::internal};
  std::string message;
  std::optional<MessageIndex> message_index;
  std::optional<std::uint64_t> source_offset;
  std::optional<std::uint8_t> source_type;
};

struct InspectionResult {
  std::optional<InspectionSummary> summary;
  std::optional<InspectionError> error;

  [[nodiscard]] bool valid() const noexcept { return summary.has_value() && !error.has_value(); }
};

// Inspects framed messages without creating derived data. A bounded result does not claim EOF or
// gzip-trailer validation unless input_complete is true.
[[nodiscard]] InspectionResult inspect_source(ByteSource& source, const InspectionOptions& options);

} // namespace itchlab
