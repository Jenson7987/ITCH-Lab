#include "itchlab/core/errors.hpp"
#include "itchlab/output/snapshot_writer.hpp"

#include <catch2/catch_test_macros.hpp>

#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <limits>
#include <memory>
#include <optional>
#include <span>
#include <string>
#include <utility>
#include <vector>

namespace {

struct OutputState {
  std::vector<std::byte> bytes;
  std::size_t position{};
  std::optional<std::size_t> fail_write_call;
  std::size_t write_calls{};
  bool flushed{};
  bool closed{};
};

class MemoryOutput final : public itchlab::SnapshotWriterOutput {
public:
  explicit MemoryOutput(std::shared_ptr<OutputState> state) : state_{std::move(state)} {}

  bool write(const std::span<const std::byte> bytes) override {
    ++state_->write_calls;
    if (state_->closed ||
        (state_->fail_write_call && state_->write_calls == *state_->fail_write_call) ||
        bytes.size() > std::numeric_limits<std::size_t>::max() - state_->position) {
      return false;
    }
    const auto end = state_->position + bytes.size();
    if (state_->bytes.size() < end) {
      state_->bytes.resize(end);
    }
    std::ranges::copy(bytes, state_->bytes.begin() + static_cast<std::ptrdiff_t>(state_->position));
    state_->position = end;
    return true;
  }

  bool seek(const std::uint64_t offset) override {
    const auto converted = itchlab::checked_integral_cast<std::size_t>(offset);
    if (state_->closed || !converted) {
      return false;
    }
    state_->position = *converted;
    return true;
  }

  bool flush() override {
    state_->flushed = true;
    return !state_->closed;
  }

  bool close() override {
    state_->closed = true;
    return true;
  }

private:
  std::shared_ptr<OutputState> state_;
};

struct WriterFixture {
  std::shared_ptr<OutputState> state;
  itchlab::SnapshotWriterOpenResult opened;
};

WriterFixture make_writer(const std::uint16_t symbol_count = 2, const std::uint16_t depth = 2) {
  auto state = std::make_shared<OutputState>();
  auto output = std::make_unique<MemoryOutput>(state);
  return WriterFixture{state,
                       itchlab::make_snapshot_writer(std::move(output), symbol_count, depth)};
}

itchlab::ContentHash sequential_hash(const std::uint8_t first) {
  itchlab::ContentHash result{};
  for (std::size_t index = 0; index < result.size(); ++index) {
    result[index] = static_cast<std::byte>(static_cast<std::uint8_t>(first + index));
  }
  return result;
}

itchlab::EventFileMetadata metadata() {
  return itchlab::EventFileMetadata{
      20'190'130,
      {itchlab::Instrument{1, 0x1234, "AAPL", 'Q', 'N', 100, false},
       itchlab::Instrument{2, 0xabcd, "MSFT.X", 'Q', 'N', 200, false}},
      true,
      sequential_hash(1),
      sequential_hash(33),
  };
}

itchlab::DiagnosticSnapshot first_snapshot() {
  itchlab::DiagnosticSnapshot snapshot;
  snapshot.message_index = 5;
  snapshot.timestamp_ns = 1'000'000;
  snapshot.symbol_id = 1;
  snapshot.stock_locate = 0x1234;
  snapshot.symbol = "AAPL";
  snapshot.event_kind = "add";
  snapshot.depth = 2;
  snapshot.top_n_changed = true;
  snapshot.event_price4 = 1'652'300;
  snapshot.event_quantity = 300;
  snapshot.last_trade_price4 = 1'652'350;
  snapshot.last_trade_quantity = 75;
  snapshot.trading_state = itchlab::TradingState::trading;
  snapshot.top_levels.bids = {itchlab::AggregatedLevel{1'652'300, 300},
                              itchlab::AggregatedLevel{1'652'200, 500}};
  snapshot.top_levels.asks = {itchlab::AggregatedLevel{1'652'400, 200}, std::nullopt};
  return snapshot;
}

itchlab::DiagnosticSnapshot second_snapshot() {
  itchlab::DiagnosticSnapshot snapshot;
  snapshot.message_index = 14;
  snapshot.timestamp_ns = 1'000'090;
  snapshot.symbol_id = 2;
  snapshot.stock_locate = 0xabcd;
  snapshot.symbol = "MSFT.X";
  snapshot.event_kind = "trading_state";
  snapshot.depth = 2;
  snapshot.trading_state = itchlab::TradingState::halted;
  snapshot.top_levels.bids = {std::nullopt, std::nullopt};
  snapshot.top_levels.asks = {std::nullopt, std::nullopt};
  return snapshot;
}

std::string read_file(const std::filesystem::path& path) {
  std::ifstream stream{path, std::ios::binary};
  REQUIRE(stream.good());
  return {std::istreambuf_iterator<char>{stream}, std::istreambuf_iterator<char>{}};
}

} // namespace

TEST_CASE("UT-OUT-002 TASK-014 snapshot writer matches the independent v1 golden byte for byte",
          "[TASK-014][TASK-031][UT-OUT-002][golden][contract]") {
  static_assert(itchlab::kSnapshotFixedRecordSize == 48);
  static_assert(itchlab::kSnapshotDepthEntrySize == 28);
  REQUIRE(itchlab::snapshot_record_size(2) == 104);

  auto fixture = make_writer();
  REQUIRE(fixture.opened.valid());
  REQUIRE_FALSE(fixture.opened.writer->requires_intermediate_book_digest());
  REQUIRE_FALSE(fixture.opened.writer->write_snapshot(first_snapshot()).has_value());
  REQUIRE_FALSE(fixture.opened.writer->write_snapshot(second_snapshot()).has_value());
  REQUIRE_FALSE(fixture.opened.writer->finalise(metadata()).has_value());
  REQUIRE(fixture.opened.writer->record_count() == 2);
  REQUIRE(fixture.state->flushed);
  REQUIRE(fixture.state->closed);

  const auto golden = read_file(std::filesystem::path{ITCHLAB_SOURCE_DIR} /
                                "tests/golden/interchange/synthetic_snapshots_v1.ilb");
  REQUIRE(fixture.state->bytes.size() == golden.size());
  for (std::size_t index = 0; index < golden.size(); ++index) {
    REQUIRE(std::to_integer<unsigned char>(fixture.state->bytes[index]) ==
            static_cast<unsigned char>(golden[index]));
  }

  constexpr std::size_t records_offset = 104 + 2 * 16;
  REQUIRE(std::to_integer<std::uint8_t>(fixture.state->bytes[records_offset + 19]) == 87);
  REQUIRE(std::to_integer<std::uint8_t>(fixture.state->bytes[records_offset + 48]) == 1);
  REQUIRE(std::to_integer<std::uint8_t>(fixture.state->bytes[records_offset + 49]) == 1);
  REQUIRE(std::to_integer<std::uint8_t>(fixture.state->bytes[records_offset + 104 + 19]) == 24);
}

TEST_CASE("TASK-014 snapshot record sizing is exactly 48 plus 28 times depth",
          "[TASK-014][UT-OUT-002][depth]") {
  REQUIRE_FALSE(itchlab::snapshot_record_size(0).has_value());
  REQUIRE(itchlab::snapshot_record_size(1) == 76);
  REQUIRE(itchlab::snapshot_record_size(2) == 104);
  REQUIRE(itchlab::snapshot_record_size(50) == 1'448);
  REQUIRE_FALSE(itchlab::snapshot_record_size(51).has_value());
}

TEST_CASE("TASK-014 snapshot validation rejects malformed records atomically",
          "[TASK-014][UT-OUT-002][validation][atomic]") {
  auto fixture = make_writer();
  REQUIRE(fixture.opened.valid());
  REQUIRE_FALSE(fixture.opened.writer->write_snapshot(first_snapshot()).has_value());
  const auto expected_bytes = fixture.state->bytes;

  auto invalid_pair = second_snapshot();
  invalid_pair.last_trade_price4 = 1;
  REQUIRE(fixture.opened.writer->write_snapshot(invalid_pair)->code ==
          itchlab::ErrorCode::invariant);

  auto invalid_depth = second_snapshot();
  invalid_depth.depth = 1;
  REQUIRE(fixture.opened.writer->write_snapshot(invalid_depth)->code ==
          itchlab::ErrorCode::invariant);

  auto invalid_order = second_snapshot();
  invalid_order.top_levels.bids = {itchlab::AggregatedLevel{100, 1},
                                   itchlab::AggregatedLevel{101, 1}};
  REQUIRE(fixture.opened.writer->write_snapshot(invalid_order)->code ==
          itchlab::ErrorCode::invariant);

  REQUIRE(fixture.opened.writer->record_count() == 1);
  REQUIRE(std::ranges::equal(fixture.state->bytes, expected_bytes));
  REQUIRE_FALSE(fixture.opened.writer->close_partial().has_value());
}

TEST_CASE("TASK-014 snapshot output failures remain terminal and partial",
          "[TASK-014][UT-OUT-002][failure][partial]") {
  auto fixture = make_writer();
  REQUIRE(fixture.opened.valid());
  fixture.state->fail_write_call = 2;
  REQUIRE(fixture.opened.writer->write_snapshot(first_snapshot())->code ==
          itchlab::ErrorCode::disk_write);
  REQUIRE(fixture.opened.writer->record_count() == 0);
  REQUIRE(fixture.opened.writer->write_snapshot(second_snapshot())->code ==
          itchlab::ErrorCode::disk_write);
}
