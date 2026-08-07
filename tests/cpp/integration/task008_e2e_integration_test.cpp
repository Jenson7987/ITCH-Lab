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
  summary.erase("replay_id");
  for (const auto* field : {"event_path", "snapshot_path", "manifest_path"}) {
    summary[field] =
        std::filesystem::path{summary.at(field).get<std::string>()}.filename().string();
  }
  return envelope.dump() + '\n';
}

std::filesystem::path replay_directory(const std::filesystem::path& output_root,
                                       const std::string& output) {
  return output_root / "replay" /
         Json::parse(output).at("summary").at("replay_id").get<std::string>();
}

std::filesystem::path only_partial_directory(const std::filesystem::path& replay_root) {
  std::vector<std::filesystem::path> partials;
  for (const auto& entry : std::filesystem::directory_iterator{replay_root}) {
    if (entry.is_directory() && entry.path().extension() == ".partial") {
      partials.push_back(entry.path());
    }
  }
  REQUIRE(partials.size() == 1);
  return partials.front();
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

  REQUIRE(path_independent_replay_output(first.output) ==
          path_independent_replay_output(second.output));

  const auto first_run = replay_directory(first_root, first.output);
  const auto second_run = replay_directory(second_root, second.output);
  REQUIRE(read_file(first_run / "events.ilb") == read_file(second_run / "events.ilb"));
  REQUIRE(read_file(first_run / "snapshots.ilb") == read_file(second_run / "snapshots.ilb"));
  auto first_manifest = Json::parse(read_file(first_run / "replay-manifest.json"));
  auto second_manifest = Json::parse(read_file(second_run / "replay-manifest.json"));
  for (const auto* field : {"started_at", "completed_at", "replay_id"}) {
    first_manifest.erase(field);
    second_manifest.erase(field);
  }
  REQUIRE(first_manifest == second_manifest);
}

TEST_CASE("E2E-002 TASK-014 corrupt gzip never publishes a completed replay",
          "[TASK-008][TASK-014][E2E-002][integration][security]") {
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
  REQUIRE(Json::parse(result.output).at("error").at("code") == "ERR_FRAMING");
  REQUIRE(read_file(corrupt_source) == source_before);

  const auto partial = only_partial_directory(output_root / "replay");
  REQUIRE_FALSE(std::filesystem::exists(output_root / "events.ilb"));
  REQUIRE_FALSE(std::filesystem::exists(output_root / "snapshots.ilb"));
  REQUIRE_FALSE(std::filesystem::exists(output_root / "replay-manifest.json"));
  REQUIRE(std::filesystem::is_regular_file(partial / "events.ilb.partial"));
  REQUIRE(std::filesystem::is_regular_file(partial / "snapshots.ilb.partial"));
  REQUIRE_FALSE(std::filesystem::exists(partial / "replay-manifest.json"));
}
