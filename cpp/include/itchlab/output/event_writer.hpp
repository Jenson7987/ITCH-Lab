#pragma once

#include "itchlab/core/types.hpp"
#include "itchlab/output/diagnostic_sinks.hpp"
#include "itchlab/replay/instrument_directory.hpp"

#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <memory>
#include <optional>
#include <span>
#include <string_view>
#include <vector>

namespace itchlab {

inline constexpr std::uint16_t kInterchangeSchemaVersion = 1;
inline constexpr std::uint16_t kInterchangeHeaderSize = 104;
inline constexpr std::uint16_t kInterchangeSymbolEntrySize = 16;
inline constexpr std::uint16_t kEventRecordSize = 72;
inline constexpr std::uint32_t kInterchangePriceScale = 10'000;

enum class EventKindCode : std::uint8_t {
  add = 1,
  execute = 2,
  execute_price = 3,
  cancel = 4,
  delete_order = 5,
  replace = 6,
  trade = 7,
  cross = 8,
  broken_trade = 9,
  trading_state = 10,
};

[[nodiscard]] std::optional<EventKindCode> event_kind_code(std::string_view event_kind) noexcept;

enum EventValidityFlag : std::uint16_t {
  event_primary_reference_valid = 1U << 0U,
  event_secondary_reference_valid = 1U << 1U,
  event_side_valid = 1U << 2U,
  event_price4_valid = 1U << 3U,
  event_quantity_valid = 1U << 4U,
  event_remaining_quantity_valid = 1U << 5U,
  event_execution_price4_valid = 1U << 6U,
  event_aux_code_valid = 1U << 7U,
  event_subtype_valid = 1U << 8U,
  event_in_session = 1U << 9U,
};

struct EventFileMetadata {
  TradingDate trading_date{};
  std::vector<Instrument> instruments;
  bool degraded{};
  ContentHash config_sha256{};
  ContentHash source_sha256{};
};

// Injectable seekable output boundary used to exercise short writes and finalisation failures.
class EventWriterOutput {
public:
  [[nodiscard]] virtual bool write(std::span<const std::byte> bytes) = 0;
  [[nodiscard]] virtual bool seek(std::uint64_t offset) = 0;
  [[nodiscard]] virtual bool flush() = 0;
  [[nodiscard]] virtual bool close() = 0;
  virtual ~EventWriterOutput() = default;
};

struct EventWriterOpenResult;

class EventWriter final : public EventSink {
public:
  EventWriter(const EventWriter&) = delete;
  EventWriter& operator=(const EventWriter&) = delete;
  EventWriter(EventWriter&&) = delete;
  EventWriter& operator=(EventWriter&&) = delete;
  ~EventWriter() override;

  [[nodiscard]] bool requires_intermediate_book_digest() const noexcept override { return false; }
  [[nodiscard]] std::optional<DiagnosticWriteError>
  write_event(const DiagnosticEvent& event) override;

  // Patches the complete header/dictionary, flushes and closes the staged file. It deliberately
  // does not publish the final path; atomic multi-artefact publication belongs to TASK-014.
  [[nodiscard]] std::optional<DiagnosticWriteError> finalise(const EventFileMetadata& metadata);

  // Flushes and closes staged output without making it final. Idempotent.
  [[nodiscard]] std::optional<DiagnosticWriteError> close_partial();

  [[nodiscard]] std::uint64_t record_count() const noexcept { return record_count_; }
  [[nodiscard]] bool finalised() const noexcept { return finalised_; }
  [[nodiscard]] const std::filesystem::path& final_path() const noexcept { return final_path_; }
  [[nodiscard]] const std::filesystem::path& partial_path() const noexcept { return partial_path_; }

private:
  EventWriter(std::unique_ptr<EventWriterOutput> output, std::uint16_t expected_symbol_count,
              std::filesystem::path final_path, std::filesystem::path partial_path);

  [[nodiscard]] std::optional<DiagnosticWriteError> initialise();
  [[nodiscard]] std::optional<DiagnosticWriteError> terminal_error(std::string_view message);

  std::unique_ptr<EventWriterOutput> output_;
  std::uint16_t expected_symbol_count_{};
  std::filesystem::path final_path_;
  std::filesystem::path partial_path_;
  std::uint64_t record_count_{};
  std::optional<MessageIndex> last_message_index_;
  std::optional<TimestampNs> last_timestamp_ns_;
  bool failed_{};
  bool closed_{};
  bool finalised_{};

  friend struct EventWriterOpenResult;
  friend EventWriterOpenResult make_event_writer(std::unique_ptr<EventWriterOutput>, std::uint16_t,
                                                 std::filesystem::path, std::filesystem::path);
};

struct EventWriterOpenResult {
  std::unique_ptr<EventWriter> writer;
  std::optional<DiagnosticWriteError> error;

  [[nodiscard]] bool valid() const noexcept { return writer != nullptr && !error.has_value(); }
};

// Creates a writer over an injected output. Intended for domain tests and non-filesystem owners.
[[nodiscard]] EventWriterOpenResult make_event_writer(std::unique_ptr<EventWriterOutput> output,
                                                      std::uint16_t expected_symbol_count,
                                                      std::filesystem::path final_path = {},
                                                      std::filesystem::path partial_path = {});

// Creates <final_path>.partial without replacing an existing partial or final file. The parent
// directory must already exist and remain owned by the caller.
[[nodiscard]] EventWriterOpenResult open_event_writer(const std::filesystem::path& final_path,
                                                      std::uint16_t expected_symbol_count);

} // namespace itchlab
