#include "itchlab/config/replay_config.hpp"
#include "itchlab/core/errors.hpp"
#include "itchlab/core/sha256.hpp"
#include "itchlab/input/file_source.hpp"
#include "itchlab/input/gzip_source.hpp"
#include "itchlab/output/diagnostic_sinks.hpp"
#include "itchlab/replay/replay_coordinator.hpp"

#include <catch2/catch_test_macros.hpp>

#include <cstddef>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <optional>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

namespace {

std::filesystem::path repository_path(const std::string_view relative_path) {
  return std::filesystem::path{ITCHLAB_SOURCE_DIR} / relative_path;
}

std::string read_file(const std::filesystem::path& path) {
  std::ifstream stream{path, std::ios::binary};
  REQUIRE(stream.good());
  return {std::istreambuf_iterator<char>{stream}, std::istreambuf_iterator<char>{}};
}

itchlab::ReplayConfig diagnostic_config() {
  const auto parsed = itchlab::parse_replay_config(
      read_file(repository_path("configs/replay.diagnostic.example.json")));
  REQUIRE(parsed.valid());
  return *parsed.config;
}

class CollectingDiagnosticSink final : public itchlab::DiagnosticSink {
public:
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
};

struct ReplayTrace {
  itchlab::ReplaySummary summary;
  CollectingDiagnosticSink diagnostics;
};

ReplayTrace replay_minimal(itchlab::ByteSource& source) {
  CollectingDiagnosticSink diagnostics;
  const itchlab::ReplayCoordinator coordinator;
  const auto result = coordinator.run(source, diagnostic_config(), diagnostics);
  REQUIRE(result.valid());
  return ReplayTrace{*result.summary, std::move(diagnostics)};
}

} // namespace

TEST_CASE("TASK-007 replay resolves AAPL and emits deterministic S R A D diagnostics",
          "[TASK-007][integration][replay][golden]") {
  auto plain = itchlab::open_file_source(repository_path("tests/fixtures/synthetic_minimal.itch"));
  auto gzip =
      itchlab::open_gzip_source(repository_path("tests/fixtures/synthetic_minimal.itch.gz"));
  REQUIRE(plain.valid());
  REQUIRE(gzip.valid());

  const auto plain_trace = replay_minimal(*plain.source);
  const auto gzip_trace = replay_minimal(*gzip.source);
  REQUIRE(plain_trace.summary.messages_processed == 9);
  REQUIRE(plain_trace.summary.decoded_messages == 9);
  REQUIRE(plain_trace.summary.selected_events == 2);
  REQUIRE(plain_trace.summary.snapshots_written == 2);
  REQUIRE(plain_trace.summary.symbol == "AAPL");
  REQUIRE(plain_trace.summary.symbol_id == 1);
  REQUIRE(plain_trace.summary.stock_locate == 1);
  REQUIRE(plain_trace.summary.final_order_count == 0);
  REQUIRE(itchlab::content_hash_to_hex(plain_trace.summary.final_book_digest) ==
          "47213ce72b18bbb9fb839f064fb00c71d810d21c19e1fe74a9ed61162c0d2a6c");

  REQUIRE(plain_trace.diagnostics.events.size() == 2);
  REQUIRE(plain_trace.diagnostics.events[0].event_kind == "add");
  REQUIRE(plain_trace.diagnostics.events[0].message_index == 4);
  REQUIRE(plain_trace.diagnostics.events[0].remaining_quantity == 100);
  REQUIRE(plain_trace.diagnostics.events[1].event_kind == "delete");
  REQUIRE(plain_trace.diagnostics.events[1].message_index == 5);
  REQUIRE(plain_trace.diagnostics.events[1].remaining_quantity == 0);
  REQUIRE(plain_trace.diagnostics.snapshots.size() == 2);
  REQUIRE(plain_trace.diagnostics.snapshots[0].top_levels.bids[0]->price4 == 1'000'000);
  REQUIRE_FALSE(plain_trace.diagnostics.snapshots[1].top_levels.bids[0].has_value());

  REQUIRE(gzip_trace.diagnostics.events.size() == plain_trace.diagnostics.events.size());
  REQUIRE(gzip_trace.diagnostics.snapshots.size() == plain_trace.diagnostics.snapshots.size());
  for (std::size_t index = 0; index < plain_trace.diagnostics.events.size(); ++index) {
    REQUIRE(gzip_trace.diagnostics.events[index].message_index ==
            plain_trace.diagnostics.events[index].message_index);
    REQUIRE(itchlab::content_hash_to_hex(gzip_trace.diagnostics.events[index].book_digest) ==
            itchlab::content_hash_to_hex(plain_trace.diagnostics.events[index].book_digest));
  }
}

TEST_CASE("TASK-007 replay rejects an unresolved symbol before successful publication",
          "[TASK-007][integration][replay][symbol]") {
  auto opened = itchlab::open_file_source(repository_path("tests/fixtures/synthetic_minimal.itch"));
  REQUIRE(opened.valid());
  auto config = diagnostic_config();
  config.selection.symbols = {"MSFT"};
  CollectingDiagnosticSink diagnostics;
  const itchlab::ReplayCoordinator coordinator;

  const auto result = coordinator.run(*opened.source, config, diagnostics);
  REQUIRE_FALSE(result.valid());
  REQUIRE(result.error->code == itchlab::ErrorCode::unknown_symbol);
  REQUIRE(diagnostics.events.empty());
  REQUIRE(diagnostics.snapshots.empty());
}

TEST_CASE("TASK-007 replay translates a duplicate order into an atomic book-domain error",
          "[TASK-007][integration][replay][atomic]") {
  auto opened = itchlab::open_file_source(
      repository_path("tests/fixtures/invalid_lifecycle/synthetic_invalid_duplicate_add.itch"));
  REQUIRE(opened.valid());
  CollectingDiagnosticSink diagnostics;
  const itchlab::ReplayCoordinator coordinator;

  const auto result = coordinator.run(*opened.source, diagnostic_config(), diagnostics);
  REQUIRE_FALSE(result.valid());
  REQUIRE(result.error->code == itchlab::ErrorCode::order_reference);
  REQUIRE(diagnostics.events.size() == 1);
}
