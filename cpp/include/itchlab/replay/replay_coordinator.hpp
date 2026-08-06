#pragma once

#include "itchlab/config/replay_config.hpp"
#include "itchlab/core/cancellation.hpp"
#include "itchlab/core/errors.hpp"
#include "itchlab/core/types.hpp"
#include "itchlab/input/byte_source.hpp"
#include "itchlab/output/diagnostic_sinks.hpp"
#include "itchlab/replay/instrument_directory.hpp"
#include "itchlab/replay/progress_reporter.hpp"
#include "itchlab/replay/session_state.hpp"

#include <cstdint>
#include <map>
#include <optional>
#include <string>
#include <vector>

namespace itchlab {

struct ReplayInstrumentSummary {
  Instrument instrument;
  std::size_t final_order_count{};
  ContentHash final_book_digest{};
  TradingState final_trading_state{TradingState::unknown};
};

struct ReplaySummary {
  std::uint64_t messages_processed{};
  std::uint64_t decoded_messages{};
  std::uint64_t global_system_messages{};
  std::uint64_t directory_messages{};
  std::uint64_t selected_instrument_messages{};
  std::uint64_t filtered_instrument_messages{};
  std::uint64_t selected_events{};
  std::uint64_t snapshots_written{};
  std::uint64_t errors_observed{};
  std::uint64_t skipped_messages{};
  bool degraded{};
  std::map<std::string, std::uint64_t> all_counts_by_type;
  std::map<std::string, std::uint64_t> selected_counts_by_type;
  std::map<std::string, std::uint64_t> error_counts_by_code;
  std::vector<GlobalSessionEvent> global_session_events;
  std::vector<ReplayInstrumentSummary> instruments;
  SourceProgress source_progress;
};

struct ReplayRuntimeContext {
  std::uint64_t messages_processed{};
  std::uint64_t selected_events{};
  std::uint64_t error_count{};
  SourceProgress source_progress;
};

struct ReplayError {
  ErrorCode code{ErrorCode::internal};
  std::string message;
  std::optional<MessageIndex> message_index;
  std::optional<std::uint64_t> source_offset;
  std::optional<std::uint8_t> source_type;
  std::optional<OrderReference> order_reference;
  std::optional<ReplayRuntimeContext> runtime;
};

struct ReplayResult {
  std::optional<ReplaySummary> summary;
  std::optional<ReplayError> error;

  [[nodiscard]] bool valid() const noexcept { return summary.has_value() && !error.has_value(); }
};

class ReplayCoordinator {
public:
  // Replays selected symbols into independent event and snapshot sinks. Publication remains the
  // command coordinator's responsibility.
  [[nodiscard]] ReplayResult run(ByteSource& source, const ReplayConfig& config, EventSink& events,
                                 SnapshotSink& snapshots, CancellationToken cancellation = {},
                                 ProgressReporter* progress = nullptr) const;

  // Compatibility adapter for the provisional combined JSONL diagnostic sink.
  [[nodiscard]] ReplayResult run(ByteSource& source, const ReplayConfig& config,
                                 DiagnosticSink& diagnostics, CancellationToken cancellation = {},
                                 ProgressReporter* progress = nullptr) const {
    return run(source, config, diagnostics, diagnostics, cancellation, progress);
  }
};

} // namespace itchlab
