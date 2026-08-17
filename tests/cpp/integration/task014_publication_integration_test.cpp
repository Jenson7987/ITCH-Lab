#include "itchlab/cli.hpp"
#include "itchlab/core/sha256.hpp"
#include "itchlab/output/manifest.hpp"

#include <catch2/catch_test_macros.hpp>
#include <nlohmann/json.hpp>

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
        std::filesystem::temp_directory_path() / ("itchlab-task014-" + std::to_string(timestamp) +
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

std::filesystem::path
write_replay_config(const std::filesystem::path& destination, const std::filesystem::path& source,
                    const std::optional<std::string_view> expected_hash = std::nullopt) {
  auto config = Json::parse(read_file(repository_path("configs/replay.diagnostic.example.json")));
  config["input"]["path"] = source.string();
  if (expected_hash) {
    config["input"]["sha256"] = *expected_hash;
  }
  write_file(destination, config.dump(2) + '\n');
  return destination;
}

void require_no_private_path(const Json& value, const std::string_view private_root) {
  if (value.is_object()) {
    for (const auto& item : value.items()) {
      require_no_private_path(item.value(), private_root);
    }
  } else if (value.is_array()) {
    for (const auto& item : value) {
      require_no_private_path(item, private_root);
    }
  } else if (value.is_string()) {
    REQUIRE(value.get_ref<const std::string&>().find(private_root) == std::string::npos);
  }
}

std::size_t directory_count(const std::filesystem::path& path) {
  std::size_t count{};
  for (const auto& entry : std::filesystem::directory_iterator{path}) {
    if (entry.is_directory() && entry.path().extension() != ".partial" &&
        !entry.path().filename().string().starts_with('.')) {
      ++count;
    }
  }
  return count;
}

itchlab::ContentHash sequential_hash() {
  itchlab::ContentHash hash{};
  for (std::size_t index = 0; index < hash.size(); ++index) {
    hash[index] = static_cast<std::byte>(static_cast<std::uint8_t>(index + 1));
  }
  return hash;
}

} // namespace

TEST_CASE("SEC-PATH-001 IT-004 TASK-014 replay publishes a verified private-path-free immutable "
          "directory",
          "[TASK-014][IT-004][SEC-PATH-001][manifest][publication][idempotency][security]") {
  TemporaryDirectory temporary;
  const auto source = repository_path("tests/fixtures/synthetic_minimal.itch");
  const auto source_content = read_file(source);
  const auto sentinel = temporary.path() / "unrelated-sentinel";
  write_file(sentinel, "unchanged");
  const auto source_hash = itchlab::content_hash_to_hex(itchlab::sha256(source_content));
  const auto config = write_replay_config(temporary.path() / "replay.json", source, source_hash);
  const auto output_root = temporary.path() / "output";

  const auto first = run_command({"replay", "--config", config.string(), "--output-root",
                                  output_root.string(), "--format", "json", "--quiet"});
  REQUIRE(first.exit_code == 0);
  REQUIRE(first.error.empty());
  const auto first_envelope = Json::parse(first.output);
  REQUIRE(first_envelope.at("status") == "completed");
  REQUIRE(first_envelope.at("summary").at("artefact_status") == "published");
  REQUIRE_FALSE(first_envelope.at("summary").at("reused").get<bool>());
  require_no_private_path(first_envelope, temporary.path().string());
  require_no_private_path(first_envelope, repository_path("").string());
  const auto replay_id = first_envelope.at("summary").at("replay_id").get<std::string>();
  const auto run_directory = output_root / "replay" / replay_id;
  const auto event_path = run_directory / "events.ilb";
  const auto snapshot_path = run_directory / "snapshots.ilb";
  const auto manifest_path = run_directory / "replay-manifest.json";
  REQUIRE(std::filesystem::is_regular_file(event_path));
  REQUIRE(std::filesystem::is_regular_file(snapshot_path));
  REQUIRE(std::filesystem::is_regular_file(manifest_path));
  REQUIRE(std::distance(std::filesystem::directory_iterator{run_directory},
                        std::filesystem::directory_iterator{}) == 3);

  const auto manifest = Json::parse(read_file(manifest_path));
  REQUIRE(manifest.at("schema_version") == 1);
  REQUIRE(manifest.at("replay_id").get<std::string>() == replay_id);
  REQUIRE(manifest.at("status") == "completed");
  REQUIRE(manifest.at("source").at("sha256").get<std::string>() == source_hash);
  REQUIRE(manifest.at("source").at("canonical_name").get<std::string>() ==
          source.filename().string());
  REQUIRE(manifest.at("config").at("input").at("path").get<std::string>() ==
          source.filename().string());
  REQUIRE(manifest.at("config").at("input").at("sha256").get<std::string>() == source_hash);
  REQUIRE(manifest.at("counts").at("selected_events") == 2);
  REQUIRE(manifest.at("counts").at("snapshots_written") == 2);
  REQUIRE(manifest.at("artefacts").at(0).at("record_size") == 72);
  REQUIRE(manifest.at("artefacts").at(1).at("record_size") == 104);
  REQUIRE(manifest.at("artefacts").at(1).at("depth") == 2);
  require_no_private_path(manifest, temporary.path().string());
  require_no_private_path(manifest, repository_path("").string());

  const auto event_hash = itchlab::hash_file(event_path, itchlab::ErrorCode::hash_mismatch);
  const auto snapshot_hash = itchlab::hash_file(snapshot_path, itchlab::ErrorCode::hash_mismatch);
  REQUIRE(event_hash.valid());
  REQUIRE(snapshot_hash.valid());
  REQUIRE(itchlab::content_hash_to_hex(event_hash.file->sha256) ==
          manifest.at("artefacts").at(0).at("sha256").get<std::string>());
  REQUIRE(event_hash.file->size_bytes == manifest.at("artefacts").at(0).at("size_bytes"));
  REQUIRE(itchlab::content_hash_to_hex(snapshot_hash.file->sha256) ==
          manifest.at("artefacts").at(1).at("sha256").get<std::string>());
  REQUIRE(snapshot_hash.file->size_bytes == manifest.at("artefacts").at(1).at("size_bytes"));

  const auto second = run_command({"replay", "--config", config.string(), "--output-root",
                                   output_root.string(), "--format", "json", "--quiet"});
  REQUIRE(second.exit_code == 0);
  const auto second_summary = Json::parse(second.output).at("summary");
  REQUIRE(second_summary.at("replay_id").get<std::string>() == replay_id);
  REQUIRE(second_summary.at("reused").get<bool>());
  REQUIRE(directory_count(output_root / "replay") == 1);

  const auto original_events = read_file(event_path);
  auto tampered_events = original_events;
  tampered_events.back() = static_cast<char>(tampered_events.back() ^ 0x01);
  write_file(event_path, tampered_events);
  const auto tampered = run_command({"replay", "--config", config.string(), "--output-root",
                                     output_root.string(), "--format", "json", "--quiet"});
  REQUIRE(tampered.exit_code == 7);
  REQUIRE(Json::parse(tampered.output).at("error").at("code") == "ERR_HASH_MISMATCH");
  REQUIRE(directory_count(output_root / "replay") == 1);
  write_file(event_path, original_events);

  const auto forced =
      run_command({"replay", "--config", config.string(), "--output-root", output_root.string(),
                   "--force-new-run", "--format", "json", "--quiet"});
  REQUIRE(forced.exit_code == 0);
  const auto forced_summary = Json::parse(forced.output).at("summary");
  REQUIRE_FALSE(forced_summary.at("reused").get<bool>());
  REQUIRE(forced_summary.at("replay_id").get<std::string>() != replay_id);
  REQUIRE(directory_count(output_root / "replay") == 2);
  REQUIRE(read_file(source) == source_content);
  REQUIRE(read_file(sentinel) == "unchanged");
}

TEST_CASE("SEC-PATH-001 TASK-014 rejects broad aliases and symlink output roots",
          "[TASK-014][SEC-PATH-001][path][security]") {
  TemporaryDirectory temporary;
  const auto source = temporary.path() / "source.itch";
  const auto source_content = read_file(repository_path("tests/fixtures/synthetic_minimal.itch"));
  const auto sentinel = temporary.path() / "unrelated-sentinel";
  write_file(source, source_content);
  write_file(sentinel, "unchanged");
  const auto identity = sequential_hash();
  constexpr std::string_view replay_id{"20260807T120000.000000000Z-010203040506"};

  const auto containing = itchlab::prepare_replay_run(temporary.path(), source, identity,
                                                      std::string{replay_id}, false);
  REQUIRE(containing.error->code == itchlab::ErrorCode::output_path);
  REQUIRE_FALSE(std::filesystem::exists(temporary.path() / "replay"));

  const auto real_root = temporary.path() / "real-output";
  const auto linked_root = temporary.path() / "linked-output";
  REQUIRE(std::filesystem::create_directory(real_root));
  std::error_code link_error;
  std::filesystem::create_directory_symlink(real_root, linked_root, link_error);
  if (!link_error) {
    const auto linked =
        itchlab::prepare_replay_run(linked_root, source, identity, std::string{replay_id}, false);
    REQUIRE(linked.error->code == itchlab::ErrorCode::output_path);
  }
  REQUIRE(read_file(source) == source_content);
  REQUIRE(read_file(sentinel) == "unchanged");
}

TEST_CASE("TASK-014 publication exposes no completed name until every staged child exists",
          "[TASK-014][atomic-write][manifest-last][partial]") {
  TemporaryDirectory temporary;
  const auto source = temporary.path() / "source.itch";
  write_file(source, "source");
  const auto identity = sequential_hash();
  constexpr std::string_view replay_id{"20260807T120000.000000000Z-010203040506"};
  const auto prepared = itchlab::prepare_replay_run(temporary.path() / "output", source, identity,
                                                    std::string{replay_id}, false);
  REQUIRE(prepared.ready());
  const auto& paths = *prepared.paths;
  write_file(paths.event_path.string() + ".partial", "events");

  const auto incomplete = itchlab::publish_replay_run(paths, "{\"status\":\"completed\"}\n");
  REQUIRE(incomplete->code == itchlab::ErrorCode::output_path);
  REQUIRE_FALSE(std::filesystem::exists(paths.final_directory));
  REQUIRE_FALSE(std::filesystem::exists(paths.manifest_path));

  write_file(paths.snapshot_path.string() + ".partial", "snapshots");
  REQUIRE_FALSE(itchlab::publish_replay_run(paths, "{\"status\":\"completed\"}\n").has_value());
  REQUIRE(std::filesystem::is_directory(paths.final_directory));
  REQUIRE_FALSE(std::filesystem::exists(paths.staging_directory));
  REQUIRE_FALSE(std::filesystem::exists(paths.lock_path));
  REQUIRE(read_file(paths.final_directory / "events.ilb") == "events");
  REQUIRE(read_file(paths.final_directory / "snapshots.ilb") == "snapshots");
  REQUIRE(read_file(paths.final_directory / "replay-manifest.json") ==
          "{\"status\":\"completed\"}\n");
}

TEST_CASE("SEC-PATH-001 TASK-014 publication rejects symlinked staged artefacts",
          "[TASK-014][SEC-PATH-001][path][security]") {
  TemporaryDirectory temporary;
  const auto source = temporary.path() / "source.itch";
  const auto sentinel = temporary.path() / "unrelated-sentinel";
  write_file(source, "source");
  write_file(sentinel, "unchanged");
  const auto identity = sequential_hash();
  constexpr std::string_view replay_id{"20260807T120000.000000000Z-010203040506"};
  const auto prepared = itchlab::prepare_replay_run(temporary.path() / "output", source, identity,
                                                    std::string{replay_id}, false);
  REQUIRE(prepared.ready());
  const auto& paths = *prepared.paths;

  std::error_code link_error;
  std::filesystem::create_symlink(sentinel, paths.event_path.string() + ".partial", link_error);
  if (link_error) {
    return;
  }
  write_file(paths.snapshot_path.string() + ".partial", "snapshots");

  const auto result = itchlab::publish_replay_run(paths, "{\"status\":\"completed\"}\n");
  REQUIRE(result.has_value());
  REQUIRE(result->code == itchlab::ErrorCode::output_path);
  REQUIRE(read_file(sentinel) == "unchanged");
  REQUIRE_FALSE(std::filesystem::exists(paths.final_directory));
}

TEST_CASE("SEC-PATH-001 TASK-014 publication does not follow a dangling manifest symlink",
          "[TASK-014][SEC-PATH-001][path][security]") {
  TemporaryDirectory temporary;
  const auto source = temporary.path() / "source.itch";
  const auto outside = temporary.path() / "outside-manifest";
  write_file(source, "source");
  const auto identity = sequential_hash();
  constexpr std::string_view replay_id{"20260807T120000.000000000Z-010203040506"};
  const auto prepared = itchlab::prepare_replay_run(temporary.path() / "output", source, identity,
                                                    std::string{replay_id}, false);
  REQUIRE(prepared.ready());
  const auto& paths = *prepared.paths;
  write_file(paths.event_path.string() + ".partial", "events");
  write_file(paths.snapshot_path.string() + ".partial", "snapshots");

  std::error_code link_error;
  std::filesystem::create_symlink(outside, paths.manifest_path.string() + ".partial", link_error);
  if (link_error) {
    return;
  }

  const auto result = itchlab::publish_replay_run(paths, "{\"status\":\"completed\"}\n");
  REQUIRE(result.has_value());
  REQUIRE(result->code == itchlab::ErrorCode::output_path);
  REQUIRE_FALSE(std::filesystem::exists(outside));
  REQUIRE_FALSE(std::filesystem::exists(paths.final_directory));
}

TEST_CASE("TASK-014 rejects a mismatched configured source digest before staging",
          "[TASK-014][hash][lineage][security]") {
  TemporaryDirectory temporary;
  const auto source = repository_path("tests/fixtures/synthetic_minimal.itch");
  const auto config = write_replay_config(temporary.path() / "replay.json", source,
                                          std::string_view{"00"
                                                           "00"
                                                           "00"
                                                           "00"
                                                           "00"
                                                           "00"
                                                           "00"
                                                           "00"
                                                           "00"
                                                           "00"
                                                           "00"
                                                           "00"
                                                           "00"
                                                           "00"
                                                           "00"
                                                           "00"
                                                           "00"
                                                           "00"
                                                           "00"
                                                           "00"
                                                           "00"
                                                           "00"
                                                           "00"
                                                           "00"
                                                           "00"
                                                           "00"
                                                           "00"
                                                           "00"
                                                           "00"
                                                           "00"
                                                           "00"
                                                           "00"});
  const auto output_root = temporary.path() / "output";
  const auto result = run_command({"replay", "--config", config.string(), "--output-root",
                                   output_root.string(), "--format", "json", "--quiet"});
  REQUIRE(result.exit_code == 7);
  REQUIRE(Json::parse(result.output).at("error").at("code") == "ERR_HASH_MISMATCH");
  REQUIRE_FALSE(std::filesystem::exists(output_root));
}
