#include "itchlab/config/canonical_json.hpp"
#include "itchlab/config/replay_config.hpp"
#include "itchlab/core/sha256.hpp"

#include <catch2/catch_test_macros.hpp>

#include <fstream>
#include <iterator>
#include <string>

namespace {

std::string read_repository_file(const std::string& relative_path) {
  std::ifstream stream{std::string{ITCHLAB_SOURCE_DIR} + '/' + relative_path};
  REQUIRE(stream.good());
  std::string value{std::istreambuf_iterator<char>{stream}, std::istreambuf_iterator<char>{}};
  if (!value.empty() && value.back() == '\n') {
    value.pop_back();
  }
  return value;
}

} // namespace

TEST_CASE("TASK-002 C++ canonical config hashes match cross-language goldens",
          "[TASK-002][contract][hash]") {
  const auto source = read_repository_file("tests/golden/configs/valid/replay.json");
  const auto parsed = itchlab::parse_replay_config(source);
  REQUIRE(parsed.valid());

  const auto full = itchlab::canonical_replay_config(*parsed.config);
  const auto identity = itchlab::canonical_replay_identity_config(*parsed.config);
  const auto hashes = itchlab::replay_config_hashes(*parsed.config);

  REQUIRE(full == read_repository_file("tests/golden/configs/replay.canonical.json"));
  REQUIRE(identity == read_repository_file("tests/golden/configs/replay.identity.canonical.json"));
  REQUIRE(itchlab::content_hash_to_hex(hashes.config_sha256) ==
          read_repository_file("tests/golden/configs/replay.sha256"));
  REQUIRE(itchlab::content_hash_to_hex(hashes.identity_config_sha256) ==
          read_repository_file("tests/golden/configs/replay.identity.sha256"));
}
