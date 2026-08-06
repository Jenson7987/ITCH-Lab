#include "itchlab/config/canonical_json.hpp"
#include "itchlab/config/replay_config.hpp"
#include "itchlab/core/sha256.hpp"
#include "itchlab/input/file_source.hpp"
#include "itchlab/output/event_writer.hpp"
#include "itchlab/replay/replay_coordinator.hpp"

#include <catch2/catch_test_macros.hpp>

#include <array>
#include <atomic>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <optional>
#include <span>
#include <string>
#include <string_view>
#include <system_error>
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

class TemporaryDirectory {
public:
  TemporaryDirectory() {
    static std::atomic<std::uint64_t> sequence{};
    const auto timestamp = std::chrono::steady_clock::now().time_since_epoch().count();
    path_ = std::filesystem::temp_directory_path() /
            ("itchlab-task013-integration-" + std::to_string(timestamp) + '-' +
             std::to_string(sequence.fetch_add(1)));
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

class CountingSnapshotSink final : public itchlab::SnapshotSink {
public:
  std::optional<itchlab::DiagnosticWriteError>
  write_snapshot(const itchlab::DiagnosticSnapshot&) override {
    ++count;
    return std::nullopt;
  }

  std::uint64_t count{};
};

itchlab::ReplayConfig mixed_config() {
  const auto parsed = itchlab::parse_replay_config(
      read_file(repository_path("configs/replay.diagnostic.example.json")));
  REQUIRE(parsed.valid());
  auto config = *parsed.config;
  config.selection.symbols = {"AAPL", "MSFT", "AMZN"};
  config.selection.session_start_ns = 0;
  config.selection.session_end_ns = itchlab::kNanosecondsPerDay;
  config.selection.require_trading_state = false;
  config.output.emit_unchanged_trade_snapshots = true;
  return config;
}

std::uint16_t little_u16(const std::span<const std::byte> bytes, const std::size_t offset) {
  REQUIRE(offset + 2 <= bytes.size());
  return static_cast<std::uint16_t>(std::to_integer<std::uint8_t>(bytes[offset])) |
         static_cast<std::uint16_t>(
             static_cast<std::uint16_t>(std::to_integer<std::uint8_t>(bytes[offset + 1])) << 8U);
}

std::uint64_t little_u64(const std::span<const std::byte> bytes, const std::size_t offset) {
  REQUIRE(offset + 8 <= bytes.size());
  std::uint64_t value{};
  for (std::size_t index = 0; index < 8; ++index) {
    const auto shift = static_cast<unsigned>(index * 8U);
    value |= static_cast<std::uint64_t>(std::to_integer<std::uint8_t>(bytes[offset + index]))
             << shift;
  }
  return value;
}

} // namespace

TEST_CASE("TASK-013 mixed replay streams every normalised kind into source-ordered event-v1 bytes",
          "[TASK-013][FR-007][integration][ordering]") {
  TemporaryDirectory temporary;
  const auto source_path = repository_path("tests/fixtures/synthetic_mixed.itch");
  auto source = itchlab::open_file_source(source_path);
  REQUIRE(source.valid());
  const auto config = mixed_config();
  auto opened = itchlab::open_event_writer(temporary.path() / "events.ilb", 3);
  REQUIRE(opened.valid());
  CountingSnapshotSink snapshots;

  const itchlab::ReplayCoordinator coordinator;
  const auto replayed = coordinator.run(*source.source, config, *opened.writer, snapshots);
  REQUIRE(replayed.valid());
  REQUIRE(replayed.summary->selected_events == 22);
  REQUIRE(opened.writer->record_count() == replayed.summary->selected_events);
  REQUIRE(snapshots.count == replayed.summary->snapshots_written);

  std::vector<itchlab::Instrument> instruments;
  for (const auto& summary : replayed.summary->instruments) {
    instruments.push_back(summary.instrument);
  }
  const auto source_bytes = read_file(source_path);
  const itchlab::EventFileMetadata metadata{
      20'190'130,
      std::move(instruments),
      replayed.summary->degraded,
      itchlab::replay_config_hashes(config).config_sha256,
      itchlab::sha256(source_bytes),
  };
  REQUIRE_FALSE(opened.writer->finalise(metadata).has_value());

  const auto partial_path = temporary.path() / "events.ilb.partial";
  REQUIRE(std::filesystem::exists(partial_path));
  REQUIRE_FALSE(std::filesystem::exists(temporary.path() / "events.ilb"));
  const auto encoded_text = read_file(partial_path);
  const auto encoded = std::as_bytes(std::span{encoded_text.data(), encoded_text.size()});
  REQUIRE(encoded.size() == 104 + 3 * 16 + 22 * 72);
  REQUIRE(little_u16(encoded, 8) == 1);
  REQUIRE(little_u16(encoded, 10) == 104);
  REQUIRE(little_u16(encoded, 12) == 72);
  REQUIRE(little_u16(encoded, 24) == 3);
  REQUIRE(little_u16(encoded, 26) == 0);
  REQUIRE(little_u64(encoded, 28) == 22);

  constexpr std::array<itchlab::MessageIndex, 22> expected_indices{
      5, 6, 7, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27,
  };
  constexpr std::size_t records_offset = 104 + 3 * 16;
  for (std::size_t index = 0; index < expected_indices.size(); ++index) {
    REQUIRE(little_u64(encoded, records_offset + index * 72) == expected_indices[index]);
  }
}
