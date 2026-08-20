#include "itchlab/cli.hpp"
#include "itchlab/performance/benchmark.hpp"

#include <catch2/catch_test_macros.hpp>
#include <nlohmann/json.hpp>

#include <atomic>
#include <chrono>
#include <cstdint>
#include <filesystem>
#include <sstream>
#include <string>
#include <string_view>
#include <system_error>
#include <vector>

namespace {

using Json = nlohmann::json;

[[nodiscard]] std::filesystem::path repository_path(const std::string_view relative_path) {
  return std::filesystem::path{ITCHLAB_SOURCE_DIR} / relative_path;
}

class TemporaryDirectory {
public:
  TemporaryDirectory() {
    static std::atomic<std::uint64_t> sequence{};
    const auto timestamp = std::chrono::steady_clock::now().time_since_epoch().count();
    path_ =
        std::filesystem::temp_directory_path() / ("itchlab-task029-" + std::to_string(timestamp) +
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

[[nodiscard]] CommandResult run_command(const std::vector<std::string>& owned_arguments) {
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

} // namespace

TEST_CASE("TASK-029 robust benchmark statistics use medians and MAD",
          "[TASK-029][PERF][statistics]") {
  REQUIRE(itchlab::benchmark_median({1.0, 100.0, 3.0}) == 3.0);
  REQUIRE(itchlab::benchmark_median({1.0, 2.0, 4.0, 8.0}) == 3.0);
  REQUIRE(itchlab::benchmark_mad({1.0, 3.0, 5.0}, 3.0) == 2.0);
}

TEST_CASE("PERF-001 through PERF-004 and PERF-006 preserve the final book digest",
          "[TASK-029][PERF-001][PERF-002][PERF-003][PERF-004][PERF-006]") {
  const auto result = itchlab::run_benchmarks(
      itchlab::BenchmarkOptions{repository_path("tests/fixtures/synthetic_mixed.itch"),
                                itchlab::BenchmarkStage::all,
                                {"AAPL"},
                                3});
  REQUIRE(result.valid());
  REQUIRE(result.report->measurements.size() == 5);
  REQUIRE(result.report->measurements[0].id == "PERF-001");
  REQUIRE(result.report->measurements[1].id == "PERF-002");
  REQUIRE(result.report->measurements[2].id == "PERF-003");
  REQUIRE(result.report->measurements[3].id == "PERF-004");
  REQUIRE(result.report->measurements[4].id == "PERF-006");
  const auto& book = result.report->measurements[3];
  REQUIRE(book.samples.front().messages == 31);
  REQUIRE(book.samples.front().selected_messages == 13);
  REQUIRE(book.samples.front().operations == 8);
  REQUIRE(book.final_book_digests.at("AAPL") ==
          "47213ce72b18bbb9fb839f064fb00c71d810d21c19e1fe74a9ed61162c0d2a6c");
  const auto& writer = result.report->measurements[4];
  REQUIRE(writer.median_operations_per_second > 0.0);
  REQUIRE(writer.median_output_bytes_per_operation == 328.0);
}

TEST_CASE("PERF-005 reports compressed and uncompressed gzip rates", "[TASK-029][PERF-005][gzip]") {
  const auto result = itchlab::run_benchmarks(
      itchlab::BenchmarkOptions{repository_path("tests/fixtures/synthetic_mixed.itch.gz"),
                                itchlab::BenchmarkStage::all,
                                {"AAPL"},
                                3});
  REQUIRE(result.valid());
  REQUIRE(result.report->measurements.size() == 1);
  const auto& gzip = result.report->measurements.front();
  REQUIRE(gzip.id == "PERF-005");
  REQUIRE(gzip.median_source_bytes_per_second > 0.0);
  REQUIRE(gzip.median_uncompressed_bytes_per_second > 0.0);
  REQUIRE(gzip.final_book_digests.at("AAPL") ==
          "47213ce72b18bbb9fb839f064fb00c71d810d21c19e1fe74a9ed61162c0d2a6c");
}

TEST_CASE("TASK-029 benchmark CLI validates arguments and publishes immutable JSON evidence",
          "[TASK-029][CLI][PERF][output]") {
  TemporaryDirectory temporary;
  const auto fixture = repository_path("tests/fixtures/synthetic_mixed.itch");
  const auto evidence = temporary.path() / "benchmark.json";
  const auto completed =
      run_command({"benchmark", "--fixture", fixture.string(), "--stage", "book", "--repetitions",
                   "3", "--output", evidence.string(), "--format", "json"});
  REQUIRE(completed.exit_code == 0);
  REQUIRE(completed.error.empty());
  REQUIRE(std::filesystem::is_regular_file(evidence));
  const auto envelope = Json::parse(completed.output);
  REQUIRE(envelope.at("command") == "benchmark");
  REQUIRE(envelope.at("status") == "completed");
  REQUIRE(envelope.at("summary").at("measurements").front().at("id") == "PERF-004");
  REQUIRE(envelope.at("summary").at("environment").contains("hardware"));
  REQUIRE(envelope.at("summary").at("fixture").contains("sha256"));

  const auto refused =
      run_command({"benchmark", "--fixture", fixture.string(), "--stage", "book", "--repetitions",
                   "3", "--output", evidence.string(), "--format", "json"});
  REQUIRE(refused.exit_code == 6);
  REQUIRE(Json::parse(refused.output).at("error").at("code") == "ERR_RUN_EXISTS");

  const auto dangling_output = temporary.path() / "dangling.json";
  std::error_code symlink_error;
  std::filesystem::create_symlink(temporary.path() / "absent-target", dangling_output,
                                  symlink_error);
  REQUIRE_FALSE(symlink_error);
  const auto refused_symlink =
      run_command({"benchmark", "--fixture", fixture.string(), "--stage", "book", "--repetitions",
                   "3", "--output", dangling_output.string(), "--format", "json"});
  REQUIRE(refused_symlink.exit_code == 6);
  REQUIRE(Json::parse(refused_symlink.output).at("error").at("code") == "ERR_RUN_EXISTS");

  const auto invalid = run_command({"benchmark", "--fixture", fixture.string(), "--stage", "all",
                                    "--repetitions", "2", "--format", "json"});
  REQUIRE(invalid.exit_code == 2);
  REQUIRE(Json::parse(invalid.output).at("error").at("code") == "ERR_CONFIG_SCHEMA");
}
