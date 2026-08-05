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
        std::filesystem::temp_directory_path() / ("itchlab-task007-" + std::to_string(timestamp) +
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
                                          const std::filesystem::path& input,
                                          const std::string_view symbol = "AAPL") {
  auto config = Json::parse(read_file(repository_path("configs/replay.diagnostic.example.json")));
  config["input"]["path"] = input.string();
  config["selection"]["symbols"] = Json::array({symbol});
  std::ofstream stream{destination, std::ios::binary};
  REQUIRE(stream.good());
  stream << config.dump(2) << '\n';
  stream.close();
  REQUIRE_FALSE(stream.fail());
  return destination;
}

std::filesystem::path write_session_replay_config(const std::filesystem::path& destination,
                                                  const std::filesystem::path& input) {
  auto config = Json::parse(read_file(repository_path("configs/replay.diagnostic.example.json")));
  config["input"]["path"] = input.string();
  config["selection"]["symbols"] = Json::array({"MSFT", "AAPL"});
  config["selection"]["session_start_ns"] = 34'200'000'000'000ULL;
  config["selection"]["session_end_ns"] = 34'200'000'010'000ULL;
  config["selection"]["require_trading_state"] = true;
  config["output"]["emit_unchanged_trade_snapshots"] = true;
  std::ofstream stream{destination, std::ios::binary};
  REQUIRE(stream.good());
  stream << config.dump(2) << '\n';
  stream.close();
  REQUIRE_FALSE(stream.fail());
  return destination;
}

std::vector<Json> read_jsonl(const std::filesystem::path& path) {
  std::istringstream stream{read_file(path)};
  std::vector<Json> rows;
  for (std::string line; std::getline(stream, line);) {
    rows.push_back(Json::parse(line));
  }
  return rows;
}

} // namespace

TEST_CASE("TASK-007 inspect JSON reports exact bounded source statistics",
          "[TASK-007][CLI][inspect][contract]") {
  const auto result = run_command(
      {"inspect", "--input", repository_path("tests/fixtures/synthetic_minimal.itch").string(),
       "--all", "--symbols", "aapl", "--format", "json"});

  REQUIRE(result.exit_code == 0);
  REQUIRE(result.error.empty());
  const auto envelope = Json::parse(result.output);
  REQUIRE(envelope.at("schema_version") == 1);
  REQUIRE(envelope.at("command") == "inspect");
  REQUIRE(envelope.at("status") == "completed");
  REQUIRE(envelope.at("summary").at("compression") == "none");
  REQUIRE(envelope.at("summary").at("framing") == "itch-length-v1");
  REQUIRE(envelope.at("summary").at("messages_examined") == 9);
  REQUIRE(envelope.at("summary").at("counts_by_type") ==
          Json{{"A", 1}, {"D", 1}, {"R", 1}, {"S", 6}});
  REQUIRE(envelope.at("summary").at("first_timestamp_ns") == 1000);
  REQUIRE(envelope.at("summary").at("last_timestamp_ns") == 72'000'000'001'000ULL);
  REQUIRE(envelope.at("summary").at("stock_directory_count") == 1);
  REQUIRE(envelope.at("summary").at("requested_symbols_found") == Json::array({"AAPL"}));
  REQUIRE(envelope.at("warnings").empty());
}

TEST_CASE("TASK-007 inspect limit and malformed input preserve channel and exit contracts",
          "[TASK-007][CLI][inspect][error]") {
  const auto bounded = run_command(
      {"inspect", "--input", repository_path("tests/fixtures/synthetic_minimal.itch.gz").string(),
       "--limit", "2", "--symbols", "MSFT", "--format", "json"});
  REQUIRE(bounded.exit_code == 0);
  REQUIRE(bounded.error.empty());
  const auto bounded_envelope = Json::parse(bounded.output);
  REQUIRE_FALSE(bounded_envelope.at("summary").at("input_complete").get<bool>());
  REQUIRE(bounded_envelope.at("warnings").size() == 2);

  const auto invalid_limit =
      run_command({"inspect", "--input", "unused", "--limit", "0", "--format", "json"});
  REQUIRE(invalid_limit.exit_code == 2);
  REQUIRE(invalid_limit.error.empty());
  REQUIRE(Json::parse(invalid_limit.output).at("error").at("code") == "ERR_CONFIG_SCHEMA");

  const auto truncated = run_command(
      {"inspect", "--input",
       repository_path("tests/fixtures/corrupt/synthetic_corrupt_truncated_payload.itch").string(),
       "--all", "--format", "json"});
  REQUIRE(truncated.exit_code == 3);
  REQUIRE(truncated.error.empty());
  const auto truncated_envelope = Json::parse(truncated.output);
  REQUIRE(truncated_envelope.at("error").at("code") == "ERR_TRUNCATED_MESSAGE");
  REQUIRE(truncated_envelope.at("error").at("context").at("message_index") == 0);
  REQUIRE(truncated_envelope.at("error").at("context").at("source_offset") == 0);
}

TEST_CASE("TASK-007 human and JSONL diagnostic channels remain separated",
          "[TASK-007][CLI][channels]") {
  const auto bounded = run_command(
      {"inspect", "--input", repository_path("tests/fixtures/synthetic_minimal.itch").string(),
       "--limit", "2", "--symbols", "MSFT", "--log-format", "jsonl"});
  REQUIRE(bounded.exit_code == 0);
  REQUIRE(bounded.output.starts_with("Inspection completed.\n"));
  REQUIRE(bounded.output.find("Warning:") == std::string::npos);
  std::istringstream diagnostic_lines{bounded.error};
  std::string first_line;
  std::string second_line;
  REQUIRE(std::getline(diagnostic_lines, first_line));
  REQUIRE(std::getline(diagnostic_lines, second_line));
  REQUIRE(Json::parse(first_line).at("event_code") == "INSPECTION_BOUNDED");
  REQUIRE(Json::parse(second_line).at("event_code") == "SYMBOL_NOT_OBSERVED");

  const auto human_error = run_command({"inspect"});
  REQUIRE(human_error.exit_code == 2);
  REQUIRE(human_error.output.empty());
  REQUIRE(human_error.error.starts_with("ERR_CONFIG_SCHEMA:"));

  const auto decode_error = run_command(
      {"inspect", "--input",
       repository_path("tests/fixtures/corrupt/synthetic_corrupt_wrong_known_length.itch").string(),
       "--all", "--format", "json"});
  REQUIRE(decode_error.exit_code == 4);
  REQUIRE(decode_error.error.empty());
  REQUIRE(Json::parse(decode_error.output).at("error").at("code") == "ERR_MESSAGE_LENGTH");
}

TEST_CASE("TASK-007 CLI replay publishes exact deterministic provisional diagnostics",
          "[TASK-007][CLI][replay][golden]") {
  TemporaryDirectory plain_root;
  TemporaryDirectory gzip_root;
  const auto plain_config = write_replay_config(
      plain_root.path() / "replay.json", repository_path("tests/fixtures/synthetic_minimal.itch"));
  const auto gzip_config =
      write_replay_config(gzip_root.path() / "replay.json",
                          repository_path("tests/fixtures/synthetic_minimal.itch.gz"));
  const auto plain_output = plain_root.path() / "output";
  const auto gzip_output = gzip_root.path() / "output";

  const auto plain = run_command({"replay", "--config", plain_config.string(), "--output-root",
                                  plain_output.string(), "--format", "json"});
  const auto gzip = run_command({"replay", "--config", gzip_config.string(), "--output-root",
                                 gzip_output.string(), "--format", "json"});
  REQUIRE(plain.exit_code == 0);
  REQUIRE(gzip.exit_code == 0);
  REQUIRE(plain.error.empty());
  REQUIRE(gzip.error.empty());
  const auto envelope = Json::parse(plain.output);
  REQUIRE(envelope.at("summary").at("artefact_status") == "provisional_diagnostic");
  REQUIRE(envelope.at("summary").at("messages_processed") == 9);
  REQUIRE(envelope.at("summary").at("global_system_messages") == 6);
  REQUIRE(envelope.at("summary").at("directory_messages") == 1);
  REQUIRE(envelope.at("summary").at("selected_instrument_messages") == 2);
  REQUIRE(envelope.at("summary").at("filtered_instrument_messages") == 0);
  REQUIRE(envelope.at("summary").at("selected_events") == 2);
  REQUIRE(envelope.at("summary").at("snapshots_written") == 2);
  REQUIRE(envelope.at("summary").at("instruments").size() == 1);
  const auto& instrument = envelope.at("summary").at("instruments").front();
  REQUIRE(instrument.at("symbol") == "AAPL");
  REQUIRE(instrument.at("final_order_count") == 0);
  REQUIRE(instrument.at("final_trading_state") == "closed");
  REQUIRE(instrument.at("final_book_digest") ==
          "47213ce72b18bbb9fb839f064fb00c71d810d21c19e1fe74a9ed61162c0d2a6c");
  REQUIRE(envelope.at("summary").at("global_session_events").size() == 6);
  REQUIRE(envelope.at("warnings").size() == 1);

  const auto expected_events =
      read_file(repository_path("tests/golden/itch50/synthetic_minimal_diagnostic_events.jsonl"));
  const auto expected_snapshots = read_file(
      repository_path("tests/golden/itch50/synthetic_minimal_diagnostic_snapshots.jsonl"));
  REQUIRE(read_file(plain_output / "diagnostic-events.jsonl") == expected_events);
  REQUIRE(read_file(gzip_output / "diagnostic-events.jsonl") == expected_events);
  REQUIRE(read_file(plain_output / "diagnostic-snapshots.jsonl") == expected_snapshots);
  REQUIRE(read_file(gzip_output / "diagnostic-snapshots.jsonl") == expected_snapshots);

  const auto repeated = run_command({"replay", "--config", plain_config.string(), "--output-root",
                                     plain_output.string(), "--format", "json"});
  REQUIRE(repeated.exit_code == 6);
  REQUIRE(Json::parse(repeated.output).at("error").at("code") == "ERR_OUTPUT_PATH");
  REQUIRE(read_file(plain_output / "diagnostic-events.jsonl") == expected_events);
  REQUIRE(read_file(plain_output / "diagnostic-snapshots.jsonl") == expected_snapshots);
}

TEST_CASE("TASK-007 CLI replay failures never publish final diagnostic names",
          "[TASK-007][CLI][replay][failure]") {
  TemporaryDirectory root;
  const auto config =
      write_replay_config(root.path() / "unknown.json",
                          repository_path("tests/fixtures/synthetic_minimal.itch"), "MSFT");
  const auto output_root = root.path() / "output";

  const auto result = run_command({"replay", "--config", config.string(), "--output-root",
                                   output_root.string(), "--format", "json"});
  REQUIRE(result.exit_code == 5);
  REQUIRE(result.error.empty());
  REQUIRE(Json::parse(result.output).at("error").at("code") == "ERR_UNKNOWN_SYMBOL");
  REQUIRE_FALSE(std::filesystem::exists(output_root / "diagnostic-events.jsonl"));
  REQUIRE_FALSE(std::filesystem::exists(output_root / "diagnostic-snapshots.jsonl"));

  const auto safe_config = write_replay_config(
      root.path() / "safe.json", repository_path("tests/fixtures/synthetic_minimal.itch"));
  const auto broad = run_command({"replay", "--config", safe_config.string(), "--output-root",
                                  std::filesystem::current_path().string(), "--format", "json"});
  REQUIRE(broad.exit_code == 6);
  REQUIRE(Json::parse(broad.output).at("error").at("code") == "ERR_OUTPUT_PATH");
}

TEST_CASE("TASK-011 CLI replay publishes deterministic filtered multi-symbol diagnostics",
          "[TASK-011][CLI][replay][session]") {
  TemporaryDirectory plain_root;
  TemporaryDirectory gzip_root;
  const auto plain_config = write_session_replay_config(
      plain_root.path() / "replay.json", repository_path("tests/fixtures/synthetic_session.itch"));
  const auto gzip_config =
      write_session_replay_config(gzip_root.path() / "replay.json",
                                  repository_path("tests/fixtures/synthetic_session.itch.gz"));
  const auto plain_output = plain_root.path() / "output";
  const auto gzip_output = gzip_root.path() / "output";

  const auto plain = run_command({"replay", "--config", plain_config.string(), "--output-root",
                                  plain_output.string(), "--format", "json"});
  const auto gzip = run_command({"replay", "--config", gzip_config.string(), "--output-root",
                                 gzip_output.string(), "--format", "json"});
  REQUIRE(plain.exit_code == 0);
  REQUIRE(gzip.exit_code == 0);
  REQUIRE(plain.error.empty());
  REQUIRE(gzip.error.empty());

  const auto envelope = Json::parse(plain.output);
  const auto& summary = envelope.at("summary");
  REQUIRE(summary.at("messages_processed") == 25);
  REQUIRE(summary.at("selected_instrument_messages") == 13);
  REQUIRE(summary.at("filtered_instrument_messages") == 3);
  REQUIRE(summary.at("selected_events") == 12);
  REQUIRE(summary.at("snapshots_written") == 6);
  REQUIRE(summary.at("instruments").at(0).at("symbol") == "MSFT");
  REQUIRE(summary.at("instruments").at(0).at("symbol_id") == 1);
  REQUIRE(summary.at("instruments").at(1).at("symbol") == "AAPL");
  REQUIRE(summary.at("instruments").at(1).at("symbol_id") == 2);
  REQUIRE(summary.at("global_session_events").size() == 6);

  REQUIRE(read_file(plain_output / "diagnostic-events.jsonl") ==
          read_file(gzip_output / "diagnostic-events.jsonl"));
  REQUIRE(read_file(plain_output / "diagnostic-snapshots.jsonl") ==
          read_file(gzip_output / "diagnostic-snapshots.jsonl"));
  const auto events = read_jsonl(plain_output / "diagnostic-events.jsonl");
  const auto snapshots = read_jsonl(plain_output / "diagnostic-snapshots.jsonl");
  REQUIRE(events.size() == 12);
  REQUIRE(snapshots.size() == 6);
  for (const auto& event : events) {
    REQUIRE(event.at("symbol") != "AMZN");
    REQUIRE(event.at("timestamp_ns").get<std::uint64_t>() < 34'200'000'010'000ULL);
  }
  for (const auto& snapshot : snapshots) {
    REQUIRE(snapshot.at("symbol") != "AMZN");
    REQUIRE(snapshot.at("timestamp_ns").get<std::uint64_t>() >= 34'200'000'000'000ULL);
    REQUIRE(snapshot.at("timestamp_ns").get<std::uint64_t>() < 34'200'000'010'000ULL);
  }
}
