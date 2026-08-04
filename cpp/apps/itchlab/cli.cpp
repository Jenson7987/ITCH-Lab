#include "itchlab/cli.hpp"

#include "itchlab/config/replay_config.hpp"
#include "itchlab/core/errors.hpp"
#include "itchlab/core/sha256.hpp"
#include "itchlab/input/source_factory.hpp"
#include "itchlab/inspect/source_inspector.hpp"
#include "itchlab/output/diagnostic_sinks.hpp"
#include "itchlab/replay/replay_coordinator.hpp"

#include <nlohmann/json.hpp>

#include <algorithm>
#include <cctype>
#include <charconv>
#include <cstdint>
#include <cstdlib>
#include <exception>
#include <filesystem>
#include <fstream>
#include <ios>
#include <iostream>
#include <iterator>
#include <map>
#include <optional>
#include <set>
#include <span>
#include <string>
#include <string_view>
#include <system_error>
#include <utility>
#include <vector>

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
         << "  replay     Replay one S/R/A/D symbol to provisional diagnostics.\n\n"
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
  output << "Replay one S/R/A/D symbol to provisional TASK-007 JSONL diagnostics.\n\n"
         << "Usage: itchlab replay --config <replay-config.json> [options]\n\n"
         << "Required:\n"
         << "  --config <path>             Version-1 replay configuration.\n\n"
         << "Options:\n"
         << "  --output-root <directory>   Diagnostic destination (default $ITCHLAB_RUNS_DIR or "
            "runs).\n"
         << "  --format <human|json>       Result format (default human).\n"
         << "  --force-new-run             Reserved for production replay; rejected by TASK-007.\n"
         << "  --quiet                     Suppress non-error progress.\n"
         << "  --log-format <human|jsonl>  Diagnostic log format (default human).\n"
         << "  --ascii                     Restrict presentation to ASCII.\n"
         << "  --no-colour                 Disable colour presentation.\n"
         << "  --help                      Show this help text.\n\n"
         << "Example:\n"
         << "  itchlab replay --config configs/replay.diagnostic.example.json --output-root "
            "runs/task-007\n\n"
         << "The output is not events.ilb, snapshots.ilb, or a completed replay manifest.\n"
         << "Exit categories: 0 success, 2 config, 3 input/framing, 4 decode, 5 book, 6 output.\n";
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
    } else if (argument == "--quiet" || argument == "--ascii" || argument == "--no-colour") {
      // TASK-007 output is already progress-free, ASCII and uncoloured.
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

[[nodiscard]] Json replay_summary_json(const ReplaySummary& summary,
                                       const JsonlDiagnosticSink& sink) {
  return Json{
      {"artefact_status", "provisional_diagnostic"},
      {"decoded_messages", summary.decoded_messages},
      {"event_path", display_path(sink.event_path())},
      {"final_book_digest", content_hash_to_hex(summary.final_book_digest)},
      {"final_order_count", summary.final_order_count},
      {"messages_processed", summary.messages_processed},
      {"selected_events", summary.selected_events},
      {"snapshot_path", display_path(sink.snapshot_path())},
      {"snapshots_written", summary.snapshots_written},
      {"source_bytes_consumed", summary.source_progress.source_bytes_consumed},
      {"stock_locate", summary.stock_locate},
      {"symbol", summary.symbol},
      {"symbol_id", summary.symbol_id},
      {"uncompressed_bytes_delivered", summary.source_progress.uncompressed_bytes_delivered},
  };
}

void render_replay_human(std::ostream& output, const ReplaySummary& summary,
                         const JsonlDiagnosticSink& sink) {
  output << "Diagnostic replay completed.\n"
         << "Artefact status: provisional diagnostic\n"
         << "Symbol: " << summary.symbol << '\n'
         << "Stock locate: " << summary.stock_locate << '\n'
         << "Messages processed: " << summary.messages_processed << '\n'
         << "Selected events: " << summary.selected_events << '\n'
         << "Snapshots written: " << summary.snapshots_written << '\n'
         << "Final order count: " << summary.final_order_count << '\n'
         << "Final book digest: " << content_hash_to_hex(summary.final_book_digest) << '\n'
         << "Diagnostic events: " << display_path(sink.event_path()) << '\n'
         << "Diagnostic snapshots: " << display_path(sink.snapshot_path()) << '\n';
}

void render_success_json(std::ostream& output, const std::string_view command, Json summary,
                         std::vector<std::string> warnings) {
  const Json envelope{
      {"command", command},
      {"schema_version", 1},
      {"status", "completed"},
      {"summary", std::move(summary)},
      {"warnings", std::move(warnings)},
  };
  output << envelope.dump(-1, ' ', false, Json::error_handler_t::strict) << '\n';
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
    render_success_json(output, "inspect", inspection_summary_json(*inspected.summary, input),
                        std::move(warnings));
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
               std::ostream& error) {
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
  if (options.force_new_run) {
    const auto failure =
        usage_error("--force-new-run is unavailable for TASK-007 provisional diagnostic replay.");
    return render_error(output, error, "replay", options.format, options.log_format, failure);
  }

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
  const auto& config = *config_result.config;
  if (config.selection.symbols.size() != 1 || config.validation.mode != ValidationMode::strict ||
      config.selection.require_trading_state || config.input.sha256) {
    const CommandError failure{
        ErrorCode::config_schema,
        "TASK-007 replay requires one symbol, strict mode, require_trading_state=false and a null "
        "input SHA-256.",
        "Use the diagnostic example config or wait for the production replay tasks."};
    return render_error(output, error, "replay", options.format, options.log_format, failure);
  }

  auto input = open_input_source(config.input.path);
  if (!input.valid()) {
    const CommandError failure{input.error->code, input.error->message,
                               default_action(input.error->code)};
    return render_error(output, error, "replay", options.format, options.log_format, failure);
  }
  auto diagnostics = open_jsonl_diagnostic_sink(options.output_root);
  if (!diagnostics.valid()) {
    const CommandError failure{diagnostics.error->code, diagnostics.error->message,
                               default_action(diagnostics.error->code)};
    return render_error(output, error, "replay", options.format, options.log_format, failure);
  }

  const ReplayCoordinator coordinator;
  const auto replayed = coordinator.run(*input.source, config, *diagnostics.sink);
  if (replayed.error) {
    return render_error(output, error, "replay", options.format, options.log_format,
                        replay_error(*replayed.error));
  }
  if (const auto publish_error = diagnostics.sink->publish()) {
    const CommandError failure{publish_error->code, publish_error->message,
                               default_action(publish_error->code)};
    return render_error(output, error, "replay", options.format, options.log_format, failure);
  }

  const std::vector<std::string> warnings{
      "TASK-007 files are provisional JSONL diagnostics, not production interchange or a completed "
      "replay manifest."};
  if (options.format == OutputFormat::json) {
    render_success_json(output, "replay", replay_summary_json(*replayed.summary, *diagnostics.sink),
                        warnings);
  } else {
    render_replay_human(output, *replayed.summary, *diagnostics.sink);
    for (const auto& warning : warnings) {
      render_warning(error, "replay", options.log_format, "PROVISIONAL_DIAGNOSTIC_OUTPUT", warning);
    }
  }
  return 0;
}

} // namespace

int run(const std::span<const std::string_view> arguments, std::ostream& output,
        std::ostream& error) {
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
      return run_replay(arguments.subspan(1), output, error);
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
                     "Report the failure; no completed diagnostic output was claimed."});
  }
}

} // namespace itchlab::cli
