#include "itchlab/output/diagnostic_sinks.hpp"

#include "itchlab/core/sha256.hpp"

#include <nlohmann/json.hpp>

#include <cstdint>
#include <exception>
#include <filesystem>
#include <fstream>
#include <optional>
#include <string>
#include <string_view>
#include <system_error>
#include <utility>
#include <vector>

namespace itchlab {
namespace {

using Json = nlohmann::json;

constexpr std::string_view kDiagnosticFormat{"itchlab-task-011-diagnostic-v1"};
constexpr std::string_view kEventFilename{"diagnostic-events.jsonl"};
constexpr std::string_view kSnapshotFilename{"diagnostic-snapshots.jsonl"};

[[nodiscard]] DiagnosticWriteError output_error(std::string message) {
  return DiagnosticWriteError{ErrorCode::output_path, std::move(message)};
}

[[nodiscard]] DiagnosticWriteError write_error(std::string message) {
  return DiagnosticWriteError{ErrorCode::disk_write, std::move(message)};
}

[[nodiscard]] Json level_array(const std::vector<std::optional<AggregatedLevel>>& levels) {
  auto result = Json::array();
  for (const auto& level : levels) {
    if (!level) {
      result.push_back(nullptr);
    } else {
      result.push_back({{"price4", level->price4}, {"quantity", level->total_quantity}});
    }
  }
  return result;
}

[[nodiscard]] std::optional<DiagnosticWriteError> write_json_line(std::ofstream& stream,
                                                                  const Json& value) {
  try {
    stream << value.dump(-1, ' ', false, Json::error_handler_t::strict) << '\n';
  } catch (const std::exception&) {
    return write_error("Diagnostic JSON serialisation failed.");
  }
  if (!stream.good()) {
    return write_error("Diagnostic file write failed.");
  }
  return std::nullopt;
}

} // namespace

JsonlDiagnosticSink::JsonlDiagnosticSink(std::filesystem::path event_path,
                                         std::filesystem::path snapshot_path,
                                         std::filesystem::path event_partial_path,
                                         std::filesystem::path snapshot_partial_path,
                                         std::ofstream event_stream, std::ofstream snapshot_stream)
    : event_path_{std::move(event_path)}, snapshot_path_{std::move(snapshot_path)},
      event_partial_path_{std::move(event_partial_path)},
      snapshot_partial_path_{std::move(snapshot_partial_path)},
      event_stream_{std::move(event_stream)}, snapshot_stream_{std::move(snapshot_stream)} {}

std::optional<DiagnosticWriteError> JsonlDiagnosticSink::write_event(const DiagnosticEvent& event) {
  if (closed_) {
    return write_error("Cannot write an event after diagnostic output was closed.");
  }
  Json value{
      {"book_digest", content_hash_to_hex(event.book_digest)},
      {"diagnostic_format", kDiagnosticFormat},
      {"event_kind", event.event_kind},
      {"in_session", event.in_session},
      {"message_index", event.message_index},
      {"source_offset", event.source_offset},
      {"source_type", std::string(1, event.source_type)},
      {"stock_locate", event.stock_locate},
      {"symbol", event.symbol},
      {"symbol_id", event.symbol_id},
      {"timestamp_ns", event.timestamp_ns},
  };
  if (event.primary_reference) {
    value["primary_reference"] = *event.primary_reference;
  }
  if (event.secondary_reference) {
    value["secondary_reference"] = *event.secondary_reference;
  }
  if (event.side) {
    value["side"] = static_cast<std::int8_t>(*event.side);
  }
  if (event.price4) {
    value["price4"] = *event.price4;
  }
  if (event.execution_price4) {
    value["execution_price4"] = *event.execution_price4;
  }
  if (event.quantity) {
    value["quantity"] = *event.quantity;
  }
  if (event.previous_remaining) {
    value["previous_remaining"] = *event.previous_remaining;
  }
  if (event.remaining_quantity) {
    value["remaining_quantity"] = *event.remaining_quantity;
  }
  if (event.aux_code) {
    value["aux_code"] = *event.aux_code;
  }
  if (event.event_subtype) {
    value["event_subtype"] = std::string(1, *event.event_subtype);
  }
  return write_json_line(event_stream_, value);
}

std::optional<DiagnosticWriteError>
JsonlDiagnosticSink::write_snapshot(const DiagnosticSnapshot& snapshot) {
  if (closed_) {
    return write_error("Cannot write a snapshot after diagnostic output was closed.");
  }
  Json value{
      {"asks", level_array(snapshot.top_levels.asks)},
      {"bids", level_array(snapshot.top_levels.bids)},
      {"book_digest", content_hash_to_hex(snapshot.book_digest)},
      {"depth", snapshot.depth},
      {"diagnostic_format", kDiagnosticFormat},
      {"event_kind", snapshot.event_kind},
      {"message_index", snapshot.message_index},
      {"stock_locate", snapshot.stock_locate},
      {"symbol", snapshot.symbol},
      {"symbol_id", snapshot.symbol_id},
      {"timestamp_ns", snapshot.timestamp_ns},
      {"top_n_changed", snapshot.top_n_changed},
      {"trading_state", trading_state_name(snapshot.trading_state)},
  };
  if (snapshot.event_price4) {
    value["event_price4"] = *snapshot.event_price4;
  }
  if (snapshot.event_quantity) {
    value["event_quantity"] = *snapshot.event_quantity;
  }
  return write_json_line(snapshot_stream_, value);
}

std::optional<DiagnosticWriteError> JsonlDiagnosticSink::close_streams() {
  if (closed_) {
    return std::nullopt;
  }

  event_stream_.flush();
  snapshot_stream_.flush();
  const auto flush_failed = !event_stream_.good() || !snapshot_stream_.good();
  event_stream_.close();
  snapshot_stream_.close();
  const auto close_failed = event_stream_.fail() || snapshot_stream_.fail();
  closed_ = true;
  if (flush_failed) {
    return write_error("Diagnostic file flush failed.");
  }
  if (close_failed) {
    return write_error("Diagnostic file close failed.");
  }
  return std::nullopt;
}

std::optional<DiagnosticWriteError> JsonlDiagnosticSink::close_partial() { return close_streams(); }

std::optional<DiagnosticWriteError> JsonlDiagnosticSink::publish() {
  if (published_) {
    return write_error("Diagnostic files have already been published.");
  }
  if (closed_) {
    return write_error("Closed partial diagnostic files cannot be published.");
  }
  if (const auto close_error = close_streams()) {
    return close_error;
  }

  std::error_code filesystem_error;
  std::filesystem::rename(event_partial_path_, event_path_, filesystem_error);
  if (filesystem_error) {
    return write_error("Could not publish the diagnostic event file.");
  }
  std::filesystem::rename(snapshot_partial_path_, snapshot_path_, filesystem_error);
  if (filesystem_error) {
    std::error_code rollback_error;
    std::filesystem::rename(event_path_, event_partial_path_, rollback_error);
    if (rollback_error) {
      return write_error(
          "Could not publish the diagnostic snapshot file or restore the event partial file.");
    }
    return write_error("Could not publish the diagnostic snapshot file.");
  }
  published_ = true;
  return std::nullopt;
}

DiagnosticSinkOpenResult open_jsonl_diagnostic_sink(const std::filesystem::path& output_root) {
  if (output_root.empty()) {
    return DiagnosticSinkOpenResult{nullptr, output_error("Output root is empty.")};
  }

  std::error_code filesystem_error;
  auto absolute_root = std::filesystem::absolute(output_root, filesystem_error).lexically_normal();
  if (filesystem_error) {
    return DiagnosticSinkOpenResult{nullptr, output_error("Output root could not be resolved.")};
  }
  if (absolute_root == absolute_root.root_path()) {
    return DiagnosticSinkOpenResult{nullptr,
                                    output_error("Filesystem root is not a safe output root.")};
  }

  if (std::filesystem::exists(absolute_root, filesystem_error)) {
    if (filesystem_error || !std::filesystem::is_directory(absolute_root, filesystem_error) ||
        filesystem_error) {
      return DiagnosticSinkOpenResult{nullptr,
                                      output_error("Output root is not a writable directory.")};
    }
  } else {
    std::filesystem::create_directories(absolute_root, filesystem_error);
    if (filesystem_error) {
      return DiagnosticSinkOpenResult{nullptr,
                                      output_error("Output root directory could not be created.")};
    }
  }

  const auto resolved_root = std::filesystem::weakly_canonical(absolute_root, filesystem_error);
  if (filesystem_error) {
    return DiagnosticSinkOpenResult{nullptr, output_error("Output root could not be resolved.")};
  }
  const auto current_directory = std::filesystem::current_path(filesystem_error);
  if (filesystem_error) {
    return DiagnosticSinkOpenResult{
        nullptr, output_error("Current working directory could not be resolved.")};
  }
  if (resolved_root == resolved_root.root_path() || resolved_root == current_directory) {
    return DiagnosticSinkOpenResult{
        nullptr, output_error("A filesystem or workspace root is not a safe output root.")};
  }

  const auto event_path = resolved_root / kEventFilename;
  const auto snapshot_path = resolved_root / kSnapshotFilename;
  auto event_partial_path = event_path;
  auto snapshot_partial_path = snapshot_path;
  event_partial_path += ".partial";
  snapshot_partial_path += ".partial";
  for (const auto& path : {event_path, snapshot_path, event_partial_path, snapshot_partial_path}) {
    if (std::filesystem::exists(path, filesystem_error) || filesystem_error) {
      return DiagnosticSinkOpenResult{
          nullptr, output_error("Diagnostic output already exists; choose a fresh output root.")};
    }
  }

  std::ofstream event_stream{event_partial_path, std::ios::binary | std::ios::out};
  if (!event_stream.is_open()) {
    return DiagnosticSinkOpenResult{
        nullptr, write_error("Diagnostic event partial file could not be opened.")};
  }
  std::ofstream snapshot_stream{snapshot_partial_path, std::ios::binary | std::ios::out};
  if (!snapshot_stream.is_open()) {
    return DiagnosticSinkOpenResult{
        nullptr, write_error("Diagnostic snapshot partial file could not be opened.")};
  }

  return DiagnosticSinkOpenResult{
      std::unique_ptr<JsonlDiagnosticSink>{new JsonlDiagnosticSink{
          event_path, snapshot_path, event_partial_path, snapshot_partial_path,
          std::move(event_stream), std::move(snapshot_stream)}},
      std::nullopt};
}

} // namespace itchlab
