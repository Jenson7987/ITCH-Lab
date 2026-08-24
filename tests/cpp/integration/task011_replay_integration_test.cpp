#include "itchlab/config/replay_config.hpp"
#include "itchlab/core/sha256.hpp"
#include "itchlab/input/file_source.hpp"
#include "itchlab/input/gzip_source.hpp"
#include "itchlab/output/diagnostic_sinks.hpp"
#include "itchlab/replay/replay_coordinator.hpp"

#include <catch2/catch_test_macros.hpp>

#include <algorithm>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <optional>
#include <set>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

namespace {

constexpr itchlab::TimestampNs session_start_ns = 34'200'000'000'000;
constexpr itchlab::TimestampNs session_end_ns = 34'200'000'010'000;

std::filesystem::path repository_path(const std::string_view relative_path) {
  return std::filesystem::path{ITCHLAB_SOURCE_DIR} / relative_path;
}

std::string read_file(const std::filesystem::path& path) {
  std::ifstream stream{path, std::ios::binary};
  REQUIRE(stream.good());
  return {std::istreambuf_iterator<char>{stream}, std::istreambuf_iterator<char>{}};
}

itchlab::ReplayConfig session_config(const bool require_trading_state = true) {
  const auto parsed = itchlab::parse_replay_config(
      read_file(repository_path("configs/replay.diagnostic.example.json")));
  REQUIRE(parsed.valid());
  auto config = *parsed.config;
  config.selection.symbols = {"MSFT", "AAPL"};
  config.selection.session_start_ns = session_start_ns;
  config.selection.session_end_ns = session_end_ns;
  config.selection.require_trading_state = require_trading_state;
  config.output.emit_unchanged_trade_snapshots = true;
  return config;
}

class CollectingDiagnosticSink final : public itchlab::DiagnosticSink {
public:
  explicit CollectingDiagnosticSink(const bool require_intermediate_digest = true)
      : require_intermediate_digest_{require_intermediate_digest} {}

  [[nodiscard]] bool requires_intermediate_book_digest() const noexcept override {
    return require_intermediate_digest_;
  }

  std::optional<itchlab::DiagnosticWriteError>
  write_event(const itchlab::DiagnosticEvent& event) override {
    events.push_back(event);
    return std::nullopt;
  }

  std::optional<itchlab::DiagnosticWriteError>
  write_snapshot(const itchlab::DiagnosticSnapshot& snapshot) override {
    snapshots.push_back(snapshot);
    return std::nullopt;
  }

  std::vector<itchlab::DiagnosticEvent> events;
  std::vector<itchlab::DiagnosticSnapshot> snapshots;

private:
  bool require_intermediate_digest_;
};

struct ReplayTrace {
  itchlab::ReplaySummary summary;
  CollectingDiagnosticSink diagnostics;
};

ReplayTrace replay(itchlab::ByteSource& source, const itchlab::ReplayConfig& config,
                   const bool require_intermediate_digest = true) {
  CollectingDiagnosticSink diagnostics{require_intermediate_digest};
  const auto& event_sink = static_cast<const itchlab::EventSink&>(diagnostics);
  const auto& snapshot_sink = static_cast<const itchlab::SnapshotSink&>(diagnostics);
  REQUIRE(event_sink.requires_intermediate_book_digest() == require_intermediate_digest);
  REQUIRE(snapshot_sink.requires_intermediate_book_digest() == require_intermediate_digest);
  const itchlab::ReplayCoordinator coordinator;
  const auto result = coordinator.run(source, config, diagnostics);
  REQUIRE(result.valid());
  return ReplayTrace{*result.summary, std::move(diagnostics)};
}

const itchlab::ReplayInstrumentSummary& instrument_summary(const ReplayTrace& trace,
                                                           const std::string_view symbol) {
  const auto found = std::ranges::find_if(trace.summary.instruments, [&](const auto& instrument) {
    return instrument.instrument.symbol == symbol;
  });
  REQUIRE(found != trace.summary.instruments.end());
  return *found;
}

} // namespace

TEST_CASE("TASK-011 replay filters early, warms selected books and honours session state",
          "[TASK-011][integration][replay][session]") {
  auto plain = itchlab::open_file_source(repository_path("tests/fixtures/synthetic_session.itch"));
  auto gzip =
      itchlab::open_gzip_source(repository_path("tests/fixtures/synthetic_session.itch.gz"));
  REQUIRE(plain.valid());
  REQUIRE(gzip.valid());

  const auto config = session_config();
  const auto plain_trace = replay(*plain.source, config);
  const auto gzip_trace = replay(*gzip.source, config);

  const auto& summary = plain_trace.summary;
  REQUIRE(summary.messages_processed == 25);
  REQUIRE(summary.decoded_messages == 25);
  REQUIRE(summary.global_system_messages == 6);
  REQUIRE(summary.directory_messages == 3);
  REQUIRE(summary.selected_instrument_messages == 13);
  REQUIRE(summary.filtered_instrument_messages == 3);
  REQUIRE(summary.all_counts_by_type == std::map<std::string, std::uint64_t>{{"A", 4},
                                                                             {"D", 3},
                                                                             {"F", 1},
                                                                             {"H", 5},
                                                                             {"P", 1},
                                                                             {"Q", 1},
                                                                             {"R", 3},
                                                                             {"S", 6},
                                                                             {"X", 1}});
  REQUIRE(summary.selected_counts_by_type ==
          std::map<std::string, std::uint64_t>{
              {"A", 3}, {"D", 2}, {"F", 1}, {"H", 4}, {"P", 1}, {"Q", 1}, {"X", 1}});
  REQUIRE(summary.selected_events == 12);
  REQUIRE(summary.snapshots_written == 6);
  REQUIRE(summary.decoded_messages == summary.global_system_messages + summary.directory_messages +
                                          summary.selected_instrument_messages +
                                          summary.filtered_instrument_messages);
  REQUIRE(summary.global_session_events.size() == 6);
  REQUIRE(summary.global_session_events.front().event_code == 'O');
  REQUIRE(summary.global_session_events.back().event_code == 'C');

  REQUIRE(summary.instruments.size() == 2);
  REQUIRE(summary.instruments[0].instrument.symbol == "MSFT");
  REQUIRE(summary.instruments[0].instrument.symbol_id == 1);
  REQUIRE(summary.instruments[0].instrument.stock_locate == 2);
  REQUIRE(summary.instruments[1].instrument.symbol == "AAPL");
  REQUIRE(summary.instruments[1].instrument.symbol_id == 2);
  REQUIRE(summary.instruments[1].instrument.stock_locate == 1);

  const auto& msft = instrument_summary(plain_trace, "MSFT");
  const auto& aapl = instrument_summary(plain_trace, "AAPL");
  REQUIRE(msft.final_order_count == 1);
  REQUIRE(aapl.final_order_count == 2);
  REQUIRE(msft.final_trading_state == itchlab::TradingState::closed);
  REQUIRE(aapl.final_trading_state == itchlab::TradingState::closed);

  REQUIRE(plain_trace.diagnostics.events.size() == 12);
  REQUIRE(plain_trace.diagnostics.snapshots.size() == 6);
  REQUIRE(std::ranges::none_of(plain_trace.diagnostics.events,
                               [](const auto& event) { return event.symbol == "AMZN"; }));
  REQUIRE(std::ranges::none_of(plain_trace.diagnostics.snapshots,
                               [](const auto& snapshot) { return snapshot.symbol == "AMZN"; }));
  REQUIRE(std::ranges::none_of(plain_trace.diagnostics.snapshots, [](const auto& snapshot) {
    return snapshot.timestamp_ns < session_start_ns || snapshot.timestamp_ns >= session_end_ns;
  }));
  REQUIRE(std::ranges::none_of(plain_trace.diagnostics.events, [](const auto& event) {
    return event.timestamp_ns >= session_end_ns;
  }));

  const auto warm_aapl = std::ranges::find_if(
      plain_trace.diagnostics.events, [](const auto& event) { return event.message_index == 8; });
  REQUIRE(warm_aapl != plain_trace.diagnostics.events.end());
  REQUIRE_FALSE(warm_aapl->in_session);
  const auto session_start_add =
      std::ranges::find_if(plain_trace.diagnostics.snapshots,
                           [](const auto& snapshot) { return snapshot.message_index == 12; });
  REQUIRE(session_start_add != plain_trace.diagnostics.snapshots.end());
  REQUIRE(session_start_add->top_levels.bids[0]->price4 == 1'000'000);
  REQUIRE(session_start_add->top_levels.asks[0]->price4 == 1'001'000);

  const auto halt_snapshot =
      std::ranges::find_if(plain_trace.diagnostics.snapshots,
                           [](const auto& snapshot) { return snapshot.message_index == 14; });
  REQUIRE(halt_snapshot != plain_trace.diagnostics.snapshots.end());
  REQUIRE(halt_snapshot->trading_state == itchlab::TradingState::halted);
  const auto resume_snapshot =
      std::ranges::find_if(plain_trace.diagnostics.snapshots,
                           [](const auto& snapshot) { return snapshot.message_index == 18; });
  REQUIRE(resume_snapshot != plain_trace.diagnostics.snapshots.end());
  REQUIRE(resume_snapshot->trading_state == itchlab::TradingState::trading);
  REQUIRE(resume_snapshot->top_levels.bids[0]->price4 == 1'000'200);
  REQUIRE(resume_snapshot->last_trade_price4 == 1'000'100);
  REQUIRE(resume_snapshot->last_trade_quantity == 25);
  REQUIRE(std::ranges::none_of(plain_trace.diagnostics.snapshots, [](const auto& snapshot) {
    return snapshot.message_index == 15 || snapshot.message_index == 17;
  }));

  REQUIRE(gzip_trace.summary.messages_processed == summary.messages_processed);
  REQUIRE(gzip_trace.summary.selected_events == summary.selected_events);
  REQUIRE(gzip_trace.summary.snapshots_written == summary.snapshots_written);
  REQUIRE(gzip_trace.diagnostics.events.size() == plain_trace.diagnostics.events.size());
  REQUIRE(gzip_trace.diagnostics.snapshots.size() == plain_trace.diagnostics.snapshots.size());
  for (std::size_t index = 0; index < plain_trace.diagnostics.events.size(); ++index) {
    const auto& expected = plain_trace.diagnostics.events[index];
    const auto& actual = gzip_trace.diagnostics.events[index];
    REQUIRE(actual.message_index == expected.message_index);
    REQUIRE(actual.symbol_id == expected.symbol_id);
    REQUIRE(actual.event_kind == expected.event_kind);
    REQUIRE(itchlab::content_hash_to_hex(actual.book_digest) ==
            itchlab::content_hash_to_hex(expected.book_digest));
  }
}

TEST_CASE("TASK-011 trading-state gating suppresses only ordinary halt-time snapshots",
          "[TASK-011][integration][replay][trading-state]") {
  auto opened = itchlab::open_file_source(repository_path("tests/fixtures/synthetic_session.itch"));
  REQUIRE(opened.valid());

  const auto trace = replay(*opened.source, session_config(false));
  REQUIRE(trace.summary.snapshots_written == 8);
  REQUIRE(std::ranges::any_of(trace.diagnostics.snapshots, [](const auto& snapshot) {
    return snapshot.message_index == 15 && snapshot.event_kind == "add";
  }));
  REQUIRE(std::ranges::any_of(trace.diagnostics.snapshots, [](const auto& snapshot) {
    return snapshot.message_index == 17 && snapshot.event_kind == "trade";
  }));
}

TEST_CASE("TASK-031 sink capability skips only intermediate book digests",
          "[TASK-031][integration][replay][digest]") {
  auto diagnostic_source =
      itchlab::open_file_source(repository_path("tests/fixtures/synthetic_session.itch"));
  auto production_style_source =
      itchlab::open_file_source(repository_path("tests/fixtures/synthetic_session.itch"));
  REQUIRE(diagnostic_source.valid());
  REQUIRE(production_style_source.valid());

  const auto config = session_config();
  const auto diagnostic_trace = replay(*diagnostic_source.source, config);
  const auto production_style_trace = replay(*production_style_source.source, config, false);

  const auto empty_digest = itchlab::ContentHash{};
  REQUIRE(std::ranges::all_of(production_style_trace.diagnostics.events, [&](const auto& event) {
    return event.book_digest == empty_digest;
  }));
  REQUIRE(
      std::ranges::all_of(production_style_trace.diagnostics.snapshots, [&](const auto& snapshot) {
        return snapshot.book_digest == empty_digest;
      }));
  for (const auto& instrument : diagnostic_trace.summary.instruments) {
    const auto& production_instrument =
        instrument_summary(production_style_trace, instrument.instrument.symbol);
    REQUIRE(itchlab::content_hash_to_hex(production_instrument.final_book_digest) ==
            itchlab::content_hash_to_hex(instrument.final_book_digest));
  }
}

TEST_CASE("TASK-011 replay routes every supported selected-instrument message type",
          "[TASK-011][integration][replay][routes]") {
  auto opened = itchlab::open_file_source(repository_path("tests/fixtures/synthetic_mixed.itch"));
  REQUIRE(opened.valid());
  auto config = session_config(false);
  config.selection.symbols = {"AAPL", "MSFT", "AMZN"};
  config.selection.session_start_ns = 0;
  config.selection.session_end_ns = 86'400'000'000'000;

  const auto trace = replay(*opened.source, config);
  REQUIRE(trace.summary.messages_processed == 31);
  REQUIRE(trace.summary.global_system_messages == 6);
  REQUIRE(trace.summary.directory_messages == 3);
  REQUIRE(trace.summary.selected_instrument_messages == 22);
  REQUIRE(trace.summary.filtered_instrument_messages == 0);
  REQUIRE(trace.summary.selected_events == 22);
  REQUIRE(trace.summary.selected_counts_by_type == std::map<std::string, std::uint64_t>{{"A", 3},
                                                                                        {"B", 1},
                                                                                        {"C", 1},
                                                                                        {"D", 2},
                                                                                        {"E", 3},
                                                                                        {"F", 1},
                                                                                        {"H", 5},
                                                                                        {"P", 1},
                                                                                        {"Q", 1},
                                                                                        {"U", 2},
                                                                                        {"X", 2}});

  std::set<std::string> event_kinds;
  for (const auto& event : trace.diagnostics.events) {
    event_kinds.insert(event.event_kind);
  }
  REQUIRE(event_kinds == std::set<std::string>{"add", "broken_trade", "cancel", "cross", "delete",
                                               "execute", "execute_price", "replace", "trade",
                                               "trading_state"});

  const auto post_break_state =
      std::ranges::find_if(trace.diagnostics.snapshots,
                           [](const auto& snapshot) { return snapshot.message_index == 26; });
  REQUIRE(post_break_state != trace.diagnostics.snapshots.end());
  REQUIRE(post_break_state->last_trade_price4 == 1'000'500);
  REQUIRE(post_break_state->last_trade_quantity == 75);
}
