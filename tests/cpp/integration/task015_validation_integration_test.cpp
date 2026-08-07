#include "itchlab/cli.hpp"

#include <catch2/catch_test_macros.hpp>
#include <nlohmann/json.hpp>

#include <algorithm>
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
    path_ = std::filesystem::temp_directory_path() /
            ("itchlab-task015-integration-" + std::to_string(timestamp) + '-' +
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

std::filesystem::path publish_replay(const TemporaryDirectory& temporary) {
  const auto source = repository_path("tests/fixtures/synthetic_minimal.itch");
  auto config = Json::parse(read_file(repository_path("configs/replay.diagnostic.example.json")));
  config["input"]["path"] = source.string();
  const auto config_path = temporary.path() / "replay.json";
  write_file(config_path, config.dump(2) + '\n');
  const auto output_root = temporary.path() / "output";
  const auto replay = run_command({"replay", "--config", config_path.string(), "--output-root",
                                   output_root.string(), "--format", "json", "--quiet"});
  REQUIRE(replay.exit_code == 0);
  REQUIRE(replay.error.empty());
  const auto replay_id =
      Json::parse(replay.output).at("summary").at("replay_id").get<std::string>();
  return output_root / "replay" / replay_id;
}

const Json& json_check(const Json& envelope, const std::string_view name) {
  const auto& checks = envelope.at("summary").at("checks");
  const auto found = std::ranges::find_if(
      checks, [name](const Json& item) { return item.at("name").get<std::string>() == name; });
  REQUIRE(found != checks.end());
  return *found;
}

const Json& error_check(const Json& envelope, const std::string_view name) {
  const auto& checks = envelope.at("error").at("context").at("checks");
  const auto found = std::ranges::find_if(
      checks, [name](const Json& item) { return item.at("name").get<std::string>() == name; });
  REQUIRE(found != checks.end());
  return *found;
}

} // namespace

TEST_CASE("TASK-015 validate command reports shallow and deep completed replay checks",
          "[TASK-015][FR-022][CLI][validation][deep]") {
  TemporaryDirectory temporary;
  const auto run = publish_replay(temporary);

  const auto shallow = run_command({"validate", "--run", run.string(), "--format", "json"});
  REQUIRE(shallow.exit_code == 0);
  REQUIRE(shallow.error.empty());
  const auto shallow_envelope = Json::parse(shallow.output);
  REQUIRE(shallow_envelope.at("command") == "validate");
  REQUIRE(shallow_envelope.at("status") == "completed");
  REQUIRE(shallow_envelope.at("summary").at("mode") == "shallow");
  REQUIRE(shallow_envelope.at("summary").at("records_examined") == 0);
  REQUIRE(json_check(shallow_envelope, "cross_file_identity").at("status") == "pass");

  const auto deep = run_command({"validate", "--run", run.string(), "--verify-source",
                                 repository_path("tests/fixtures/synthetic_minimal.itch").string(),
                                 "--deep", "--format", "json"});
  REQUIRE(deep.exit_code == 0);
  REQUIRE(deep.error.empty());
  const auto envelope = Json::parse(deep.output);
  REQUIRE(envelope.at("summary").at("mode") == "deep");
  REQUIRE(envelope.at("summary").at("records_examined") == 4);
  REQUIRE(envelope.at("summary").at("artefacts").size() == 2);
  REQUIRE(json_check(envelope, "source_hash").at("status") == "pass");
  REQUIRE(json_check(envelope, "events_records").at("records_examined") == 2);
  REQUIRE(json_check(envelope, "snapshots_records").at("records_examined") == 2);
  REQUIRE(json_check(envelope, "final_book_digests").at("status") == "pass");

  const auto wrong_source =
      run_command({"validate", "--run", run.string(), "--verify-source",
                   repository_path("tests/fixtures/synthetic_mixed.itch").string(), "--deep",
                   "--format", "json"});
  REQUIRE(wrong_source.exit_code == 7);
  const auto wrong_source_envelope = Json::parse(wrong_source.output);
  REQUIRE(wrong_source_envelope.at("error").at("code") == "ERR_HASH_MISMATCH");
  REQUIRE(error_check(wrong_source_envelope, "events_records").at("status") == "not_run");

  const auto human = run_command(
      {"validate", "--file",
       repository_path("tests/golden/interchange/synthetic_events_v1.ilb").string(), "--deep"});
  REQUIRE(human.exit_code == 0);
  REQUIRE(human.error.empty());
  REQUIRE(human.output.starts_with("Validation completed.\n"));
  REQUIRE(human.output.find("[PASS] records:") != std::string::npos);
}

TEST_CASE("TASK-015 deep validation authenticates reconstructed final book digests",
          "[TASK-015][validation][deep][digest]") {
  TemporaryDirectory temporary;
  const auto run = publish_replay(temporary);
  const auto manifest_path = run / "replay-manifest.json";
  auto manifest = Json::parse(read_file(manifest_path));
  manifest.at("instruments").at(0).at("final_book_digest") = std::string(64, '0');
  write_file(manifest_path, manifest.dump(2) + '\n');

  const auto result =
      run_command({"validate", "--run", run.string(), "--deep", "--format", "json"});
  REQUIRE(result.exit_code == 7);
  const auto envelope = Json::parse(result.output);
  REQUIRE(envelope.at("error").at("code") == "ERR_HASH_MISMATCH");
  REQUIRE(envelope.at("error").at("context").at("failed_check") == "final_book_digests");
  REQUIRE(error_check(envelope, "snapshots_records").at("status") == "not_run");
}

TEST_CASE("TASK-015 manifest validation rejects duplicate object properties",
          "[TASK-015][validation][manifest][schema]") {
  TemporaryDirectory temporary;
  const auto run = publish_replay(temporary);
  const auto manifest_path = run / "replay-manifest.json";
  auto document = read_file(manifest_path);
  const std::string status_property{"\"status\": \"completed\""};
  const auto status_offset = document.find(status_property);
  REQUIRE(status_offset != std::string::npos);
  document.insert(status_offset, status_property + ",\n  ");
  write_file(manifest_path, document);

  const auto result = run_command({"validate", "--run", run.string(), "--format", "json"});
  REQUIRE(result.exit_code == 7);
  REQUIRE(Json::parse(result.output).at("error").at("code") == "ERR_INVARIANT");
}

TEST_CASE("IT-012 TASK-015 hash tampering fails before deep record use",
          "[TASK-015][IT-012][hash][tamper][security]") {
  TemporaryDirectory temporary;
  const auto run = publish_replay(temporary);
  const auto events_path = run / "events.ilb";
  auto bytes = read_file(events_path);
  bytes.back() = static_cast<char>(bytes.back() ^ 0x01);
  write_file(events_path, bytes);

  const auto result =
      run_command({"validate", "--run", run.string(), "--deep", "--format", "json"});
  REQUIRE(result.exit_code == 7);
  REQUIRE(result.error.empty());
  const auto envelope = Json::parse(result.output);
  REQUIRE(envelope.at("status") == "failed");
  REQUIRE(envelope.at("error").at("code") == "ERR_HASH_MISMATCH");
  REQUIRE(envelope.at("error").at("context").at("records_examined") == 0);
  REQUIRE(error_check(envelope, "events_records").at("status") == "not_run");
  REQUIRE(error_check(envelope, "snapshots_records").at("status") == "not_run");
}

TEST_CASE("TASK-015 CLI distinguishes partial status, unsupported version and path errors",
          "[TASK-015][CLI][partial][schema][exit]") {
  TemporaryDirectory temporary;
  const auto run = publish_replay(temporary);
  const auto manifest_path = run / "replay-manifest.json";
  const auto original_manifest = Json::parse(read_file(manifest_path));

  auto partial_manifest = original_manifest;
  partial_manifest["status"] = "running";
  write_file(manifest_path, partial_manifest.dump(2) + '\n');
  const auto partial = run_command({"validate", "--run", run.string(), "--format", "json"});
  REQUIRE(partial.exit_code == 7);
  REQUIRE(Json::parse(partial.output).at("error").at("code") == "ERR_PARTIAL_ARTEFACT");

  auto unsupported_manifest = original_manifest;
  unsupported_manifest["schema_version"] = 2;
  write_file(manifest_path, unsupported_manifest.dump(2) + '\n');
  const auto unsupported = run_command({"validate", "--run", run.string(), "--format", "json"});
  REQUIRE(unsupported.exit_code == 7);
  REQUIRE(Json::parse(unsupported.output).at("error").at("code") == "ERR_SCHEMA_VERSION");

  const auto missing = run_command(
      {"validate", "--run", (temporary.path() / "missing").string(), "--format", "json"});
  REQUIRE(missing.exit_code == 3);
  REQUIRE(Json::parse(missing.output).at("error").at("code") == "ERR_INPUT_PATH");

  const auto usage = run_command({"validate", "--format", "json"});
  REQUIRE(usage.exit_code == 2);
  REQUIRE(Json::parse(usage.output).at("error").at("code") == "ERR_CONFIG_SCHEMA");

  const auto both =
      run_command({"validate", "--run", run.string(), "--file",
                   repository_path("tests/golden/interchange/synthetic_events_v1.ilb").string(),
                   "--format", "json"});
  REQUIRE(both.exit_code == 2);
}
