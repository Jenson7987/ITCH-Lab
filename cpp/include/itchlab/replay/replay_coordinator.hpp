#pragma once

#include "itchlab/config/replay_config.hpp"
#include "itchlab/core/errors.hpp"
#include "itchlab/core/types.hpp"
#include "itchlab/input/byte_source.hpp"
#include "itchlab/output/diagnostic_sinks.hpp"

#include <cstdint>
#include <optional>
#include <string>

namespace itchlab {

struct ReplaySummary {
  std::uint64_t messages_processed{};
  std::uint64_t decoded_messages{};
  std::uint64_t selected_events{};
  std::uint64_t snapshots_written{};
  SymbolId symbol_id{};
  StockLocate stock_locate{};
  std::string symbol;
  std::size_t final_order_count{};
  ContentHash final_book_digest{};
  SourceProgress source_progress;
};

struct ReplayError {
  ErrorCode code{ErrorCode::internal};
  std::string message;
  std::optional<MessageIndex> message_index;
  std::optional<std::uint64_t> source_offset;
  std::optional<std::uint8_t> source_type;
  std::optional<OrderReference> order_reference;
};

struct ReplayResult {
  std::optional<ReplaySummary> summary;
  std::optional<ReplayError> error;

  [[nodiscard]] bool valid() const noexcept { return summary.has_value() && !error.has_value(); }
};

class ReplayCoordinator {
public:
  // The provisional diagnostic replay mutates only A/D messages for one strict symbol.
  [[nodiscard]] ReplayResult run(ByteSource& source, const ReplayConfig& config,
                                 DiagnosticSink& diagnostics) const;
};

} // namespace itchlab
