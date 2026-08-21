#include "itchlab/core/errors.hpp"
#include "itchlab/output/event_writer.hpp"

#include <catch2/catch_test_macros.hpp>

#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
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
#include <string_view>
#include <system_error>
#include <utility>
#include <vector>

namespace {

struct OutputPlan {
  std::optional<std::size_t> fail_write_call;
  bool fail_seek{};
  bool fail_flush{};
  bool fail_close{};
};

struct OutputState {
  std::vector<std::byte> bytes;
  std::size_t position{};
  std::size_t write_calls{};
  bool flushed{};
  bool closed{};
};

class MemoryOutput final : public itchlab::EventWriterOutput {
public:
  MemoryOutput(std::shared_ptr<OutputState> state, OutputPlan plan = {})
      : state_{std::move(state)}, plan_{plan} {}

  bool write(const std::span<const std::byte> bytes) override {
    ++state_->write_calls;
    if (state_->closed ||
        (plan_.fail_write_call && state_->write_calls == *plan_.fail_write_call) ||
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
    if (state_->closed || plan_.fail_seek || !converted) {
      return false;
    }
    state_->position = *converted;
    return true;
  }

  bool flush() override {
    state_->flushed = true;
    return !state_->closed && !plan_.fail_flush;
  }

  bool close() override {
    state_->closed = true;
    return !plan_.fail_close;
  }

private:
  std::shared_ptr<OutputState> state_;
  OutputPlan plan_;
};

struct WriterFixture {
  std::shared_ptr<OutputState> state;
  itchlab::EventWriterOpenResult opened;
};

WriterFixture make_writer(const std::uint16_t symbol_count, const OutputPlan plan = {}) {
  auto state = std::make_shared<OutputState>();
  auto output = std::make_unique<MemoryOutput>(state, plan);
  return WriterFixture{state, itchlab::make_event_writer(std::move(output), symbol_count)};
}

itchlab::ContentHash sequential_hash(const std::uint8_t first) {
  itchlab::ContentHash result{};
  for (std::size_t index = 0; index < result.size(); ++index) {
    result[index] = static_cast<std::byte>(static_cast<std::uint8_t>(first + index));
  }
  return result;
}

itchlab::EventFileMetadata metadata(const std::size_t symbol_count = 2) {
  itchlab::EventFileMetadata result;
  result.trading_date = 20'190'130;
  result.degraded = true;
  result.config_sha256 = sequential_hash(1);
  result.source_sha256 = sequential_hash(33);
  result.instruments.push_back(itchlab::Instrument{1, 0x1234, "AAPL", 'Q', 'N', 100, false});
  if (symbol_count == 2) {
    result.instruments.push_back(itchlab::Instrument{2, 0xabcd, "MSFT.X", 'Q', 'N', 200, false});
  }
  return result;
}

itchlab::DiagnosticEvent event(const itchlab::MessageIndex message_index,
                               const itchlab::TimestampNs timestamp_ns, std::string event_kind,
                               const char source_type, const itchlab::SymbolId symbol_id) {
  itchlab::DiagnosticEvent result;
  result.message_index = message_index;
  result.timestamp_ns = timestamp_ns;
  result.symbol_id = symbol_id;
  result.event_kind = std::move(event_kind);
  result.source_type = source_type;
  return result;
}

std::vector<itchlab::DiagnosticEvent> golden_events() {
  std::vector<itchlab::DiagnosticEvent> events;

  auto add = event(5, 1'000'000, "add", 'F', 1);
  add.primary_reference = 0x0102030405060708ULL;
  add.side = itchlab::Side::buy;
  add.price4 = 1'652'300;
  add.quantity = 300;
  add.remaining_quantity = 300;
  add.aux_code = "MM01";
  events.push_back(add);

  auto execute = event(6, 1'000'010, "execute", 'E', 1);
  execute.primary_reference = 0x0102030405060708ULL;
  execute.secondary_reference = 0x1112131415161718ULL;
  execute.side = itchlab::Side::buy;
  execute.price4 = 1'652'300;
  execute.quantity = 100;
  execute.remaining_quantity = 200;
  execute.in_session = true;
  events.push_back(execute);

  auto execute_price = event(7, 1'000'020, "execute_price", 'C', 1);
  execute_price.primary_reference = 0x2122232425262728ULL;
  execute_price.secondary_reference = 0x3132333435363738ULL;
  execute_price.side = itchlab::Side::sell;
  execute_price.price4 = 1'652'400;
  execute_price.quantity = 50;
  execute_price.remaining_quantity = 100;
  execute_price.execution_price4 = 1'652'350;
  execute_price.in_session = true;
  events.push_back(execute_price);

  auto cancel = event(8, 1'000'030, "cancel", 'X', 2);
  cancel.primary_reference = 0x4142434445464748ULL;
  cancel.side = itchlab::Side::sell;
  cancel.price4 = 1'652'500;
  cancel.quantity = 50;
  cancel.remaining_quantity = 250;
  cancel.in_session = true;
  events.push_back(cancel);

  auto delete_order = event(9, 1'000'040, "delete", 'D', 2);
  delete_order.primary_reference = 0x4142434445464748ULL;
  delete_order.side = itchlab::Side::sell;
  delete_order.price4 = 1'652'500;
  delete_order.quantity = 250;
  delete_order.remaining_quantity = 0;
  delete_order.in_session = true;
  events.push_back(delete_order);

  auto replace = event(10, 1'000'050, "replace", 'U', 2);
  replace.primary_reference = 0x5152535455565758ULL;
  replace.secondary_reference = 0x6162636465666768ULL;
  replace.side = itchlab::Side::sell;
  replace.price4 = 1'652'600;
  replace.quantity = 400;
  replace.remaining_quantity = 400;
  replace.in_session = true;
  events.push_back(replace);

  auto trade = event(11, 1'000'060, "trade", 'P', 1);
  trade.primary_reference = 0;
  trade.secondary_reference = 0x7172737475767778ULL;
  trade.side = itchlab::Side::buy;
  trade.price4 = 1'652'700;
  trade.quantity = 75;
  trade.in_session = true;
  events.push_back(trade);

  auto cross = event(12, 1'000'070, "cross", 'Q', 2);
  cross.secondary_reference = 0x0101010102020202ULL;
  cross.price4 = 1'652'800;
  cross.quantity = 1'000;
  cross.event_subtype = 'O';
  cross.in_session = true;
  events.push_back(cross);

  auto broken = event(13, 1'000'080, "broken_trade", 'B', 1);
  broken.primary_reference = 0x7172737475767778ULL;
  broken.in_session = true;
  events.push_back(broken);

  auto state = event(14, 1'000'090, "trading_state", 'H', 1);
  state.aux_code = "NEWS";
  state.event_subtype = 'H';
  state.in_session = true;
  events.push_back(state);
  return events;
}

std::string read_file(const std::filesystem::path& path) {
  std::ifstream stream{path, std::ios::binary};
  REQUIRE(stream.good());
  return {std::istreambuf_iterator<char>{stream}, std::istreambuf_iterator<char>{}};
}

class TemporaryDirectory {
public:
  TemporaryDirectory() {
    static std::atomic<std::uint64_t> sequence{};
    const auto timestamp = std::chrono::steady_clock::now().time_since_epoch().count();
    path_ =
        std::filesystem::temp_directory_path() / ("itchlab-task013-" + std::to_string(timestamp) +
                                                  '-' + std::to_string(sequence.fetch_add(1)));
    std::error_code error;
    REQUIRE(std::filesystem::create_directory(path_, error));
    REQUIRE_FALSE(error);
  }

  TemporaryDirectory(const TemporaryDirectory&) = delete;
  TemporaryDirectory& operator=(const TemporaryDirectory&) = delete;

  ~TemporaryDirectory() {
    std::error_code ignored;
    std::filesystem::remove_all(path_, ignored);
  }

  [[nodiscard]] const std::filesystem::path& path() const noexcept { return path_; }

private:
  std::filesystem::path path_;
};

std::uint16_t little_u16(const std::vector<std::byte>& bytes, const std::size_t offset) {
  REQUIRE(offset + 2 <= bytes.size());
  return static_cast<std::uint16_t>(std::to_integer<std::uint8_t>(bytes[offset])) |
         static_cast<std::uint16_t>(
             static_cast<std::uint16_t>(std::to_integer<std::uint8_t>(bytes[offset + 1])) << 8U);
}

} // namespace

TEST_CASE("UT-OUT-001 TASK-013 event writer matches the independent v1 golden byte for byte",
          "[TASK-013][TASK-031][UT-OUT-001][golden][contract]") {
  static_assert(itchlab::kInterchangeHeaderSize == 104);
  static_assert(itchlab::kInterchangeSymbolEntrySize == 16);
  static_assert(itchlab::kEventRecordSize == 72);

  auto fixture = make_writer(2);
  REQUIRE(fixture.opened.valid());
  REQUIRE_FALSE(fixture.opened.writer->requires_intermediate_book_digest());
  for (const auto& item : golden_events()) {
    REQUIRE_FALSE(fixture.opened.writer->write_event(item).has_value());
  }
  REQUIRE(fixture.opened.writer->record_count() == 10);
  REQUIRE_FALSE(fixture.opened.writer->finalise(metadata()).has_value());
  REQUIRE(fixture.opened.writer->finalised());
  REQUIRE(fixture.state->flushed);
  REQUIRE(fixture.state->closed);

  const auto golden = read_file(std::filesystem::path{ITCHLAB_SOURCE_DIR} /
                                "tests/golden/interchange/synthetic_events_v1.ilb");
  REQUIRE(fixture.state->bytes.size() == golden.size());
  for (std::size_t index = 0; index < golden.size(); ++index) {
    REQUIRE(std::to_integer<unsigned char>(fixture.state->bytes[index]) ==
            static_cast<unsigned char>(golden[index]));
  }
}

TEST_CASE("TASK-013 event validity flags distinguish null from valid zero",
          "[TASK-013][UT-OUT-001][validity]") {
  auto fixture = make_writer(1);
  REQUIRE(fixture.opened.valid());

  const auto absent = event(1, 1, "broken_trade", 'B', 1);
  REQUIRE_FALSE(fixture.opened.writer->write_event(absent).has_value());

  auto zero = event(2, 2, "broken_trade", 'B', 1);
  zero.primary_reference = 0;
  zero.secondary_reference = 0;
  zero.side = itchlab::Side::buy;
  zero.price4 = 0;
  zero.quantity = 0;
  zero.remaining_quantity = 0;
  zero.execution_price4 = 0;
  zero.aux_code = "";
  zero.event_subtype = '\0';
  zero.in_session = true;
  REQUIRE_FALSE(fixture.opened.writer->write_event(zero).has_value());

  constexpr std::size_t prefix_size = 104 + 16;
  REQUIRE(little_u16(fixture.state->bytes, prefix_size + 57) == 0);
  REQUIRE(little_u16(fixture.state->bytes, prefix_size + 72 + 57) == 0x03ffU);
  REQUIRE(std::to_integer<unsigned char>(fixture.state->bytes[prefix_size + 72 + 60]) == ' ');
  REQUIRE(std::to_integer<unsigned char>(fixture.state->bytes[prefix_size + 72 + 64]) == 0);
  REQUIRE_FALSE(fixture.opened.writer->close_partial().has_value());
}

TEST_CASE("TASK-013 event writer rejects invalid records without advancing output state",
          "[TASK-013][UT-OUT-001][validation][atomic]") {
  auto fixture = make_writer(1);
  REQUIRE(fixture.opened.valid());

  const auto first = event(10, 10, "broken_trade", 'B', 1);
  REQUIRE_FALSE(fixture.opened.writer->write_event(first).has_value());
  const auto bytes_after_first = fixture.state->bytes;

  auto duplicate = first;
  duplicate.timestamp_ns = 11;
  const auto order_error = fixture.opened.writer->write_event(duplicate);
  REQUIRE(order_error->code == itchlab::ErrorCode::invariant);

  auto mismatched = event(11, 11, "trade", 'Q', 1);
  const auto kind_error = fixture.opened.writer->write_event(mismatched);
  REQUIRE(kind_error->code == itchlab::ErrorCode::invariant);

  auto decreasing_timestamp = event(11, 9, "broken_trade", 'B', 1);
  const auto timestamp_error = fixture.opened.writer->write_event(decreasing_timestamp);
  REQUIRE(timestamp_error->code == itchlab::ErrorCode::invariant);

  auto unknown_symbol = event(11, 11, "broken_trade", 'B', 2);
  const auto symbol_error = fixture.opened.writer->write_event(unknown_symbol);
  REQUIRE(symbol_error->code == itchlab::ErrorCode::invariant);

  auto too_large = event(11, 11, "broken_trade", 'B', 1);
  too_large.remaining_quantity =
      static_cast<itchlab::Shares>(std::numeric_limits<std::uint32_t>::max()) + 1ULL;
  const auto quantity_error = fixture.opened.writer->write_event(too_large);
  REQUIRE(quantity_error->code == itchlab::ErrorCode::quantity);

  auto invalid_side = event(11, 11, "broken_trade", 'B', 1);
  invalid_side.side = itchlab::Side::not_applicable;
  const auto side_error = fixture.opened.writer->write_event(invalid_side);
  REQUIRE(side_error->code == itchlab::ErrorCode::invariant);

  REQUIRE(fixture.opened.writer->record_count() == 1);
  REQUIRE(std::ranges::equal(fixture.state->bytes, bytes_after_first));
  REQUIRE_FALSE(fixture.opened.writer->close_partial().has_value());
}

TEST_CASE("TASK-013 writer failures remain partial and terminal",
          "[TASK-013][write-failure][partial]") {
  SECTION("placeholder write failure rejects creation") {
    auto fixture = make_writer(1, OutputPlan{1});
    REQUIRE_FALSE(fixture.opened.valid());
    REQUIRE(fixture.opened.error->code == itchlab::ErrorCode::disk_write);
  }

  SECTION("record failure does not advance the count") {
    auto fixture = make_writer(1, OutputPlan{2});
    REQUIRE(fixture.opened.valid());
    const auto write_error =
        fixture.opened.writer->write_event(event(1, 1, "broken_trade", 'B', 1));
    REQUIRE(write_error->code == itchlab::ErrorCode::disk_write);
    REQUIRE(fixture.opened.writer->record_count() == 0);
    REQUIRE(fixture.opened.writer->write_event(event(2, 2, "broken_trade", 'B', 1))->code ==
            itchlab::ErrorCode::disk_write);
  }

  SECTION("header seek failure is terminal") {
    auto fixture = make_writer(1, OutputPlan{std::nullopt, true});
    REQUIRE(fixture.opened.valid());
    REQUIRE(fixture.opened.writer->finalise(metadata(1))->code == itchlab::ErrorCode::disk_write);
  }

  SECTION("header patch failure is terminal") {
    auto fixture = make_writer(1, OutputPlan{2});
    REQUIRE(fixture.opened.valid());
    REQUIRE(fixture.opened.writer->finalise(metadata(1))->code == itchlab::ErrorCode::disk_write);
  }

  SECTION("final flush failure is terminal") {
    auto fixture = make_writer(1, OutputPlan{std::nullopt, false, true});
    REQUIRE(fixture.opened.valid());
    REQUIRE(fixture.opened.writer->finalise(metadata(1))->code == itchlab::ErrorCode::disk_write);
  }

  SECTION("final close failure is terminal") {
    auto fixture = make_writer(1, OutputPlan{std::nullopt, false, false, true});
    REQUIRE(fixture.opened.valid());
    REQUIRE(fixture.opened.writer->finalise(metadata(1))->code == itchlab::ErrorCode::disk_write);
    REQUIRE_FALSE(fixture.opened.writer->finalised());
  }
}

TEST_CASE("TASK-013 filesystem writer finalises a valid empty partial without publishing",
          "[TASK-013][UT-OUT-001][filesystem][partial]") {
  TemporaryDirectory temporary;
  const auto final_path = temporary.path() / "events.ilb";
  const auto partial_path = temporary.path() / "events.ilb.partial";

  auto opened = itchlab::open_event_writer(final_path, 1);
  REQUIRE(opened.valid());
  REQUIRE(std::filesystem::exists(partial_path));
  REQUIRE_FALSE(std::filesystem::exists(final_path));
  REQUIRE_FALSE(opened.writer->finalise(metadata(1)).has_value());
  REQUIRE(opened.writer->record_count() == 0);
  REQUIRE(std::filesystem::file_size(partial_path) == 104 + 16);
  REQUIRE_FALSE(std::filesystem::exists(final_path));

  const auto conflict = itchlab::open_event_writer(final_path, 1);
  REQUIRE_FALSE(conflict.valid());
  REQUIRE(conflict.error->code == itchlab::ErrorCode::output_path);
}
