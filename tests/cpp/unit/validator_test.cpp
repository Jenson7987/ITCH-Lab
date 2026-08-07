#include "itchlab/core/errors.hpp"
#include "itchlab/validation/validator.hpp"

#include <catch2/catch_test_macros.hpp>

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <string>
#include <string_view>
#include <system_error>

namespace {

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
            ("itchlab-task015-unit-" + std::to_string(timestamp) + '-' +
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

const itchlab::ValidationCheck& check(const itchlab::ArtefactValidationReport& report,
                                      const std::string_view name) {
  const auto found = std::ranges::find_if(
      report.checks, [name](const itchlab::ValidationCheck& item) { return item.name == name; });
  REQUIRE(found != report.checks.end());
  return *found;
}

} // namespace

TEST_CASE("CT-BIN-001 TASK-015 deep validator accepts independent event and snapshot goldens",
          "[TASK-015][CT-BIN-001][validation][golden]") {
  const auto events = itchlab::validate_interchange_file(
      repository_path("tests/golden/interchange/synthetic_events_v1.ilb"), true);
  REQUIRE(events.valid());
  REQUIRE(events.report.artefacts.size() == 1);
  REQUIRE(events.report.artefacts.front().kind == "events");
  REQUIRE(events.report.artefacts.front().declared_records == 10);
  REQUIRE(events.report.artefacts.front().records_examined == 10);
  REQUIRE(check(events.report, "records").status == itchlab::ValidationCheckStatus::passed);

  const auto snapshots = itchlab::validate_interchange_file(
      repository_path("tests/golden/interchange/synthetic_snapshots_v1.ilb"), true);
  REQUIRE(snapshots.valid());
  REQUIRE(snapshots.report.artefacts.size() == 1);
  REQUIRE(snapshots.report.artefacts.front().kind == "snapshots");
  REQUIRE(snapshots.report.artefacts.front().declared_records == 2);
  REQUIRE(snapshots.report.artefacts.front().records_examined == 2);
}

TEST_CASE("TASK-015 standalone validation distinguishes partial and unsupported schema",
          "[TASK-015][validation][partial][schema]") {
  TemporaryDirectory temporary;
  const auto golden =
      read_file(repository_path("tests/golden/interchange/synthetic_events_v1.ilb"));

  const auto partial_path = temporary.path() / "events.ilb.partial";
  write_file(partial_path, golden);
  const auto partial = itchlab::validate_interchange_file(partial_path, true);
  REQUIRE_FALSE(partial.valid());
  REQUIRE(partial.error->code == itchlab::ErrorCode::partial_artefact);
  REQUIRE(check(partial.report, "events_records").status ==
          itchlab::ValidationCheckStatus::not_run);

  auto unknown_version = golden;
  unknown_version[8] = 2;
  unknown_version[9] = 0;
  const auto version_path = temporary.path() / "unknown.ilb";
  write_file(version_path, unknown_version);
  const auto unsupported = itchlab::validate_interchange_file(version_path, false);
  REQUIRE_FALSE(unsupported.valid());
  REQUIRE(unsupported.error->code == itchlab::ErrorCode::schema_version);

  const auto truncated_path = temporary.path() / "truncated.ilb";
  write_file(truncated_path, std::string_view{golden}.substr(0, golden.size() - 1));
  const auto truncated = itchlab::validate_interchange_file(truncated_path, false);
  REQUIRE_FALSE(truncated.valid());
  REQUIRE(truncated.error->code == itchlab::ErrorCode::partial_artefact);
}

TEST_CASE("TASK-015 deep event validation rejects reserved bits and source reordering",
          "[TASK-015][validation][events][ordering][flags]") {
  TemporaryDirectory temporary;
  const auto golden =
      read_file(repository_path("tests/golden/interchange/synthetic_events_v1.ilb"));
  constexpr std::size_t records_offset = 104 + 2 * 16;

  auto reserved = golden;
  reserved[records_offset + 65] = 1;
  const auto reserved_path = temporary.path() / "reserved.ilb";
  write_file(reserved_path, reserved);
  const auto reserved_result = itchlab::validate_interchange_file(reserved_path, true);
  REQUIRE_FALSE(reserved_result.valid());
  REQUIRE(reserved_result.error->code == itchlab::ErrorCode::invariant);
  REQUIRE(reserved_result.error->record_index == 0);

  auto reordered = golden;
  for (std::size_t index = 0; index < 8; ++index) {
    reordered[records_offset + 72 + index] = reordered[records_offset + index];
  }
  const auto reordered_path = temporary.path() / "reordered.ilb";
  write_file(reordered_path, reordered);
  const auto reordered_result = itchlab::validate_interchange_file(reordered_path, true);
  REQUIRE_FALSE(reordered_result.valid());
  REQUIRE(reordered_result.error->code == itchlab::ErrorCode::invariant);
  REQUIRE(reordered_result.error->record_index == 1);
}

TEST_CASE("TASK-015 deep snapshot validation rejects non-canonical depth validity",
          "[TASK-015][validation][snapshots][depth]") {
  TemporaryDirectory temporary;
  auto golden = read_file(repository_path("tests/golden/interchange/synthetic_snapshots_v1.ilb"));
  constexpr std::size_t records_offset = 104 + 2 * 16;
  golden[records_offset + 48] = 0;
  const auto path = temporary.path() / "invalid-depth.ilb";
  write_file(path, golden);

  const auto result = itchlab::validate_interchange_file(path, true);
  REQUIRE_FALSE(result.valid());
  REQUIRE(result.error->code == itchlab::ErrorCode::invariant);
  REQUIRE(result.error->record_index == 0);
}
