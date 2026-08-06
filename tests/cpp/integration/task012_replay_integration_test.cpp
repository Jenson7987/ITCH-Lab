#include "itchlab/cli.hpp"
#include "itchlab/config/replay_config.hpp"
#include "itchlab/core/cancellation.hpp"
#include "itchlab/input/byte_source.hpp"
#include "itchlab/output/diagnostic_sinks.hpp"
#include "itchlab/replay/replay_coordinator.hpp"

#include <catch2/catch_test_macros.hpp>
#include <nlohmann/json.hpp>

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <optional>
#include <sstream>
#include <string>
#include <string_view>
#include <system_error>
#include <vector>

namespace {

using Json = nlohmann::json;

std::filesystem::path repository_path(const std::string_view relative_path) {
  return std::filesystem::path{ITCHLAB_SOURCE_DIR} / relative_path;
}

std::string read_file(const std::filesystem::path& path) {
  std::ifstream stream{path, std::ios::binary};
  REQUIRE(stream.good());
  return {std::istreambuf_iterator<char>{stream}, std::istreambuf_iterator<char>{}};
}

void write_file(const std::filesystem::path& path, const std::string_view bytes) {
  std::ofstream stream{path, std::ios::binary};
  REQUIRE(stream.good());
  stream.write(bytes.data(), static_cast<std::streamsize>(bytes.size()));
  stream.close();
  REQUIRE_FALSE(stream.fail());
}

class TemporaryDirectory {
public:
  TemporaryDirectory() {
    static std::atomic<std::uint64_t> sequence{};
    const auto timestamp = std::chrono::steady_clock::now().time_since_epoch().count();
    path_ =
        std::filesystem::temp_directory_path() / ("itchlab-task012-" + std::to_string(timestamp) +
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

struct CommandResult {
  int exit_code{};
  std::string output;
  std::string error;
};

CommandResult run_command(const std::vector<std::string>& owned_arguments,
                          const itchlab::cli::RuntimeContext runtime = {}) {
  std::vector<std::string_view> arguments;
  arguments.reserve(owned_arguments.size());
  for (const auto& argument : owned_arguments) {
    arguments.push_back(argument);
  }
  std::ostringstream output;
  std::ostringstream error;
  const auto code = itchlab::cli::run(arguments, output, error, runtime);
  return CommandResult{code, output.str(), error.str()};
}

std::filesystem::path write_replay_config(const std::filesystem::path& destination,
                                          const std::filesystem::path& input,
                                          const itchlab::ValidationMode mode,
                                          const std::uint64_t budget) {
  auto config = Json::parse(read_file(repository_path("configs/replay.diagnostic.example.json")));
  config["input"]["path"] = input.string();
  config["validation"]["mode"] = mode == itchlab::ValidationMode::strict ? "strict" : "permissive";
  config["validation"]["max_skipped_messages"] = budget;
  write_file(destination, config.dump(2) + '\n');
  return destination;
}

std::filesystem::path source_with_suffix(const std::filesystem::path& destination,
                                         const std::string_view suffix,
                                         const std::size_t repetitions = 1) {
  auto bytes = read_file(repository_path("tests/fixtures/synthetic_minimal.itch"));
  for (std::size_t index = 0; index < repetitions; ++index) {
    bytes.append(suffix);
  }
  write_file(destination, bytes);
  return destination;
}

class StepClock final : public itchlab::ProgressClock {
public:
  [[nodiscard]] TimePoint now() const noexcept override {
    if (calls_++ == 0) {
      return TimePoint{};
    }
    return TimePoint{} + std::chrono::seconds{5};
  }

private:
  mutable std::size_t calls_{};
};

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

class CancellingByteSource final : public itchlab::ByteSource {
public:
  CancellingByteSource(std::string bytes, itchlab::CancellationSource& cancellation)
      : bytes_{std::move(bytes)}, cancellation_{cancellation} {}

  itchlab::ReadResult read(const std::span<std::byte> destination) override {
    if (offset_ == bytes_.size()) {
      return itchlab::ReadResult::eof();
    }
    const auto size = std::min(destination.size(), bytes_.size() - offset_);
    for (std::size_t index = 0; index < size; ++index) {
      destination[index] = static_cast<std::byte>(bytes_[offset_ + index]);
    }
    offset_ += size;
    if (offset_ == bytes_.size()) {
      cancellation_.request_cancellation();
    }
    return itchlab::ReadResult::data(size);
  }

  [[nodiscard]] itchlab::SourceProgress progress() const noexcept override {
    return itchlab::SourceProgress{offset_, offset_};
  }

private:
  std::string bytes_;
  itchlab::CancellationSource& cancellation_;
  std::size_t offset_{};
};

} // namespace

TEST_CASE("E2E-003 TASK-012 permissive replay completes degraded for a safely framed unknown type",
          "[TASK-012][E2E-003][permissive][degraded]") {
  TemporaryDirectory temporary;
  const auto unknown =
      read_file(repository_path("tests/fixtures/corrupt/synthetic_corrupt_unknown_type.itch"));
  const auto source = source_with_suffix(temporary.path() / "degraded.itch", unknown);
  const auto config = write_replay_config(temporary.path() / "replay.json", source,
                                          itchlab::ValidationMode::permissive, 1);
  const auto output_root = temporary.path() / "output";

  const auto result = run_command({"replay", "--config", config.string(), "--output-root",
                                   output_root.string(), "--format", "json"});
  REQUIRE(result.exit_code == 0);
  REQUIRE(result.error.empty());
  const auto envelope = Json::parse(result.output);
  REQUIRE(envelope.at("status") == "degraded");
  REQUIRE(envelope.at("summary").at("artefact_status") == "provisional_diagnostic_degraded");
  REQUIRE(envelope.at("summary").at("messages_processed") == 10);
  REQUIRE(envelope.at("summary").at("decoded_messages") == 9);
  REQUIRE(envelope.at("summary").at("errors_observed") == 1);
  REQUIRE(envelope.at("summary").at("skipped_messages") == 1);
  REQUIRE(envelope.at("summary").at("error_counts_by_code") == Json{{"ERR_UNKNOWN_MESSAGE", 1}});
  REQUIRE(envelope.at("warnings").size() == 2);
  REQUIRE(envelope.at("warnings").at(0).get<std::string>().starts_with("DEGRADED:"));
  REQUIRE(std::filesystem::exists(output_root / "diagnostic-events.jsonl"));
  REQUIRE(std::filesystem::exists(output_root / "diagnostic-snapshots.jsonl"));
  REQUIRE_FALSE(std::filesystem::exists(output_root / "diagnostic-events.jsonl.partial"));
}

TEST_CASE("TASK-012 strict mode and permissive budgets stop at the first fatal point",
          "[TASK-012][FR-006][strict][permissive][budget]") {
  TemporaryDirectory temporary;
  const auto unknown =
      read_file(repository_path("tests/fixtures/corrupt/synthetic_corrupt_unknown_type.itch"));

  SECTION("strict mode stops at the first unknown message") {
    const auto source = source_with_suffix(temporary.path() / "strict.itch", unknown);
    const auto config = write_replay_config(temporary.path() / "strict.json", source,
                                            itchlab::ValidationMode::strict, 0);
    const auto output_root = temporary.path() / "strict-output";
    const auto result = run_command({"replay", "--config", config.string(), "--output-root",
                                     output_root.string(), "--format", "json"});
    REQUIRE(result.exit_code == 4);
    const auto envelope = Json::parse(result.output);
    REQUIRE(envelope.at("error").at("code") == "ERR_UNKNOWN_MESSAGE");
    REQUIRE(envelope.at("error").at("context").at("message_index") == 9);
    REQUIRE_FALSE(std::filesystem::exists(output_root / "diagnostic-events.jsonl"));
    REQUIRE(std::filesystem::exists(output_root / "diagnostic-events.jsonl.partial"));
  }

  SECTION("zero budget rejects the first safely skippable message") {
    const auto source = source_with_suffix(temporary.path() / "zero.itch", unknown);
    const auto config = write_replay_config(temporary.path() / "zero.json", source,
                                            itchlab::ValidationMode::permissive, 0);
    const auto result =
        run_command({"replay", "--config", config.string(), "--output-root",
                     (temporary.path() / "zero-output").string(), "--format", "json"});
    REQUIRE(result.exit_code == 4);
    REQUIRE(Json::parse(result.output)
                .at("error")
                .at("message")
                .get<std::string>()
                .find("budget of 0 was exceeded") != std::string::npos);
  }

  SECTION("the message after the exact budget aborts") {
    const auto source = source_with_suffix(temporary.path() / "exceeded.itch", unknown, 2);
    const auto config = write_replay_config(temporary.path() / "exceeded.json", source,
                                            itchlab::ValidationMode::permissive, 1);
    const auto result =
        run_command({"replay", "--config", config.string(), "--output-root",
                     (temporary.path() / "exceeded-output").string(), "--format", "json"});
    REQUIRE(result.exit_code == 4);
    const auto envelope = Json::parse(result.output);
    REQUIRE(envelope.at("error").at("context").at("message_index") == 10);
    REQUIRE(envelope.at("error").at("message").get<std::string>().find(
                "budget of 1 was exceeded") != std::string::npos);
  }
}

TEST_CASE("TASK-012 permissive replay skips frame-local lengths and atomic book rejections only",
          "[TASK-012][FR-006][permissive][atomic][security]") {
  TemporaryDirectory temporary;

  SECTION("safely framed wrong known length is degraded") {
    const auto wrong_length = read_file(
        repository_path("tests/fixtures/corrupt/synthetic_corrupt_wrong_known_length.itch"));
    const auto source = source_with_suffix(temporary.path() / "length.itch", wrong_length);
    const auto config = write_replay_config(temporary.path() / "length.json", source,
                                            itchlab::ValidationMode::permissive, 1);
    const auto result =
        run_command({"replay", "--config", config.string(), "--output-root",
                     (temporary.path() / "length-output").string(), "--format", "json"});
    REQUIRE(result.exit_code == 0);
    REQUIRE(Json::parse(result.output).at("summary").at("error_counts_by_code") ==
            Json{{"ERR_MESSAGE_LENGTH", 1}});
  }

  SECTION("outer framing remains fatal") {
    const std::string zero_length{"\0\0", 2};
    const auto source = source_with_suffix(temporary.path() / "framing.itch", zero_length);
    const auto config = write_replay_config(temporary.path() / "framing.json", source,
                                            itchlab::ValidationMode::permissive, 1);
    const auto result =
        run_command({"replay", "--config", config.string(), "--output-root",
                     (temporary.path() / "framing-output").string(), "--format", "json"});
    REQUIRE(result.exit_code == 3);
    REQUIRE(Json::parse(result.output).at("error").at("code") == "ERR_FRAMING");
  }

  SECTION("atomic duplicate order is skipped without a second event") {
    const auto source =
        repository_path("tests/fixtures/invalid_lifecycle/synthetic_invalid_duplicate_add.itch");
    const auto config = write_replay_config(temporary.path() / "book.json", source,
                                            itchlab::ValidationMode::permissive, 1);
    const auto result =
        run_command({"replay", "--config", config.string(), "--output-root",
                     (temporary.path() / "book-output").string(), "--format", "json"});
    REQUIRE(result.exit_code == 0);
    const auto summary = Json::parse(result.output).at("summary");
    REQUIRE(summary.at("error_counts_by_code") == Json{{"ERR_ORDER_REFERENCE", 1}});
    REQUIRE(summary.at("selected_instrument_messages") == 2);
    REQUIRE(summary.at("selected_events") == 1);
    REQUIRE(summary.at("instruments").at(0).at("final_order_count") == 1);
  }
}

TEST_CASE("TASK-012 non-TTY progress stays on stderr and quiet suppresses it",
          "[TASK-012][NFR-007][progress][channels][non-tty]") {
  TemporaryDirectory temporary;
  const auto source = repository_path("tests/fixtures/synthetic_minimal.itch");
  const auto config = write_replay_config(temporary.path() / "replay.json", source,
                                          itchlab::ValidationMode::strict, 0);
  StepClock clock;

  const auto progress =
      run_command({"replay", "--config", config.string(), "--output-root",
                   (temporary.path() / "progress-output").string(), "--format", "json",
                   "--log-format", "jsonl"},
                  itchlab::cli::RuntimeContext{itchlab::CancellationToken{}, &clock});
  REQUIRE(progress.exit_code == 0);
  REQUIRE(Json::parse(progress.output).at("status") == "completed");
  std::istringstream lines{progress.error};
  std::string line;
  REQUIRE(std::getline(lines, line));
  const auto update = Json::parse(line);
  REQUIRE(update.at("event_code") == "PROGRESS");
  REQUIRE(update.at("stage") == "replay");
  REQUIRE(update.at("elapsed_ms") == 5'000);
  REQUIRE_FALSE(std::getline(lines, line));

  StepClock quiet_clock;
  const auto quiet =
      run_command({"replay", "--config", config.string(), "--output-root",
                   (temporary.path() / "quiet-output").string(), "--format", "json", "--log-format",
                   "jsonl", "--quiet"},
                  itchlab::cli::RuntimeContext{itchlab::CancellationToken{}, &quiet_clock});
  REQUIRE(quiet.exit_code == 0);
  REQUIRE(quiet.error.empty());
  REQUIRE(Json::parse(quiet.output).at("status") == "completed");
}

TEST_CASE("TASK-012 coordinator observes cancellation at a complete message boundary",
          "[TASK-012][E2E-004][cancellation][coordinator]") {
  const auto minimal = read_file(repository_path("tests/fixtures/synthetic_minimal.itch"));
  REQUIRE(minimal.size() >= 14);
  itchlab::CancellationSource cancellation;
  CancellingByteSource source{minimal.substr(0, 14), cancellation};
  const auto parsed = itchlab::parse_replay_config(
      read_file(repository_path("configs/replay.diagnostic.example.json")));
  REQUIRE(parsed.valid());
  CollectingDiagnosticSink diagnostics;

  const itchlab::ReplayCoordinator coordinator;
  const auto result =
      coordinator.run(source, *parsed.config, diagnostics, cancellation.token(), nullptr);
  REQUIRE_FALSE(result.valid());
  REQUIRE(result.error->code == itchlab::ErrorCode::cancelled);
  REQUIRE(result.error->runtime.has_value());
  REQUIRE(result.error->runtime->messages_processed == 1);
  REQUIRE(result.error->runtime->source_progress.uncompressed_bytes_delivered == 14);
  REQUIRE(diagnostics.events.empty());
  REQUIRE(diagnostics.snapshots.empty());
}
