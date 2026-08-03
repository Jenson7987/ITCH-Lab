#include "itchlab/config/replay_config.hpp"

#include <catch2/catch_test_macros.hpp>

#include <fstream>
#include <iterator>
#include <string>

namespace {

std::string read_repository_file(const std::string& relative_path) {
  std::ifstream stream{std::string{ITCHLAB_SOURCE_DIR} + '/' + relative_path};
  REQUIRE(stream.good());
  return {std::istreambuf_iterator<char>{stream}, std::istreambuf_iterator<char>{}};
}

} // namespace

TEST_CASE("UT-CFG-001 C++ replay config accepts the golden v1 document",
          "[TASK-002][UT-CFG-001][config]") {
  const auto result =
      itchlab::parse_replay_config(read_repository_file("tests/golden/configs/valid/replay.json"));

  REQUIRE(result.valid());
  REQUIRE(result.issues.empty());
  REQUIRE(result.config->schema_version == 1);
  REQUIRE(result.config->selection.symbols.size() == 3);
  REQUIRE(result.config->output.depth == 10);
  REQUIRE(result.config->validation.mode == itchlab::ValidationMode::strict);
}

TEST_CASE("UT-CFG-001 C++ replay config rejects unknown keys", "[TASK-002][UT-CFG-001][config]") {
  const auto result = itchlab::parse_replay_config(
      read_repository_file("tests/golden/configs/invalid/replay-unknown-key.json"));

  REQUIRE_FALSE(result.valid());
  REQUIRE_FALSE(result.issues.empty());
  REQUIRE(result.issues.back().json_pointer == "/unexpected");
  REQUIRE(result.issues.back().code == itchlab::ErrorCode::config_schema);
}

TEST_CASE("TASK-002 C++ replay config rejects duplicate names before validation",
          "[TASK-002][config][security]") {
  const auto result = itchlab::parse_replay_config(R"({"schema_version":1,"schema_version":1})");

  REQUIRE_FALSE(result.valid());
  REQUIRE(result.issues.size() == 1);
  REQUIRE(result.issues.front().code == itchlab::ErrorCode::config_schema);
}

TEST_CASE("TASK-002 C++ replay config rejects an invalid half-open session",
          "[TASK-002][config][boundary]") {
  auto document = read_repository_file("tests/golden/configs/valid/replay.json");
  const auto start = document.find("34200000000000");
  REQUIRE(start != std::string::npos);
  document.replace(start, std::string{"34200000000000"}.size(), "57600000000000");

  const auto result = itchlab::parse_replay_config(document);
  REQUIRE_FALSE(result.valid());
  REQUIRE(result.issues.front().code == itchlab::ErrorCode::session_window);
}
