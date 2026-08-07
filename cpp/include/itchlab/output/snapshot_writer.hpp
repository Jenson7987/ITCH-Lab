#pragma once

#include "itchlab/output/event_writer.hpp"

#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <memory>
#include <optional>
#include <span>
#include <string_view>

namespace itchlab {

inline constexpr std::uint16_t kSnapshotFixedRecordSize = 48;
inline constexpr std::uint16_t kSnapshotDepthEntrySize = 28;

[[nodiscard]] std::optional<std::uint16_t> snapshot_record_size(std::uint16_t depth) noexcept;

enum SnapshotValidityFlag : std::uint8_t {
  snapshot_trigger_price_valid = 1U << 0U,
  snapshot_trigger_quantity_valid = 1U << 1U,
  snapshot_last_trade_valid = 1U << 2U,
  snapshot_top_n_changed = 1U << 6U,
};

// Injectable seekable output boundary used to exercise short writes and finalisation failures.
class SnapshotWriterOutput {
public:
  [[nodiscard]] virtual bool write(std::span<const std::byte> bytes) = 0;
  [[nodiscard]] virtual bool seek(std::uint64_t offset) = 0;
  [[nodiscard]] virtual bool flush() = 0;
  [[nodiscard]] virtual bool close() = 0;
  virtual ~SnapshotWriterOutput() = default;
};

struct SnapshotWriterOpenResult;

class SnapshotWriter final : public SnapshotSink {
public:
  SnapshotWriter(const SnapshotWriter&) = delete;
  SnapshotWriter& operator=(const SnapshotWriter&) = delete;
  SnapshotWriter(SnapshotWriter&&) = delete;
  SnapshotWriter& operator=(SnapshotWriter&&) = delete;
  ~SnapshotWriter() override;

  [[nodiscard]] std::optional<DiagnosticWriteError>
  write_snapshot(const DiagnosticSnapshot& snapshot) override;
  [[nodiscard]] std::optional<DiagnosticWriteError> finalise(const EventFileMetadata& metadata);
  [[nodiscard]] std::optional<DiagnosticWriteError> close_partial();

  [[nodiscard]] std::uint64_t record_count() const noexcept { return record_count_; }
  [[nodiscard]] std::uint16_t depth() const noexcept { return depth_; }
  [[nodiscard]] std::uint16_t record_size() const noexcept { return record_size_; }
  [[nodiscard]] bool finalised() const noexcept { return finalised_; }
  [[nodiscard]] const std::filesystem::path& final_path() const noexcept { return final_path_; }
  [[nodiscard]] const std::filesystem::path& partial_path() const noexcept { return partial_path_; }

private:
  SnapshotWriter(std::unique_ptr<SnapshotWriterOutput> output, std::uint16_t expected_symbol_count,
                 std::uint16_t depth, std::uint16_t record_size, std::filesystem::path final_path,
                 std::filesystem::path partial_path);

  [[nodiscard]] std::optional<DiagnosticWriteError> initialise();
  [[nodiscard]] std::optional<DiagnosticWriteError> terminal_error(std::string_view message);

  std::unique_ptr<SnapshotWriterOutput> output_;
  std::uint16_t expected_symbol_count_{};
  std::uint16_t depth_{};
  std::uint16_t record_size_{};
  std::filesystem::path final_path_;
  std::filesystem::path partial_path_;
  std::uint64_t record_count_{};
  std::optional<MessageIndex> last_message_index_;
  std::optional<TimestampNs> last_timestamp_ns_;
  bool failed_{};
  bool closed_{};
  bool finalised_{};

  friend struct SnapshotWriterOpenResult;
  friend SnapshotWriterOpenResult make_snapshot_writer(std::unique_ptr<SnapshotWriterOutput>,
                                                       std::uint16_t, std::uint16_t,
                                                       std::filesystem::path,
                                                       std::filesystem::path);
};

struct SnapshotWriterOpenResult {
  std::unique_ptr<SnapshotWriter> writer;
  std::optional<DiagnosticWriteError> error;

  [[nodiscard]] bool valid() const noexcept { return writer != nullptr && !error.has_value(); }
};

[[nodiscard]] SnapshotWriterOpenResult
make_snapshot_writer(std::unique_ptr<SnapshotWriterOutput> output,
                     std::uint16_t expected_symbol_count, std::uint16_t depth,
                     std::filesystem::path final_path = {},
                     std::filesystem::path partial_path = {});

// Creates <final_path>.partial without replacing an existing partial or final file.
[[nodiscard]] SnapshotWriterOpenResult open_snapshot_writer(const std::filesystem::path& final_path,
                                                            std::uint16_t expected_symbol_count,
                                                            std::uint16_t depth);

} // namespace itchlab
