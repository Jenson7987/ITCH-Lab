#include "itchlab/cli.hpp"

#include <catch2/catch_test_macros.hpp>
#include <nlohmann/json.hpp>

#include <atomic>
#include <chrono>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iterator>
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

class TemporaryDirectory {
public:
  TemporaryDirectory() {
    static std::atomic<std::uint64_t> sequence{};
    const auto timestamp = std::chrono::steady_clock::now().time_since_epoch().count();
    path_ =
        std::filesystem::temp_directory_path() / ("itchlab-task008-" + std::to_string(timestamp) +
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

CommandResult run_command(const std::vector<std::string>& owned_arguments) {
  std::vector<std::string_view> arguments;
  arguments.reserve(owned_arguments.size());
  for (const auto& argument : owned_arguments) {
    arguments.push_back(argument);
  }
  std::ostringstream output;
  std::ostringstream error;
  const auto code = itchlab::cli::run(arguments, output, error);
  return CommandResult{code, output.str(), error.str()};
}

std::filesystem::path write_replay_config(const std::filesystem::path& destination,
                                          const std::filesystem::path& input) {
  auto config = Json::parse(read_file(repository_path("configs/replay.diagnostic.example.json")));
  config["input"]["path"] = input.string();
  std::ofstream stream{destination, std::ios::binary};
  REQUIRE(stream.good());
  stream << config.dump(2) << '\n';
  stream.close();
  REQUIRE_FALSE(stream.fail());
  return destination;
}

std::string path_independent_replay_output(const std::string& output) {
  auto envelope = Json::parse(output);
  auto& summary = envelope.at("summary");
  for (const auto* field : {"event_path", "snapshot_path"}) {
    summary[field] =
        std::filesystem::path{summary.at(field).get<std::string>()}.filename().string();
  }
  return envelope.dump() + '\n';
}

} // namespace

TEST_CASE("E2E-001 TASK-008 minimal inspect and replay slice is reproducible",
          "[TASK-008][E2E-001][integration][golden]") {
  const auto inspect = run_command(
      {"inspect", "--input", repository_path("tests/fixtures/synthetic_minimal.itch").string(),
       "--all", "--symbols", "AAPL", "--format", "json"});
  REQUIRE(inspect.exit_code == 0);
  REQUIRE(inspect.error.empty());
  REQUIRE(inspect.output == read_file(repository_path("tests/golden/minimal/inspect.json")));

  TemporaryDirectory temporary;
  const auto config = write_replay_config(temporary.path() / "replay.json",
                                          repository_path("tests/fixtures/synthetic_minimal.itch"));
  const auto first_root = temporary.path() / "first";
  const auto second_root = temporary.path() / "second";

  const auto first = run_command({"replay", "--config", config.string(), "--output-root",
                                  first_root.string(), "--format", "json"});
  const auto second = run_command({"replay", "--config", config.string(), "--output-root",
                                   second_root.string(), "--format", "json"});
  REQUIRE(first.exit_code == 0);
  REQUIRE(second.exit_code == 0);
  REQUIRE(first.error.empty());
  REQUIRE(second.error.empty());

  const auto expected_replay = read_file(repository_path("tests/golden/minimal/replay.json"));
  REQUIRE(path_independent_replay_output(first.output) == expected_replay);
  REQUIRE(path_independent_replay_output(second.output) == expected_replay);

  const auto expected_events =
      read_file(repository_path("tests/golden/itch50/synthetic_minimal_diagnostic_events.jsonl"));
  const auto expected_snapshots = read_file(
      repository_path("tests/golden/itch50/synthetic_minimal_diagnostic_snapshots.jsonl"));
  REQUIRE(read_file(first_root / "diagnostic-events.jsonl") == expected_events);
  REQUIRE(read_file(second_root / "diagnostic-events.jsonl") == expected_events);
  REQUIRE(read_file(first_root / "diagnostic-snapshots.jsonl") == expected_snapshots);
  REQUIRE(read_file(second_root / "diagnostic-snapshots.jsonl") == expected_snapshots);
}

TEST_CASE("E2E-002 TASK-008 corrupt gzip never publishes final diagnostics",
          "[TASK-008][E2E-002][integration][security]") {
  TemporaryDirectory temporary;
  const auto corrupt_source =
      repository_path("tests/fixtures/corrupt/synthetic_corrupt_gzip_checksum.itch.gz");
  const auto source_before = read_file(corrupt_source);
  const auto config = write_replay_config(temporary.path() / "corrupt.json", corrupt_source);
  const auto output_root = temporary.path() / "output";

  const auto result = run_command({"replay", "--config", config.string(), "--output-root",
                                   output_root.string(), "--format", "json"});
  REQUIRE(result.exit_code == 3);
  REQUIRE(result.error.empty());
  REQUIRE(result.output == read_file(repository_path("tests/golden/minimal/corrupt-replay.json")));
  REQUIRE(read_file(corrupt_source) == source_before);

  REQUIRE_FALSE(std::filesystem::exists(output_root / "diagnostic-events.jsonl"));
  REQUIRE_FALSE(std::filesystem::exists(output_root / "diagnostic-snapshots.jsonl"));
  REQUIRE(read_file(output_root / "diagnostic-events.jsonl.partial").empty());
  REQUIRE(read_file(output_root / "diagnostic-snapshots.jsonl.partial").empty());
}
