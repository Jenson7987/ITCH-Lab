#include "itchlab/cli.hpp"

#include "itchlab/build_metadata.hpp"

#include "itchlab/config/replay_config.hpp"
#include "itchlab/core/errors.hpp"
#include "itchlab/core/sha256.hpp"
#include "itchlab/input/source_factory.hpp"
#include "itchlab/inspect/source_inspector.hpp"
#include "itchlab/output/diagnostic_sinks.hpp"
#include "itchlab/output/event_writer.hpp"
#include "itchlab/output/manifest.hpp"
#include "itchlab/output/snapshot_writer.hpp"
#include "itchlab/replay/replay_coordinator.hpp"

#include <nlohmann/json.hpp>

#include <algorithm>
#include <array>
#include <cctype>
#include <charconv>
#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <ctime>
#include <exception>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <ios>
#include <iostream>
#include <iterator>
#include <map>
#include <memory>
#include <optional>
#include <set>
#include <span>
#include <sstream>
#include <string>
#include <string_view>
#include <system_error>
#include <utility>
#include <vector>

#if defined(__APPLE__)
#include <mach-o/dyld.h>
#elif defined(__linux__)
#include <unistd.h>
#endif

namespace itchlab::cli {
namespace {

using Json = nlohmann::json;

constexpr std::string_view kProgramName{"itchlab"};
constexpr std::uint64_t kDefaultInspectLimit = 1'000'000;
constexpr std::uintmax_t kMaximumConfigBytes = 1U << 20U;

enum class OutputFormat : std::uint8_t {
  human,
  json,
};

enum class LogFormat : std::uint8_t {
  human,
  jsonl,
};

struct CommandError {
  ErrorCode code{ErrorCode::internal};
  std::string message;
  std::string action;
  Json context{Json::object()};
};

struct InspectArguments {
  std::filesystem::path input;
  std::optional<std::uint64_t> limit{kDefaultInspectLimit};
  std::vector<std::string> symbols;
  ValidationMode mode{ValidationMode::strict};
  OutputFormat format{OutputFormat::human};
  LogFormat log_format{LogFormat::human};
};

struct ReplayArguments {
  std::filesystem::path config;
  std::filesystem::path output_root{"runs"};
  OutputFormat format{OutputFormat::human};
  LogFormat log_format{LogFormat::human};
  bool force_new_run{};
  bool quiet{};
};

template <typename T> struct ParsedArguments {
  std::optional<T> value;
  std::optional<CommandError> error;
};

[[nodiscard]] CommandError usage_error(std::string message) {
  return CommandError{ErrorCode::config_schema, std::move(message),
                      "Run the command with --help and correct the arguments."};
}

[[nodiscard]] int exit_code(const ErrorCode code) noexcept {
  switch (code) {
  case ErrorCode::config_schema:
  case ErrorCode::schema_version:
  case ErrorCode::trading_date:
  case ErrorCode::session_window:
  case ErrorCode::timezone:
  case ErrorCode::depth:
  case ErrorCode::horizon:
  case ErrorCode::partition:
  case ErrorCode::row_stride:
  case ErrorCode::seed:
    return 2;
  case ErrorCode::input_path:
  case ErrorCode::unsupported_compression:
  case ErrorCode::framing:
  case ErrorCode::truncated_message:
  case ErrorCode::empty_input:
    return 3;
  case ErrorCode::message_length:
  case ErrorCode::unknown_message:
  case ErrorCode::timestamp:
    return 4;
  case ErrorCode::unknown_symbol:
  case ErrorCode::order_reference:
  case ErrorCode::quantity:
  case ErrorCode::price:
  case ErrorCode::book_crossed:
  case ErrorCode::invariant:
    return 5;
  case ErrorCode::output_path:
  case ErrorCode::disk_write:
  case ErrorCode::run_exists:
    return 6;
  case ErrorCode::hash_mismatch:
  case ErrorCode::partial_artefact:
    return 7;
  case ErrorCode::empty_dataset:
  case ErrorCode::leakage_guard:
  case ErrorCode::model_training:
  case ErrorCode::prediction_key:
    return 8;
  case ErrorCode::latency:
  case ErrorCode::cost:
  case ErrorCode::queue_state:
  case ErrorCode::inventory_limit:
  case ErrorCode::simulation_anomaly:
  case ErrorCode::broken_sim_fill:
    return 9;
  case ErrorCode::cancelled:
    return 130;
  case ErrorCode::internal:
    return 70;
  }
  return 70;
}

[[nodiscard]] std::string default_action(const ErrorCode code) {
  switch (exit_code(code)) {
  case 2:
    return "Correct the command or configuration and rerun.";
  case 3:
  case 4:
    return "Verify the local source file and its framing.";
  case 5:
    return "Inspect the symbol directory or offending order lifecycle.";
  case 6:
    return "Choose a fresh writable output root and rerun.";
  default:
    return "Review the diagnostic context and rerun after correcting the cause.";
  }
}

[[nodiscard]] bool wants_json(const std::span<const std::string_view> arguments) noexcept {
  for (std::size_t index = 0; index + 1 < arguments.size(); ++index) {
    if (arguments[index] == "--format" && arguments[index + 1] == "json") {
      return true;
    }
  }
  return false;
}

[[nodiscard]] bool wants_jsonl_logs(const std::span<const std::string_view> arguments) noexcept {
  for (std::size_t index = 0; index + 1 < arguments.size(); ++index) {
    if (arguments[index] == "--log-format" && arguments[index + 1] == "jsonl") {
      return true;
    }
  }
  return false;
}

void print_global_help(std::ostream& output) {
  output << "Offline Nasdaq ITCH research platform\n\n"
         << "Usage: itchlab <command> [options]\n\n"
         << "Commands:\n"
         << "  inspect    Inspect bounded source framing and message composition.\n"
         << "  replay     Replay selected symbols to immutable binary artefacts.\n\n"
         << "Global options:\n"
         << "  --help       Show this help text.\n"
         << "  --version    Show the application version.\n\n"
         << "This is an offline historical-data research tool, not a live-trading system.\n";
}

void print_inspect_help(std::ostream& output) {
  output
      << "Inspect source framing and bounded message statistics without writing data.\n\n"
      << "Usage: itchlab inspect --input <path> [options]\n\n"
      << "Required:\n"
      << "  --input <path>              Local gzip or uncompressed ITCH source.\n\n"
      << "Options:\n"
      << "  --limit <positive-integer>  Examine at most this many messages (default 1000000).\n"
      << "  --all                       Examine through validated end of input.\n"
      << "  --symbols <AAPL,MSFT>       Find exact source symbols after ASCII uppercasing.\n"
      << "  --mode <strict|permissive>  Decoder-error policy (default strict).\n"
      << "  --format <human|json>       Result format (default human).\n"
      << "  --quiet                     Suppress non-error progress.\n"
      << "  --log-format <human|jsonl>  Diagnostic log format (default human).\n"
      << "  --ascii                     Restrict presentation to ASCII.\n"
      << "  --no-colour                 Disable colour presentation.\n"
      << "  --help                      Show this help text.\n\n"
      << "Example:\n"
      << "  itchlab inspect --input tests/fixtures/synthetic_minimal.itch --all --symbols AAPL\n\n"
      << "Exit categories: 0 success, 2 usage, 3 input/framing, 4 decode, 5 symbol/domain.\n";
}

void print_replay_help(std::ostream& output) {
  output
      << "Replay selected symbols to versioned binary events, snapshots and a manifest.\n\n"
      << "Usage: itchlab replay --config <replay-config.json> [options]\n\n"
      << "Required:\n"
      << "  --config <path>             Version-1 replay configuration.\n\n"
      << "Options:\n"
      << "  --output-root <directory>   Run destination (default $ITCHLAB_RUNS_DIR or "
         "runs).\n"
      << "  --format <human|json>       Result format (default human).\n"
      << "  --force-new-run             Publish a new timestamped run for the same identity.\n"
      << "  --quiet                     Suppress non-error progress.\n"
      << "  --log-format <human|jsonl>  Diagnostic log format (default human).\n"
      << "  --ascii                     Restrict presentation to ASCII.\n"
      << "  --no-colour                 Disable colour presentation.\n"
      << "  --help                      Show this help text.\n\n"
      << "Example:\n"
      << "  itchlab replay --config configs/replay.diagnostic.example.json --output-root runs\n\n"
      << "Exit categories: 0 success, 2 config, 3 input/framing, 4 decode, 5 book, 6 output, "
         "130 cancellation.\n";
}

[[nodiscard]] std::optional<std::string_view>
required_value(const std::span<const std::string_view> arguments, std::size_t& index,
               const std::string_view option, CommandError& error) {
  if (index + 1 >= arguments.size() || arguments[index + 1].starts_with("--")) {
    error = usage_error(std::string{option} + " requires a value.");
    return std::nullopt;
  }
  ++index;
  return arguments[index];
}

[[nodiscard]] std::optional<std::uint64_t> parse_positive_integer(const std::string_view value) {
  std::uint64_t parsed{};
  const auto result = std::from_chars(value.data(), value.data() + value.size(), parsed);
  if (result.ec != std::errc{} || result.ptr != value.data() + value.size() || parsed == 0) {
    return std::nullopt;
  }
  return parsed;
}

[[nodiscard]] bool valid_symbol(const std::string_view symbol) noexcept {
  if (symbol.empty() || symbol.size() > 8 || symbol.front() == ' ' || symbol.back() == ' ') {
    return false;
  }
  return std::all_of(symbol.begin(), symbol.end(), [](const char character) {
    const auto byte = static_cast<unsigned char>(character);
    return byte >= 0x20U && byte <= 0x7eU;
  });
}

[[nodiscard]] std::optional<std::vector<std::string>> parse_symbols(const std::string_view value) {
  std::vector<std::string> symbols;
  std::set<std::string> unique;
  std::size_t start{};
  while (start <= value.size()) {
    const auto separator = value.find(',', start);
    const auto end = separator == std::string_view::npos ? value.size() : separator;
    auto symbol = std::string{value.substr(start, end - start)};
    std::transform(symbol.begin(), symbol.end(), symbol.begin(), [](const char character) {
      return static_cast<char>(std::toupper(static_cast<unsigned char>(character)));
    });
    if (!valid_symbol(symbol) || !unique.insert(symbol).second) {
      return std::nullopt;
    }
    symbols.push_back(std::move(symbol));
    if (separator == std::string_view::npos) {
      break;
    }
    start = separator + 1;
  }
  return symbols;
}

[[nodiscard]] ParsedArguments<InspectArguments>
parse_inspect_arguments(const std::span<const std::string_view> arguments) {
  InspectArguments parsed;
  bool saw_input = false;
  bool saw_limit = false;
  bool saw_all = false;
  bool saw_symbols = false;
  bool saw_mode = false;
  bool saw_format = false;
  bool saw_log_format = false;

  for (std::size_t index = 0; index < arguments.size(); ++index) {
    const auto argument = arguments[index];
    CommandError error;
    if (argument == "--input") {
      if (saw_input) {
        return {std::nullopt, usage_error("--input may be supplied only once.")};
      }
      const auto value = required_value(arguments, index, argument, error);
      if (!value) {
        return {std::nullopt, std::move(error)};
      }
      parsed.input = *value;
      saw_input = true;
    } else if (argument == "--limit") {
      if (saw_limit) {
        return {std::nullopt, usage_error("--limit may be supplied only once.")};
      }
      const auto value = required_value(arguments, index, argument, error);
      if (!value) {
        return {std::nullopt, std::move(error)};
      }
      const auto limit = parse_positive_integer(*value);
      if (!limit) {
        return {std::nullopt, usage_error("--limit must be a positive integer.")};
      }
      parsed.limit = *limit;
      saw_limit = true;
    } else if (argument == "--all") {
      if (saw_all) {
        return {std::nullopt, usage_error("--all may be supplied only once.")};
      }
      parsed.limit = std::nullopt;
      saw_all = true;
    } else if (argument == "--symbols") {
      if (saw_symbols) {
        return {std::nullopt, usage_error("--symbols may be supplied only once.")};
      }
      const auto value = required_value(arguments, index, argument, error);
      if (!value) {
        return {std::nullopt, std::move(error)};
      }
      const auto symbols = parse_symbols(*value);
      if (!symbols) {
        return {std::nullopt,
                usage_error("--symbols must contain unique comma-separated 1-8 byte symbols.")};
      }
      parsed.symbols = *symbols;
      saw_symbols = true;
    } else if (argument == "--mode") {
      if (saw_mode) {
        return {std::nullopt, usage_error("--mode may be supplied only once.")};
      }
      const auto value = required_value(arguments, index, argument, error);
      if (!value) {
        return {std::nullopt, std::move(error)};
      }
      if (*value == "strict") {
        parsed.mode = ValidationMode::strict;
      } else if (*value == "permissive") {
        parsed.mode = ValidationMode::permissive;
      } else {
        return {std::nullopt, usage_error("--mode must be strict or permissive.")};
      }
      saw_mode = true;
    } else if (argument == "--format") {
      if (saw_format) {
        return {std::nullopt, usage_error("--format may be supplied only once.")};
      }
      const auto value = required_value(arguments, index, argument, error);
      if (!value) {
        return {std::nullopt, std::move(error)};
      }
      if (*value == "human") {
        parsed.format = OutputFormat::human;
      } else if (*value == "json") {
        parsed.format = OutputFormat::json;
      } else {
        return {std::nullopt, usage_error("--format must be human or json.")};
      }
      saw_format = true;
    } else if (argument == "--log-format") {
      if (saw_log_format) {
        return {std::nullopt, usage_error("--log-format may be supplied only once.")};
      }
      const auto value = required_value(arguments, index, argument, error);
      if (!value) {
        return {std::nullopt, std::move(error)};
      }
      if (*value != "human" && *value != "jsonl") {
        return {std::nullopt, usage_error("--log-format must be human or jsonl.")};
      }
      parsed.log_format = *value == "jsonl" ? LogFormat::jsonl : LogFormat::human;
      saw_log_format = true;
    } else if (argument == "--quiet" || argument == "--ascii" || argument == "--no-colour") {
      // TASK-007 output is already progress-free, ASCII and uncoloured.
    } else {
      return {std::nullopt, usage_error("Unrecognised inspect option: " + std::string{argument})};
    }
  }

  if (!saw_input) {
    return {std::nullopt, usage_error("inspect requires --input <path>.")};
  }
  if (saw_limit && saw_all) {
    return {std::nullopt, usage_error("--limit and --all are mutually exclusive.")};
  }
  return {std::move(parsed), std::nullopt};
}

[[nodiscard]] ParsedArguments<ReplayArguments>
parse_replay_arguments(const std::span<const std::string_view> arguments) {
  ReplayArguments parsed;
  if (const auto* environment_root = std::getenv("ITCHLAB_RUNS_DIR");
      environment_root != nullptr && environment_root[0] != '\0') {
    parsed.output_root = environment_root;
  }
  bool saw_config = false;
  bool saw_output_root = false;
  bool saw_format = false;
  bool saw_force = false;
  bool saw_log_format = false;
  bool saw_quiet = false;

  for (std::size_t index = 0; index < arguments.size(); ++index) {
    const auto argument = arguments[index];
    CommandError error;
    if (argument == "--config") {
      if (saw_config) {
        return {std::nullopt, usage_error("--config may be supplied only once.")};
      }
      const auto value = required_value(arguments, index, argument, error);
      if (!value) {
        return {std::nullopt, std::move(error)};
      }
      parsed.config = *value;
      saw_config = true;
    } else if (argument == "--output-root") {
      if (saw_output_root) {
        return {std::nullopt, usage_error("--output-root may be supplied only once.")};
      }
      const auto value = required_value(arguments, index, argument, error);
      if (!value) {
        return {std::nullopt, std::move(error)};
      }
      parsed.output_root = *value;
      saw_output_root = true;
    } else if (argument == "--format") {
      if (saw_format) {
        return {std::nullopt, usage_error("--format may be supplied only once.")};
      }
      const auto value = required_value(arguments, index, argument, error);
      if (!value) {
        return {std::nullopt, std::move(error)};
      }
      if (*value == "human") {
        parsed.format = OutputFormat::human;
      } else if (*value == "json") {
        parsed.format = OutputFormat::json;
      } else {
        return {std::nullopt, usage_error("--format must be human or json.")};
      }
      saw_format = true;
    } else if (argument == "--force-new-run") {
      if (saw_force) {
        return {std::nullopt, usage_error("--force-new-run may be supplied only once.")};
      }
      parsed.force_new_run = true;
      saw_force = true;
    } else if (argument == "--log-format") {
      if (saw_log_format) {
        return {std::nullopt, usage_error("--log-format may be supplied only once.")};
      }
      const auto value = required_value(arguments, index, argument, error);
      if (!value) {
        return {std::nullopt, std::move(error)};
      }
      if (*value != "human" && *value != "jsonl") {
        return {std::nullopt, usage_error("--log-format must be human or jsonl.")};
      }
      parsed.log_format = *value == "jsonl" ? LogFormat::jsonl : LogFormat::human;
      saw_log_format = true;
    } else if (argument == "--quiet") {
      if (saw_quiet) {
        return {std::nullopt, usage_error("--quiet may be supplied only once.")};
      }
      parsed.quiet = true;
      saw_quiet = true;
    } else if (argument == "--ascii" || argument == "--no-colour") {
      // Current presentation is already ASCII and uncoloured.
    } else {
      return {std::nullopt, usage_error("Unrecognised replay option: " + std::string{argument})};
    }
  }

  if (!saw_config) {
    return {std::nullopt, usage_error("replay requires --config <path>.")};
  }
  return {std::move(parsed), std::nullopt};
}

[[nodiscard]] std::optional<std::string> read_config_document(const std::filesystem::path& path) {
  std::error_code filesystem_error;
  if (!std::filesystem::is_regular_file(path, filesystem_error) || filesystem_error) {
    return std::nullopt;
  }
  const auto size = std::filesystem::file_size(path, filesystem_error);
  if (filesystem_error || size > kMaximumConfigBytes) {
    return std::nullopt;
  }
  std::ifstream stream{path, std::ios::binary};
  if (!stream.is_open()) {
    return std::nullopt;
  }
  return std::string{std::istreambuf_iterator<char>{stream}, std::istreambuf_iterator<char>{}};
}

[[nodiscard]] CommandError config_issues_error(const std::vector<ConfigIssue>& issues) {
  Json context = Json::object();
  auto issue_values = Json::array();
  for (const auto& issue : issues) {
    issue_values.push_back({{"code", error_code_name(issue.code)},
                            {"json_pointer", issue.json_pointer},
                            {"message", issue.message}});
  }
  context["issues"] = std::move(issue_values);
  const auto code = issues.empty() ? ErrorCode::config_schema : issues.front().code;
  return CommandError{code, "Replay configuration validation failed.",
                      "Correct every reported configuration issue and rerun.", std::move(context)};
}

[[nodiscard]] Json optional_integer(const std::optional<TimestampNs> value) {
  return value ? Json(*value) : Json(nullptr);
}

[[nodiscard]] Json inspection_summary_json(const InspectionSummary& summary,
                                           const InputOpenResult& input) {
  return Json{
      {"compression", input_compression_name(input.compression)},
      {"counts_by_type", summary.counts_by_type},
      {"first_timestamp_ns", optional_integer(summary.first_timestamp_ns)},
      {"framing", "itch-length-v1"},
      {"input_complete", summary.input_complete},
      {"last_timestamp_ns", optional_integer(summary.last_timestamp_ns)},
      {"messages_examined", summary.messages_examined},
      {"parse_errors_by_code", summary.parse_errors_by_code},
      {"requested_symbols_found", summary.requested_symbols_found},
      {"selected_counts_by_type", summary.selected_counts_by_type},
      {"source_bytes_consumed", summary.source_progress.source_bytes_consumed},
      {"source_size_bytes", input.source_size_bytes},
      {"stock_directory_count", summary.stock_directory_count},
      {"uncompressed_bytes_delivered", summary.source_progress.uncompressed_bytes_delivered},
  };
}

[[nodiscard]] std::string map_text(const std::map<std::string, std::uint64_t>& values) {
  if (values.empty()) {
    return "none";
  }
  std::string result;
  for (const auto& [key, value] : values) {
    if (!result.empty()) {
      result += ", ";
    }
    result += key + '=' + std::to_string(value);
  }
  return result;
}

[[nodiscard]] std::string vector_text(const std::vector<std::string>& values) {
  if (values.empty()) {
    return "none";
  }
  std::string result;
  for (const auto& value : values) {
    if (!result.empty()) {
      result += ", ";
    }
    result += value;
  }
  return result;
}

void render_inspection_human(std::ostream& output, const InspectionSummary& summary,
                             const InputOpenResult& input) {
  output << "Inspection completed.\n"
         << "Compression: " << input_compression_name(input.compression) << '\n'
         << "Framing: itch-length-v1\n"
         << "Input complete: " << (summary.input_complete ? "yes" : "no") << '\n'
         << "Source size bytes: " << input.source_size_bytes << '\n'
         << "Messages examined: " << summary.messages_examined << '\n'
         << "Counts by type: " << map_text(summary.counts_by_type) << '\n'
         << "First timestamp ns: "
         << (summary.first_timestamp_ns ? std::to_string(*summary.first_timestamp_ns) : "none")
         << '\n'
         << "Last timestamp ns: "
         << (summary.last_timestamp_ns ? std::to_string(*summary.last_timestamp_ns) : "none")
         << '\n'
         << "Stock Directory messages: " << summary.stock_directory_count << '\n'
         << "Requested symbols found: " << vector_text(summary.requested_symbols_found) << '\n'
         << "Selected counts by type: " << map_text(summary.selected_counts_by_type) << '\n'
         << "Parse errors by code: " << map_text(summary.parse_errors_by_code) << '\n';
}

[[nodiscard]] std::string display_path(const std::filesystem::path& path) {
  std::error_code filesystem_error;
  const auto relative =
      std::filesystem::relative(path, std::filesystem::current_path(), filesystem_error);
  if (!filesystem_error && !relative.empty()) {
    const auto text = relative.generic_string();
    if (!text.starts_with("../") && text != "..") {
      return text;
    }
  }
  return path.filename().generic_string();
}

[[nodiscard]] Json publication_summary_json(const Json& manifest,
                                            const std::filesystem::path& run_directory,
                                            const bool reused) {
  const auto& counts = manifest.at("counts");
  return Json{{"all_counts_by_type", counts.at("all_by_type")},
              {"artefact_status", "published"},
              {"counts", counts},
              {"decoded_messages", counts.at("decoded_messages")},
              {"directory_messages", counts.at("directory_messages")},
              {"error_counts_by_code", manifest.at("error_summary")},
              {"errors_observed", counts.at("errors_observed")},
              {"event_path", display_path(run_directory / "events.ilb")},
              {"filtered_instrument_messages", counts.at("filtered_instrument_messages")},
              {"global_session_events", manifest.at("global_session_events")},
              {"global_system_messages", counts.at("global_system_messages")},
              {"identity_sha256", manifest.at("identity_sha256")},
              {"instruments", manifest.at("instruments")},
              {"manifest_path", display_path(run_directory / "replay-manifest.json")},
              {"messages_processed", counts.at("messages_processed")},
              {"publishable", manifest.at("publishable")},
              {"replay_id", manifest.at("replay_id")},
              {"reused", reused},
              {"selected_counts_by_type", counts.at("selected_by_type")},
              {"selected_events", counts.at("selected_events")},
              {"selected_instrument_messages", counts.at("selected_instrument_messages")},
              {"skipped_messages", counts.at("skipped_messages")},
              {"snapshot_path", display_path(run_directory / "snapshots.ilb")},
              {"snapshots_written", counts.at("snapshots_written")}};
}

void render_replay_human(std::ostream& output, const Json& manifest,
                         const std::filesystem::path& run_directory, const bool reused) {
  const auto& counts = manifest.at("counts");
  output << (manifest.at("status") == "degraded" ? "Replay published DEGRADED.\n"
                                                 : "Replay published.\n")
         << "Reused: " << (reused ? "yes" : "no") << '\n'
         << "Replay ID: " << manifest.at("replay_id").get<std::string>() << '\n'
         << "Publishable: " << (manifest.at("publishable").get<bool>() ? "yes" : "no") << '\n'
         << "Messages processed: " << counts.at("messages_processed") << '\n'
         << "Selected events: " << counts.at("selected_events") << '\n'
         << "Snapshots written: " << counts.at("snapshots_written") << '\n'
         << "Events: " << display_path(run_directory / "events.ilb") << '\n'
         << "Snapshots: " << display_path(run_directory / "snapshots.ilb") << '\n'
         << "Manifest: " << display_path(run_directory / "replay-manifest.json") << '\n';
}

void render_success_json(std::ostream& output, const std::string_view command,
                         const std::string_view status, Json summary,
                         std::vector<std::string> warnings) {
  const Json envelope{
      {"command", command},
      {"schema_version", 1},
      {"status", status},
      {"summary", std::move(summary)},
      {"warnings", std::move(warnings)},
  };
  output << envelope.dump(-1, ' ', false, Json::error_handler_t::strict) << '\n';
}

void render_progress(std::ostream& error, const LogFormat log_format,
                     const ReplayProgress& progress) {
  if (log_format == LogFormat::jsonl) {
    const Json log_line{{"command", "replay"},
                        {"elapsed_ms", progress.elapsed_ms},
                        {"error_count", progress.error_count},
                        {"event_code", "PROGRESS"},
                        {"level", "info"},
                        {"messages", progress.messages},
                        {"selected_events", progress.selected_events},
                        {"source_bytes", progress.source_bytes},
                        {"stage", progress.stage}};
    error << log_line.dump(-1, ' ', false, Json::error_handler_t::strict) << '\n';
    return;
  }
  error << "Progress: stage=" << progress.stage << " messages=" << progress.messages
        << " source_bytes=" << progress.source_bytes
        << " selected_events=" << progress.selected_events << " elapsed_ms=" << progress.elapsed_ms
        << " errors=" << progress.error_count << '\n';
}

void render_cancellation_requested(std::ostream& error, const LogFormat log_format) {
  constexpr std::string_view message{"Cancellation requested; closing partial outputs"};
  if (log_format == LogFormat::jsonl) {
    const Json log_line{{"command", "replay"},
                        {"event_code", "CANCELLATION_REQUESTED"},
                        {"level", "warning"},
                        {"message", message}};
    error << log_line.dump(-1, ' ', false, Json::error_handler_t::strict) << '\n';
  } else {
    error << message << '\n';
  }
}

int render_cancelled(std::ostream& output, std::ostream& error, const OutputFormat format,
                     const LogFormat log_format, const ReplayError& cancellation) {
  if (format == OutputFormat::json) {
    Json context = Json::object();
    if (cancellation.runtime) {
      context = {
          {"error_count", cancellation.runtime->error_count},
          {"messages_processed", cancellation.runtime->messages_processed},
          {"selected_events", cancellation.runtime->selected_events},
          {"source_bytes_consumed", cancellation.runtime->source_progress.source_bytes_consumed},
          {"uncompressed_bytes_delivered",
           cancellation.runtime->source_progress.uncompressed_bytes_delivered}};
    }
    const Json envelope{
        {"command", "replay"},
        {"error",
         {{"action", "Start a fresh replay; automatic resume is unavailable."},
          {"code", error_code_name(ErrorCode::cancelled)},
          {"context", std::move(context)},
          {"message", "Replay was cancelled; any staged replay files remain partial."}}},
        {"schema_version", 1},
        {"status", "cancelled"},
    };
    output << envelope.dump(-1, ' ', false, Json::error_handler_t::strict) << '\n';
  } else if (log_format == LogFormat::jsonl) {
    const Json log_line{{"command", "replay"},
                        {"event_code", error_code_name(ErrorCode::cancelled)},
                        {"level", "warning"},
                        {"message", "Replay cancelled; any staged replay files remain partial."}};
    error << log_line.dump(-1, ' ', false, Json::error_handler_t::strict) << '\n';
  } else {
    error << "ERR_CANCELLED: Replay cancelled; any staged replay files remain partial.\n";
  }
  return 130;
}

int render_error(std::ostream& output, std::ostream& error, const std::string_view command,
                 const OutputFormat format, const LogFormat log_format,
                 const CommandError& failure) {
  if (format == OutputFormat::json) {
    const Json envelope{
        {"command", command},
        {"error",
         {{"action", failure.action},
          {"code", error_code_name(failure.code)},
          {"context", failure.context},
          {"message", failure.message}}},
        {"schema_version", 1},
        {"status", "failed"},
    };
    output << envelope.dump(-1, ' ', false, Json::error_handler_t::strict) << '\n';
  } else if (log_format == LogFormat::jsonl) {
    const Json log_line{
        {"action", failure.action},   {"command", command},
        {"context", failure.context}, {"event_code", error_code_name(failure.code)},
        {"level", "error"},           {"message", failure.message},
    };
    error << log_line.dump(-1, ' ', false, Json::error_handler_t::strict) << '\n';
  } else {
    error << error_code_name(failure.code) << ": " << failure.message << '\n';
    if (!failure.context.empty()) {
      error << "Context: " << failure.context.dump(-1, ' ', false, Json::error_handler_t::replace)
            << '\n';
    }
    error << failure.action << '\n';
  }
  return exit_code(failure.code);
}

void render_warning(std::ostream& error, const std::string_view command, const LogFormat log_format,
                    const std::string_view event_code, const std::string_view message) {
  if (log_format == LogFormat::jsonl) {
    const Json log_line{{"command", command},
                        {"event_code", event_code},
                        {"level", "warning"},
                        {"message", message}};
    error << log_line.dump(-1, ' ', false, Json::error_handler_t::strict) << '\n';
  } else {
    error << "Warning: " << message << '\n';
  }
}

[[nodiscard]] CommandError inspection_error(const InspectionError& failure) {
  Json context = Json::object();
  if (failure.message_index) {
    context["message_index"] = *failure.message_index;
  }
  if (failure.source_offset) {
    context["source_offset"] = *failure.source_offset;
  }
  if (failure.source_type) {
    context["source_type"] = std::string(1, static_cast<char>(*failure.source_type));
  }
  return CommandError{failure.code, failure.message, default_action(failure.code),
                      std::move(context)};
}

[[nodiscard]] CommandError replay_error(const ReplayError& failure) {
  Json context = Json::object();
  if (failure.message_index) {
    context["message_index"] = *failure.message_index;
  }
  if (failure.source_offset) {
    context["source_offset"] = *failure.source_offset;
  }
  if (failure.source_type) {
    context["source_type"] = std::string(1, static_cast<char>(*failure.source_type));
  }
  if (failure.order_reference) {
    context["order_reference"] = *failure.order_reference;
  }
  return CommandError{failure.code, failure.message, default_action(failure.code),
                      std::move(context)};
}

[[nodiscard]] CommandError write_error(const DiagnosticWriteError& failure) {
  return CommandError{failure.code, failure.message, default_action(failure.code)};
}

struct ReplayObservationTime {
  std::string timestamp;
  std::string run_timestamp;
};

class ReplayLockRelease final {
public:
  explicit ReplayLockRelease(std::filesystem::path path) : path_{std::move(path)} {}
  ReplayLockRelease(const ReplayLockRelease&) = delete;
  ReplayLockRelease& operator=(const ReplayLockRelease&) = delete;
  ~ReplayLockRelease() {
    std::error_code ignored;
    static_cast<void>(std::filesystem::remove(path_, ignored));
  }

private:
  std::filesystem::path path_;
};

[[nodiscard]] ReplayObservationTime observe_replay_time() {
  const auto now = std::chrono::system_clock::now();
  const auto since_epoch =
      std::chrono::duration_cast<std::chrono::nanoseconds>(now.time_since_epoch());
  const auto whole_seconds = std::chrono::duration_cast<std::chrono::seconds>(since_epoch);
  const auto fractional = since_epoch - whole_seconds;
  const auto seconds_time = std::chrono::system_clock::time_point{whole_seconds};
  const auto time = std::chrono::system_clock::to_time_t(seconds_time);
  std::tm utc{};
#if defined(_WIN32)
  static_cast<void>(gmtime_s(&utc, &time));
#else
  static_cast<void>(gmtime_r(&time, &utc));
#endif

  std::ostringstream readable;
  readable << std::put_time(&utc, "%Y-%m-%dT%H:%M:%S") << '.' << std::setfill('0') << std::setw(9)
           << fractional.count() << 'Z';
  std::ostringstream compact;
  compact << std::put_time(&utc, "%Y%m%dT%H%M%S") << '.' << std::setfill('0') << std::setw(9)
          << fractional.count() << 'Z';
  return ReplayObservationTime{readable.str(), compact.str()};
}

[[nodiscard]] std::filesystem::path
resolve_executable_path(const std::filesystem::path& supplied_path) {
  std::error_code filesystem_error;
  if (!supplied_path.empty()) {
    const auto resolved = std::filesystem::canonical(supplied_path, filesystem_error);
    if (!filesystem_error && std::filesystem::is_regular_file(resolved, filesystem_error) &&
        !filesystem_error) {
      return resolved;
    }
  }

#if defined(__APPLE__)
  std::uint32_t size{};
  static_cast<void>(_NSGetExecutablePath(nullptr, &size));
  std::vector<char> buffer(size);
  if (_NSGetExecutablePath(buffer.data(), &size) == 0) {
    const auto resolved = std::filesystem::canonical(buffer.data(), filesystem_error);
    if (!filesystem_error) {
      return resolved;
    }
  }
#elif defined(__linux__)
  std::vector<char> buffer(1U << 12U);
  while (buffer.size() <= (1U << 20U)) {
    const auto length = ::readlink("/proc/self/exe", buffer.data(), buffer.size());
    if (length < 0) {
      break;
    }
    const auto converted = static_cast<std::size_t>(length);
    if (converted < buffer.size()) {
      return std::filesystem::path{std::string_view{buffer.data(), converted}};
    }
    buffer.resize(buffer.size() * 2U);
  }
#endif
  return {};
}

[[nodiscard]] BuildMetadata compiled_build_metadata() {
  return BuildMetadata{ITCHLAB_VERSION,     ITCHLAB_GIT_REVISION,     ITCHLAB_GIT_DIRTY != 0,
                       ITCHLAB_COMPILER_ID, ITCHLAB_COMPILER_VERSION, ITCHLAB_TARGET,
                       ITCHLAB_BUILD_TYPE};
}

[[nodiscard]] TradingDate trading_date_number(const std::string_view value) {
  std::array<char, 8> digits{};
  std::size_t destination{};
  for (const char character : value) {
    if (character != '-') {
      digits[destination++] = character;
    }
  }
  TradingDate result{};
  static_cast<void>(std::from_chars(digits.data(), digits.data() + digits.size(), result));
  return result;
}

void close_partial_outputs(std::ostream& error, const LogFormat log_format, EventWriter* events,
                           SnapshotWriter* snapshots) {
  if (events != nullptr) {
    if (const auto close_error = events->close_partial()) {
      render_warning(error, "replay", log_format, "PARTIAL_CLOSE_FAILED", close_error->message);
    }
  }
  if (snapshots != nullptr) {
    if (const auto close_error = snapshots->close_partial()) {
      render_warning(error, "replay", log_format, "PARTIAL_CLOSE_FAILED", close_error->message);
    }
  }
}

int render_replay_publication(std::ostream& output, std::ostream& error,
                              const ReplayArguments& options, const std::string_view document,
                              const std::filesystem::path& run_directory, const bool reused) {
  const auto manifest = Json::parse(document);
  const auto status = manifest.at("status").get<std::string>();
  std::vector<std::string> warnings;
  if (status == "degraded") {
    warnings.emplace_back("DEGRADED: malformed or inconsistent messages were safely skipped; see "
                          "the manifest error summary.");
  }
  if (!manifest.at("publishable").get<bool>()) {
    warnings.emplace_back("Only clean Release builds are publishable; this run is marked "
                          "publishable=false.");
  }
  if (options.format == OutputFormat::json) {
    render_success_json(output, "replay", status,
                        publication_summary_json(manifest, run_directory, reused), warnings);
  } else {
    render_replay_human(output, manifest, run_directory, reused);
    for (std::size_t index = 0; index < warnings.size(); ++index) {
      const auto degraded_warning = status == "degraded" && index == 0;
      render_warning(error, "replay", options.log_format,
                     degraded_warning ? "REPLAY_DEGRADED" : "NON_PUBLISHABLE_BUILD",
                     warnings[index]);
    }
  }
  return 0;
}

int run_inspect(const std::span<const std::string_view> arguments, std::ostream& output,
                std::ostream& error) {
  if (std::find(arguments.begin(), arguments.end(), "--help") != arguments.end()) {
    print_inspect_help(output);
    return 0;
  }
  if (arguments.size() == 1 && arguments.front() == "--version") {
    output << kProgramName << ' ' << ITCHLAB_VERSION << '\n';
    return 0;
  }
  const auto parsed = parse_inspect_arguments(arguments);
  const auto requested_format = wants_json(arguments) ? OutputFormat::json : OutputFormat::human;
  const auto requested_log_format =
      wants_jsonl_logs(arguments) ? LogFormat::jsonl : LogFormat::human;
  if (parsed.error) {
    return render_error(output, error, "inspect", requested_format, requested_log_format,
                        *parsed.error);
  }
  const auto& options = *parsed.value;

  auto input = open_input_source(options.input);
  if (!input.valid()) {
    const CommandError failure{input.error->code, input.error->message,
                               default_action(input.error->code)};
    return render_error(output, error, "inspect", options.format, options.log_format, failure);
  }

  const auto inspected = inspect_source(
      *input.source, InspectionOptions{options.limit, options.symbols, options.mode});
  if (inspected.error) {
    return render_error(output, error, "inspect", options.format, options.log_format,
                        inspection_error(*inspected.error));
  }

  std::vector<std::string> warnings;
  if (!inspected.summary->input_complete) {
    warnings.emplace_back("Inspection stopped at the configured limit; complete framing and gzip "
                          "trailer validation were not claimed.");
    if (inspected.summary->requested_symbols_found.size() != options.symbols.size()) {
      warnings.emplace_back("At least one requested symbol was not observed within the bounded "
                            "inspection window.");
    }
  }
  if (options.format == OutputFormat::json) {
    render_success_json(output, "inspect", "completed",
                        inspection_summary_json(*inspected.summary, input), std::move(warnings));
  } else {
    render_inspection_human(output, *inspected.summary, input);
    for (std::size_t index = 0; index < warnings.size(); ++index) {
      const auto event_code = index == 0 ? "INSPECTION_BOUNDED" : "SYMBOL_NOT_OBSERVED";
      render_warning(error, "inspect", options.log_format, event_code, warnings[index]);
    }
  }
  return 0;
}

int run_replay(const std::span<const std::string_view> arguments, std::ostream& output,
               std::ostream& error, const RuntimeContext runtime,
               const ProgressClock& progress_clock) {
  if (std::find(arguments.begin(), arguments.end(), "--help") != arguments.end()) {
    print_replay_help(output);
    return 0;
  }
  if (arguments.size() == 1 && arguments.front() == "--version") {
    output << kProgramName << ' ' << ITCHLAB_VERSION << '\n';
    return 0;
  }
  const auto parsed = parse_replay_arguments(arguments);
  const auto requested_format = wants_json(arguments) ? OutputFormat::json : OutputFormat::human;
  const auto requested_log_format =
      wants_jsonl_logs(arguments) ? LogFormat::jsonl : LogFormat::human;
  if (parsed.error) {
    return render_error(output, error, "replay", requested_format, requested_log_format,
                        *parsed.error);
  }
  const auto& options = *parsed.value;

  const auto document = read_config_document(options.config);
  if (!document) {
    const CommandError failure{
        ErrorCode::config_schema,
        "Replay config is not a readable regular JSON file of at most 1 MiB.",
        "Correct the config path or file size and rerun."};
    return render_error(output, error, "replay", options.format, options.log_format, failure);
  }
  const auto config_result = parse_replay_config(*document);
  if (!config_result.valid()) {
    return render_error(output, error, "replay", options.format, options.log_format,
                        config_issues_error(config_result.issues));
  }
  const auto& requested_config = *config_result.config;

  if (runtime.cancellation.is_cancellation_requested()) {
    render_cancellation_requested(error, options.log_format);
    return render_cancelled(output, error, options.format, options.log_format,
                            ReplayError{ErrorCode::cancelled, "Replay cancellation was requested.",
                                        std::nullopt, std::nullopt, std::nullopt, std::nullopt,
                                        std::nullopt});
  }

  const auto started = observe_replay_time();
  const auto source =
      hash_file(requested_config.input.path, ErrorCode::input_path, runtime.cancellation);
  if (!source.valid()) {
    if (source.error->code == ErrorCode::cancelled) {
      render_cancellation_requested(error, options.log_format);
      return render_cancelled(output, error, options.format, options.log_format,
                              ReplayError{ErrorCode::cancelled, source.error->message, std::nullopt,
                                          std::nullopt, std::nullopt, std::nullopt, std::nullopt});
    }
    return render_error(output, error, "replay", options.format, options.log_format,
                        write_error(*source.error));
  }
  if (requested_config.input.sha256 && *requested_config.input.sha256 != source.file->sha256) {
    return render_error(
        output, error, "replay", options.format, options.log_format,
        CommandError{ErrorCode::hash_mismatch,
                     "The source SHA-256 does not match the configured expected value.",
                     "Verify the local source file and configured hash before rerunning."});
  }

  const auto executable_path = resolve_executable_path(runtime.executable_path);
  if (executable_path.empty()) {
    return render_error(
        output, error, "replay", options.format, options.log_format,
        CommandError{
            ErrorCode::internal, "The running executable path could not be resolved.",
            "Run the installed executable directly and report the failure if it persists."});
  }
  const auto executable =
      hash_file(executable_path, ErrorCode::hash_mismatch, runtime.cancellation);
  if (!executable.valid()) {
    if (executable.error->code == ErrorCode::cancelled) {
      render_cancellation_requested(error, options.log_format);
      return render_cancelled(output, error, options.format, options.log_format,
                              ReplayError{ErrorCode::cancelled, executable.error->message,
                                          std::nullopt, std::nullopt, std::nullopt, std::nullopt,
                                          std::nullopt});
    }
    return render_error(output, error, "replay", options.format, options.log_format,
                        write_error(*executable.error));
  }

  ReplayConfig effective_config = requested_config;
  effective_config.input.path =
      std::filesystem::path{requested_config.input.path}.filename().generic_string();
  effective_config.input.sha256 = source.file->sha256;
  const auto config_hashes = replay_config_hashes(effective_config);
  const auto identity = replay_identity_hash(
      source.file->sha256, config_hashes.identity_config_sha256, executable.file->sha256);
  const auto replay_id = started.run_timestamp + '-' + content_hash_to_hex(identity).substr(0, 12);

  auto input = open_input_source(requested_config.input.path);
  if (!input.valid()) {
    return render_error(
        output, error, "replay", options.format, options.log_format,
        CommandError{input.error->code, input.error->message, default_action(input.error->code)});
  }
  if (runtime.cancellation.is_cancellation_requested()) {
    render_cancellation_requested(error, options.log_format);
    return render_cancelled(output, error, options.format, options.log_format,
                            ReplayError{ErrorCode::cancelled, "Replay cancellation was requested.",
                                        std::nullopt, std::nullopt, std::nullopt, std::nullopt,
                                        std::nullopt});
  }
  auto preparation = prepare_replay_run(options.output_root, requested_config.input.path, identity,
                                        replay_id, options.force_new_run, runtime.cancellation);
  if (preparation.error) {
    if (preparation.error->code == ErrorCode::cancelled) {
      render_cancellation_requested(error, options.log_format);
      return render_cancelled(output, error, options.format, options.log_format,
                              ReplayError{ErrorCode::cancelled, preparation.error->message,
                                          std::nullopt, std::nullopt, std::nullopt, std::nullopt,
                                          std::nullopt});
    }
    return render_error(output, error, "replay", options.format, options.log_format,
                        write_error(*preparation.error));
  }
  if (preparation.existing) {
    if (runtime.cancellation.is_cancellation_requested()) {
      render_cancellation_requested(error, options.log_format);
      return render_cancelled(output, error, options.format, options.log_format,
                              ReplayError{ErrorCode::cancelled,
                                          "Replay cancellation was requested.", std::nullopt,
                                          std::nullopt, std::nullopt, std::nullopt, std::nullopt});
    }
    return render_replay_publication(output, error, options,
                                     preparation.existing->manifest_document,
                                     preparation.existing->paths.final_directory, true);
  }
  if (!preparation.paths) {
    return render_error(output, error, "replay", options.format, options.log_format,
                        CommandError{ErrorCode::internal,
                                     "Replay publication preparation returned no run path.",
                                     default_action(ErrorCode::internal)});
  }
  const auto& paths = *preparation.paths;
  const ReplayLockRelease release_lock{paths.lock_path};
  if (runtime.cancellation.is_cancellation_requested()) {
    render_cancellation_requested(error, options.log_format);
    return render_cancelled(output, error, options.format, options.log_format,
                            ReplayError{ErrorCode::cancelled, "Replay cancellation was requested.",
                                        std::nullopt, std::nullopt, std::nullopt, std::nullopt,
                                        std::nullopt});
  }
  const auto symbol_count =
      checked_integral_cast<std::uint16_t>(effective_config.selection.symbols.size());
  if (!symbol_count) {
    return render_error(output, error, "replay", options.format, options.log_format,
                        CommandError{ErrorCode::config_schema,
                                     "Replay symbol count exceeds interchange-v1 capacity.",
                                     default_action(ErrorCode::config_schema)});
  }
  auto events = open_event_writer(paths.event_path, *symbol_count);
  if (!events.valid()) {
    return render_error(output, error, "replay", options.format, options.log_format,
                        write_error(*events.error));
  }
  auto snapshots =
      open_snapshot_writer(paths.snapshot_path, *symbol_count, effective_config.output.depth);
  if (!snapshots.valid()) {
    close_partial_outputs(error, options.log_format, events.writer.get(), nullptr);
    return render_error(output, error, "replay", options.format, options.log_format,
                        write_error(*snapshots.error));
  }

  std::unique_ptr<ProgressReporter> progress;
  if (!options.quiet) {
    progress = std::make_unique<ProgressReporter>(
        progress_clock, [&error, log_format = options.log_format](const ReplayProgress& update) {
          render_progress(error, log_format, update);
        });
  }

  const ReplayCoordinator coordinator;
  const auto replayed = coordinator.run(*input.source, effective_config, *events.writer,
                                        *snapshots.writer, runtime.cancellation, progress.get());
  if (replayed.error) {
    if (replayed.error->code == ErrorCode::cancelled) {
      render_cancellation_requested(error, options.log_format);
    }
    close_partial_outputs(error, options.log_format, events.writer.get(), snapshots.writer.get());
    if (replayed.error->code == ErrorCode::cancelled) {
      return render_cancelled(output, error, options.format, options.log_format, *replayed.error);
    }
    return render_error(output, error, "replay", options.format, options.log_format,
                        replay_error(*replayed.error));
  }
  if (runtime.cancellation.is_cancellation_requested()) {
    render_cancellation_requested(error, options.log_format);
    close_partial_outputs(error, options.log_format, events.writer.get(), snapshots.writer.get());
    return render_cancelled(output, error, options.format, options.log_format,
                            ReplayError{ErrorCode::cancelled, "Replay cancellation was requested.",
                                        std::nullopt, std::nullopt, std::nullopt, std::nullopt,
                                        ReplayRuntimeContext{replayed.summary->messages_processed,
                                                             replayed.summary->selected_events,
                                                             replayed.summary->errors_observed,
                                                             replayed.summary->source_progress}});
  }

  const auto verified_source =
      hash_file(requested_config.input.path, ErrorCode::hash_mismatch, runtime.cancellation);
  if (!verified_source.valid() || *verified_source.file != *source.file) {
    close_partial_outputs(error, options.log_format, events.writer.get(), snapshots.writer.get());
    if (verified_source.error && verified_source.error->code == ErrorCode::cancelled) {
      render_cancellation_requested(error, options.log_format);
      return render_cancelled(output, error, options.format, options.log_format,
                              ReplayError{ErrorCode::cancelled, verified_source.error->message,
                                          std::nullopt, std::nullopt, std::nullopt, std::nullopt,
                                          std::nullopt});
    }
    return render_error(
        output, error, "replay", options.format, options.log_format,
        CommandError{ErrorCode::hash_mismatch,
                     "The source file changed or could not be verified after replay.",
                     "Restore the exact source bytes and start a fresh replay."});
  }

  std::vector<Instrument> instruments;
  instruments.reserve(replayed.summary->instruments.size());
  for (const auto& item : replayed.summary->instruments) {
    instruments.push_back(item.instrument);
  }
  const EventFileMetadata metadata{trading_date_number(effective_config.input.trading_date),
                                   std::move(instruments), replayed.summary->degraded,
                                   config_hashes.config_sha256, source.file->sha256};
  if (const auto finalise_error = events.writer->finalise(metadata)) {
    close_partial_outputs(error, options.log_format, events.writer.get(), snapshots.writer.get());
    return render_error(output, error, "replay", options.format, options.log_format,
                        write_error(*finalise_error));
  }
  if (const auto finalise_error = snapshots.writer->finalise(metadata)) {
    close_partial_outputs(error, options.log_format, events.writer.get(), snapshots.writer.get());
    return render_error(output, error, "replay", options.format, options.log_format,
                        write_error(*finalise_error));
  }

  const auto event_hash =
      hash_file(events.writer->partial_path(), ErrorCode::hash_mismatch, runtime.cancellation);
  const auto snapshot_hash =
      hash_file(snapshots.writer->partial_path(), ErrorCode::hash_mismatch, runtime.cancellation);
  if (!event_hash.valid() || !snapshot_hash.valid()) {
    const auto& hash_error = !event_hash.valid() ? *event_hash.error : *snapshot_hash.error;
    if (hash_error.code == ErrorCode::cancelled) {
      render_cancellation_requested(error, options.log_format);
      return render_cancelled(output, error, options.format, options.log_format,
                              ReplayError{ErrorCode::cancelled, hash_error.message, std::nullopt,
                                          std::nullopt, std::nullopt, std::nullopt, std::nullopt});
    }
    return render_error(output, error, "replay", options.format, options.log_format,
                        write_error(hash_error));
  }
  if (runtime.cancellation.is_cancellation_requested()) {
    render_cancellation_requested(error, options.log_format);
    return render_cancelled(output, error, options.format, options.log_format,
                            ReplayError{ErrorCode::cancelled, "Replay cancellation was requested.",
                                        std::nullopt, std::nullopt, std::nullopt, std::nullopt,
                                        ReplayRuntimeContext{replayed.summary->messages_processed,
                                                             replayed.summary->selected_events,
                                                             replayed.summary->errors_observed,
                                                             replayed.summary->source_progress}});
  }

  const auto completed = observe_replay_time();
  const auto manifest = build_replay_manifest(ReplayManifestInput{
      replay_id, identity, effective_config, config_hashes, *source.file,
      effective_config.input.path, input.compression, *executable.file, compiled_build_metadata(),
      started.timestamp, completed.timestamp, *replayed.summary, *event_hash.file,
      *snapshot_hash.file, events.writer->record_count(), snapshots.writer->record_count()});
  if (!manifest.valid()) {
    return render_error(output, error, "replay", options.format, options.log_format,
                        write_error(*manifest.error));
  }
  if (runtime.cancellation.is_cancellation_requested()) {
    render_cancellation_requested(error, options.log_format);
    return render_cancelled(output, error, options.format, options.log_format,
                            ReplayError{ErrorCode::cancelled, "Replay cancellation was requested.",
                                        std::nullopt, std::nullopt, std::nullopt, std::nullopt,
                                        ReplayRuntimeContext{replayed.summary->messages_processed,
                                                             replayed.summary->selected_events,
                                                             replayed.summary->errors_observed,
                                                             replayed.summary->source_progress}});
  }
  if (const auto publish_error = publish_replay_run(paths, *manifest.document)) {
    return render_error(output, error, "replay", options.format, options.log_format,
                        write_error(*publish_error));
  }
  return render_replay_publication(output, error, options, *manifest.document,
                                   paths.final_directory, false);
}

} // namespace

int run(const std::span<const std::string_view> arguments, std::ostream& output,
        std::ostream& error) {
  return run(arguments, output, error, RuntimeContext{});
}

int run(const std::span<const std::string_view> arguments, std::ostream& output,
        std::ostream& error, const RuntimeContext runtime) {
  const SteadyProgressClock default_progress_clock;
  const auto& progress_clock =
      runtime.progress_clock != nullptr ? *runtime.progress_clock : default_progress_clock;
  try {
    if (arguments.empty()) {
      print_global_help(output);
      return 0;
    }
    if (arguments.size() == 1 && arguments.front() == "--help") {
      print_global_help(output);
      return 0;
    }
    if (arguments.size() == 1 && arguments.front() == "--version") {
      output << kProgramName << ' ' << ITCHLAB_VERSION << '\n';
      return 0;
    }
    if (arguments.front() == "inspect") {
      return run_inspect(arguments.subspan(1), output, error);
    }
    if (arguments.front() == "replay") {
      return run_replay(arguments.subspan(1), output, error, runtime, progress_clock);
    }

    const auto format = wants_json(arguments) ? OutputFormat::json : OutputFormat::human;
    const auto log_format = wants_jsonl_logs(arguments) ? LogFormat::jsonl : LogFormat::human;
    return render_error(output, error, "itchlab", format, log_format,
                        usage_error("Unrecognised command: " + std::string{arguments.front()}));
  } catch (const std::exception&) {
    const auto format = wants_json(arguments) ? OutputFormat::json : OutputFormat::human;
    const auto log_format = wants_jsonl_logs(arguments) ? LogFormat::jsonl : LogFormat::human;
    return render_error(
        output, error, arguments.empty() ? "itchlab" : arguments.front(), format, log_format,
        CommandError{ErrorCode::internal, "Unexpected internal command failure.",
                     "Rerun with a valid local fixture; report the failure if it persists."});
  } catch (...) {
    const auto format = wants_json(arguments) ? OutputFormat::json : OutputFormat::human;
    const auto log_format = wants_jsonl_logs(arguments) ? LogFormat::jsonl : LogFormat::human;
    return render_error(
        output, error, arguments.empty() ? "itchlab" : arguments.front(), format, log_format,
        CommandError{ErrorCode::internal, "Unexpected non-standard command failure.",
                     "Report the failure; no completed output was claimed."});
  }
}

} // namespace itchlab::cli
