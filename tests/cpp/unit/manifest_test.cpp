#include "itchlab/config/canonical_json.hpp"
#include "itchlab/config/replay_config.hpp"
#include "itchlab/core/sha256.hpp"
#include "itchlab/output/manifest.hpp"

#include <catch2/catch_test_macros.hpp>
#include <nlohmann/json.hpp>

#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <string>
#include <utility>

namespace {

using Json = nlohmann::json;

std::string read_file(const std::filesystem::path& path) {
  std::ifstream stream{path, std::ios::binary};
  REQUIRE(stream.good());
  return {std::istreambuf_iterator<char>{stream}, std::istreambuf_iterator<char>{}};
}

itchlab::ContentHash sequential_hash(const std::uint8_t first) {
  itchlab::ContentHash result{};
  for (std::size_t index = 0; index < result.size(); ++index) {
    result[index] = static_cast<std::byte>(static_cast<std::uint8_t>(first + index));
  }
  return result;
}

itchlab::ReplayManifestInput manifest_input() {
  const auto parsed = itchlab::parse_replay_config(read_file(
      std::filesystem::path{ITCHLAB_SOURCE_DIR} / "configs/replay.diagnostic.example.json"));
  REQUIRE(parsed.valid());
  auto config = *parsed.config;
  const auto source_hash = sequential_hash(1);
  const auto executable_hash = sequential_hash(65);
  config.input.path = "synthetic_minimal.itch";
  config.input.sha256 = source_hash;
  const auto config_hashes = itchlab::replay_config_hashes(config);
  const auto identity = itchlab::replay_identity_hash(
      source_hash, config_hashes.identity_config_sha256, executable_hash);

  itchlab::ReplaySummary summary;
  summary.messages_processed = 9;
  summary.decoded_messages = 9;
  summary.global_system_messages = 6;
  summary.directory_messages = 1;
  summary.selected_instrument_messages = 2;
  summary.selected_events = 2;
  summary.snapshots_written = 2;
  summary.all_counts_by_type = {{"A", 1}, {"D", 1}, {"R", 1}, {"S", 6}};
  summary.selected_counts_by_type = {{"A", 1}, {"D", 1}};
  summary.instruments.push_back(
      itchlab::ReplayInstrumentSummary{itchlab::Instrument{1, 1, "AAPL", 'Q', 'N', 100, false}, 0,
                                       sequential_hash(97), itchlab::TradingState::closed});
  summary.source_progress = {184, 184};

  return itchlab::ReplayManifestInput{
      "20260807T120000.000000000Z-" + itchlab::content_hash_to_hex(identity).substr(0, 12),
      identity,
      config,
      config_hashes,
      itchlab::HashedFile{source_hash, 184},
      "synthetic_minimal.itch",
      itchlab::InputCompression::none,
      itchlab::HashedFile{executable_hash, 1'024},
      itchlab::BuildMetadata{"0.1.0", std::string(40, 'a'), false, "Clang", "18.1.0",
                             "Linux-x86_64", "Release"},
      "2026-08-07T12:00:00.000000000Z",
      "2026-08-07T12:00:01.000000000Z",
      std::move(summary),
      itchlab::HashedFile{sequential_hash(129), 264},
      itchlab::HashedFile{sequential_hash(161), 328},
      2,
      2,
  };
}

} // namespace

TEST_CASE("TASK-014 replay identity uses the documented domain and ordered raw hashes",
          "[TASK-014][identity][hash][contract]") {
  const auto actual =
      itchlab::replay_identity_hash(sequential_hash(1), sequential_hash(33), sequential_hash(65));
  REQUIRE(itchlab::content_hash_to_hex(actual) ==
          "13a05e5d66a4777dc2376ada76a90d2ad78b7ca9dcf0dd416ba1791fa3e03c20");
}

TEST_CASE("TASK-014 manifest builder records completed lineage and checks exact sizes",
          "[TASK-014][manifest][lineage][validation]") {
  auto input = manifest_input();
  const auto built = itchlab::build_replay_manifest(input);
  REQUIRE(built.valid());
  const auto manifest = Json::parse(*built.document);
  REQUIRE(manifest.at("status") == "completed");
  REQUIRE(manifest.at("publishable").get<bool>());
  REQUIRE(manifest.at("source").at("canonical_name") == "synthetic_minimal.itch");
  REQUIRE(manifest.at("config").at("input").at("path") == "synthetic_minimal.itch");
  REQUIRE(manifest.at("counts").at("selected_events") == 2);
  REQUIRE(manifest.at("artefacts").at(0).at("size_bytes") == 264);
  REQUIRE(manifest.at("artefacts").at(1).at("size_bytes") == 328);

  input.build.build_type = "Debug";
  const auto development = itchlab::build_replay_manifest(input);
  REQUIRE(development.valid());
  REQUIRE_FALSE(Json::parse(*development.document).at("publishable").get<bool>());

  input.events.size_bytes = 263;
  const auto wrong_size = itchlab::build_replay_manifest(input);
  REQUIRE_FALSE(wrong_size.valid());
  REQUIRE(wrong_size.error->code == itchlab::ErrorCode::invariant);
}

TEST_CASE(
    "TASK-014 manifest builder rejects locator leakage even with internally consistent hashes",
    "[TASK-014][manifest][path][security]") {
  auto input = manifest_input();
  input.effective_config.input.path = "/Users/alice/private.itch";
  input.config_hashes = itchlab::replay_config_hashes(input.effective_config);
  input.identity_sha256 = itchlab::replay_identity_hash(
      input.source.sha256, input.config_hashes.identity_config_sha256, input.executable.sha256);
  input.replay_id = "20260807T120000.000000000Z-" +
                    itchlab::content_hash_to_hex(input.identity_sha256).substr(0, 12);

  const auto built = itchlab::build_replay_manifest(input);
  REQUIRE_FALSE(built.valid());
  REQUIRE(built.error->code == itchlab::ErrorCode::invariant);

  input.effective_config.input.path = "C:\\Users\\alice\\private.itch";
  input.config_hashes = itchlab::replay_config_hashes(input.effective_config);
  input.identity_sha256 = itchlab::replay_identity_hash(
      input.source.sha256, input.config_hashes.identity_config_sha256, input.executable.sha256);
  input.replay_id = "20260807T120000.000000000Z-" +
                    itchlab::content_hash_to_hex(input.identity_sha256).substr(0, 12);
  const auto windows_path = itchlab::build_replay_manifest(input);
  REQUIRE_FALSE(windows_path.valid());
  REQUIRE(windows_path.error->code == itchlab::ErrorCode::invariant);
}
