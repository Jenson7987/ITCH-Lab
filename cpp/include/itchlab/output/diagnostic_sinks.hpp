#pragma once

#include "itchlab/book/price_level.hpp"
#include "itchlab/core/errors.hpp"
#include "itchlab/core/types.hpp"

#include <cstdint>
#include <filesystem>
#include <fstream>
#include <memory>
#include <optional>
#include <string>

namespace itchlab {

struct DiagnosticEvent {
  MessageIndex message_index{};
  std::uint64_t source_offset{};
  TimestampNs timestamp_ns{};
  SymbolId symbol_id{};
  StockLocate stock_locate{};
  std::string symbol;
  std::string event_kind;
  char source_type{};
  OrderReference order_reference{};
  Side side{Side::not_applicable};
  Price4 price4{};
  Shares quantity{};
  Shares previous_remaining{};
  Shares remaining_quantity{};
  bool in_session{};
  ContentHash book_digest{};
};

struct DiagnosticSnapshot {
  MessageIndex message_index{};
  TimestampNs timestamp_ns{};
  SymbolId symbol_id{};
  StockLocate stock_locate{};
  std::string symbol;
  std::string event_kind;
  std::uint16_t depth{};
  bool top_n_changed{};
  TopLevels top_levels;
  ContentHash book_digest{};
};

struct DiagnosticWriteError {
  ErrorCode code{ErrorCode::disk_write};
  std::string message;
};

class DiagnosticSink {
public:
  [[nodiscard]] virtual std::optional<DiagnosticWriteError>
  write_event(const DiagnosticEvent& event) = 0;
  [[nodiscard]] virtual std::optional<DiagnosticWriteError>
  write_snapshot(const DiagnosticSnapshot& snapshot) = 0;
  virtual ~DiagnosticSink() = default;
};

class JsonlDiagnosticSink final : public DiagnosticSink {
public:
  JsonlDiagnosticSink(std::filesystem::path event_path, std::filesystem::path snapshot_path,
                      std::filesystem::path event_partial_path,
                      std::filesystem::path snapshot_partial_path, std::ofstream event_stream,
                      std::ofstream snapshot_stream);
  JsonlDiagnosticSink(const JsonlDiagnosticSink&) = delete;
  JsonlDiagnosticSink& operator=(const JsonlDiagnosticSink&) = delete;
  JsonlDiagnosticSink(JsonlDiagnosticSink&&) = delete;
  JsonlDiagnosticSink& operator=(JsonlDiagnosticSink&&) = delete;
  ~JsonlDiagnosticSink() override = default;

  [[nodiscard]] std::optional<DiagnosticWriteError>
  write_event(const DiagnosticEvent& event) override;
  [[nodiscard]] std::optional<DiagnosticWriteError>
  write_snapshot(const DiagnosticSnapshot& snapshot) override;

  // Flushes and atomically renames both staged files. Existing final files are never replaced.
  [[nodiscard]] std::optional<DiagnosticWriteError> publish();

  [[nodiscard]] const std::filesystem::path& event_path() const noexcept { return event_path_; }
  [[nodiscard]] const std::filesystem::path& snapshot_path() const noexcept {
    return snapshot_path_;
  }

private:
  std::filesystem::path event_path_;
  std::filesystem::path snapshot_path_;
  std::filesystem::path event_partial_path_;
  std::filesystem::path snapshot_partial_path_;
  std::ofstream event_stream_;
  std::ofstream snapshot_stream_;
  bool published_{};
};

struct DiagnosticSinkOpenResult {
  std::unique_ptr<JsonlDiagnosticSink> sink;
  std::optional<DiagnosticWriteError> error;

  [[nodiscard]] bool valid() const noexcept { return sink != nullptr && !error.has_value(); }
};

// Opens exact run-owned partial files beneath output_root. The root may be created, but it must not
// be the filesystem root and no existing diagnostic file is overwritten.
[[nodiscard]] DiagnosticSinkOpenResult
open_jsonl_diagnostic_sink(const std::filesystem::path& output_root);

} // namespace itchlab
