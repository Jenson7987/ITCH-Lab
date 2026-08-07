#include "itchlab/output/snapshot_writer.hpp"

#include "itchlab/output/binary_encode.hpp"

#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <limits>
#include <memory>
#include <optional>
#include <span>
#include <string>
#include <string_view>
#include <system_error>
#include <unordered_set>
#include <utility>
#include <vector>

namespace itchlab {
namespace {

constexpr std::array<std::byte, 8> kSnapshotMagic{
    std::byte{'I'}, std::byte{'T'}, std::byte{'C'}, std::byte{'H'},
    std::byte{'L'}, std::byte{'S'}, std::byte{'1'}, std::byte{0},
};

[[nodiscard]] DiagnosticWriteError writer_error(const ErrorCode code, std::string message) {
  return DiagnosticWriteError{code, std::move(message)};
}

[[nodiscard]] bool all_zero(const ContentHash& hash) noexcept {
  return std::ranges::all_of(hash, [](const std::byte value) { return value == std::byte{0}; });
}

[[nodiscard]] bool valid_symbol(const std::string_view symbol) noexcept {
  if (symbol.empty() || symbol.size() > 8 || symbol.front() == ' ' || symbol.back() == ' ') {
    return false;
  }
  return std::ranges::all_of(symbol, [](const char character) {
    const auto byte = static_cast<unsigned char>(character);
    return byte >= 0x20U && byte <= 0x7eU;
  });
}

[[nodiscard]] bool valid_trading_date(const TradingDate value) noexcept {
  const auto year = value / 10'000U;
  const auto month = value / 100U % 100U;
  const auto day = value % 100U;
  if (year == 0 || month == 0 || month > 12 || day == 0) {
    return false;
  }
  constexpr std::array<std::uint32_t, 12> days_per_month{31, 28, 31, 30, 31, 30,
                                                         31, 31, 30, 31, 30, 31};
  auto maximum_day = days_per_month[month - 1];
  const auto leap_year = (year % 4U == 0 && year % 100U != 0) || year % 400U == 0;
  if (month == 2 && leap_year) {
    maximum_day = 29;
  }
  return day <= maximum_day;
}

[[nodiscard]] std::optional<DiagnosticWriteError>
validate_levels(const std::vector<std::optional<AggregatedLevel>>& levels, const bool bids) {
  bool saw_empty{};
  std::optional<Price4> previous_price;
  for (const auto& level : levels) {
    if (!level) {
      saw_empty = true;
      continue;
    }
    if (saw_empty || level->total_quantity == 0) {
      return writer_error(ErrorCode::invariant,
                          "Snapshot-v1 depth levels must be positive and contiguous.");
    }
    if (previous_price &&
        (bids ? level->price4 >= *previous_price : level->price4 <= *previous_price)) {
      return writer_error(ErrorCode::invariant,
                          "Snapshot-v1 prices are not ordered best to worst.");
    }
    previous_price = level->price4;
  }
  return std::nullopt;
}

[[nodiscard]] std::optional<DiagnosticWriteError>
encode_snapshot_record(const DiagnosticSnapshot& snapshot, const std::uint16_t depth,
                       const std::uint16_t record_size, std::vector<std::byte>& output) {
  const auto kind = event_kind_code(snapshot.event_kind);
  if (!kind) {
    return writer_error(ErrorCode::invariant, "Snapshot trigger event kind is unsupported.");
  }
  if (!is_valid_timestamp(snapshot.timestamp_ns)) {
    return writer_error(ErrorCode::timestamp, "Snapshot timestamp is outside one day.");
  }
  if (snapshot.symbol_id == 0 || snapshot.depth != depth ||
      snapshot.top_levels.bids.size() != depth || snapshot.top_levels.asks.size() != depth) {
    return writer_error(ErrorCode::invariant,
                        "Snapshot depth or symbol identity disagrees with the output contract.");
  }
  if (static_cast<std::uint8_t>(snapshot.trading_state) >
      static_cast<std::uint8_t>(TradingState::closed)) {
    return writer_error(ErrorCode::invariant, "Snapshot trading state is invalid.");
  }
  if (snapshot.last_trade_price4.has_value() != snapshot.last_trade_quantity.has_value()) {
    return writer_error(ErrorCode::invariant,
                        "Snapshot last-trade price and quantity must be valid as a pair.");
  }
  if (const auto error = validate_levels(snapshot.top_levels.bids, true)) {
    return error;
  }
  if (const auto error = validate_levels(snapshot.top_levels.asks, false)) {
    return error;
  }

  std::uint8_t flags =
      static_cast<std::uint8_t>(static_cast<std::uint8_t>(snapshot.trading_state) << 3U);
  if (snapshot.event_price4) {
    flags |= snapshot_trigger_price_valid;
  }
  if (snapshot.event_quantity) {
    flags |= snapshot_trigger_quantity_valid;
  }
  if (snapshot.last_trade_price4) {
    flags |= snapshot_last_trade_valid;
  }
  if (snapshot.top_n_changed) {
    flags |= snapshot_top_n_changed;
  }

  output.assign(record_size, std::byte{0});
  const auto encoded =
      encode_little_endian_u64(output, 0, snapshot.message_index) &&
      encode_little_endian_u64(output, 8, snapshot.timestamp_ns) &&
      encode_little_endian_u16(output, 16, snapshot.symbol_id) &&
      encode_little_endian_u32(output, 20, snapshot.event_price4.value_or(0)) &&
      encode_little_endian_u64(output, 24, snapshot.event_quantity.value_or(0)) &&
      encode_little_endian_u32(output, 32, snapshot.last_trade_price4.value_or(0)) &&
      encode_little_endian_u64(output, 40, snapshot.last_trade_quantity.value_or(0));
  if (!encoded) {
    return writer_error(ErrorCode::internal, "Snapshot-v1 prefix bounds are inconsistent.");
  }
  output[18] = static_cast<std::byte>(static_cast<std::uint8_t>(*kind));
  output[19] = static_cast<std::byte>(flags);

  for (std::size_t index = 0; index < depth; ++index) {
    const auto offset = static_cast<std::size_t>(kSnapshotFixedRecordSize) +
                        index * static_cast<std::size_t>(kSnapshotDepthEntrySize);
    const auto& bid = snapshot.top_levels.bids[index];
    const auto& ask = snapshot.top_levels.asks[index];
    output[offset] = static_cast<std::byte>(bid ? 1U : 0U);
    output[offset + 1] = static_cast<std::byte>(ask ? 1U : 0U);
    if ((bid && (!encode_little_endian_u32(output, offset + 4, bid->price4) ||
                 !encode_little_endian_u64(output, offset + 8, bid->total_quantity))) ||
        (ask && (!encode_little_endian_u32(output, offset + 16, ask->price4) ||
                 !encode_little_endian_u64(output, offset + 20, ask->total_quantity)))) {
      return writer_error(ErrorCode::internal, "Snapshot-v1 depth-entry bounds are inconsistent.");
    }
  }
  return std::nullopt;
}

[[nodiscard]] std::optional<DiagnosticWriteError>
encode_prefix(const EventFileMetadata& metadata, const std::uint16_t expected_symbol_count,
              const std::uint16_t depth, const std::uint16_t record_size,
              const std::uint64_t record_count, std::vector<std::byte>& output) {
  if (!valid_trading_date(metadata.trading_date)) {
    return writer_error(ErrorCode::trading_date,
                        "Snapshot-v1 metadata has an invalid YYYYMMDD trading date.");
  }
  if (metadata.instruments.size() != expected_symbol_count) {
    return writer_error(ErrorCode::invariant,
                        "Snapshot-v1 symbol dictionary count changed during replay.");
  }
  if (all_zero(metadata.config_sha256) || all_zero(metadata.source_sha256)) {
    return writer_error(ErrorCode::hash_mismatch,
                        "Final snapshot-v1 metadata cannot contain placeholder hashes.");
  }

  std::unordered_set<StockLocate> locates;
  std::unordered_set<std::string> symbols;
  for (std::size_t index = 0; index < metadata.instruments.size(); ++index) {
    const auto& instrument = metadata.instruments[index];
    const auto expected_id = checked_integral_cast<SymbolId>(index + 1);
    if (!expected_id || instrument.symbol_id != *expected_id || instrument.stock_locate == 0 ||
        !valid_symbol(instrument.symbol) || !locates.insert(instrument.stock_locate).second ||
        !symbols.insert(instrument.symbol).second) {
      return writer_error(ErrorCode::invariant,
                          "Snapshot-v1 symbol dictionary is not valid requested-symbol order.");
    }
  }

  const auto prefix_size = static_cast<std::size_t>(kInterchangeHeaderSize) +
                           static_cast<std::size_t>(expected_symbol_count) *
                               static_cast<std::size_t>(kInterchangeSymbolEntrySize);
  output.assign(prefix_size, std::byte{0});
  const auto header_flags = static_cast<std::uint16_t>(metadata.degraded ? 1U : 0U);
  const auto encoded = encode_bytes(output, 0, kSnapshotMagic) &&
                       encode_little_endian_u16(output, 8, kInterchangeSchemaVersion) &&
                       encode_little_endian_u16(output, 10, kInterchangeHeaderSize) &&
                       encode_little_endian_u16(output, 12, record_size) &&
                       encode_little_endian_u16(output, 14, depth) &&
                       encode_little_endian_u32(output, 16, kInterchangePriceScale) &&
                       encode_little_endian_u32(output, 20, metadata.trading_date) &&
                       encode_little_endian_u16(output, 24, expected_symbol_count) &&
                       encode_little_endian_u16(output, 26, header_flags) &&
                       encode_little_endian_u64(output, 28, record_count) &&
                       encode_bytes(output, 36, metadata.config_sha256) &&
                       encode_bytes(output, 68, metadata.source_sha256);
  if (!encoded) {
    return writer_error(ErrorCode::internal, "Snapshot-v1 header bounds are inconsistent.");
  }

  for (std::size_t index = 0; index < metadata.instruments.size(); ++index) {
    const auto& instrument = metadata.instruments[index];
    const auto offset = static_cast<std::size_t>(kInterchangeHeaderSize) +
                        index * static_cast<std::size_t>(kInterchangeSymbolEntrySize);
    if (!encode_little_endian_u16(output, offset, instrument.symbol_id) ||
        !encode_little_endian_u16(output, offset + 2, instrument.stock_locate) ||
        !encode_padded_ascii(output, offset + 4, 8, instrument.symbol) ||
        !encode_little_endian_u32(output, offset + 12, instrument.round_lot_size)) {
      return writer_error(ErrorCode::internal, "Snapshot-v1 dictionary bounds are inconsistent.");
    }
  }
  return std::nullopt;
}

class FileSnapshotWriterOutput final : public SnapshotWriterOutput {
public:
  explicit FileSnapshotWriterOutput(std::fstream stream) : stream_{std::move(stream)} {}

  bool write(const std::span<const std::byte> bytes) override {
    if (bytes.size() > static_cast<std::size_t>(std::numeric_limits<std::streamsize>::max())) {
      return false;
    }
    stream_.write(reinterpret_cast<const char*>(bytes.data()),
                  static_cast<std::streamsize>(bytes.size()));
    return stream_.good();
  }

  bool seek(const std::uint64_t offset) override {
    const auto converted = checked_integral_cast<std::streamoff>(offset);
    if (!converted) {
      return false;
    }
    stream_.seekp(*converted, std::ios::beg);
    return stream_.good();
  }

  bool flush() override {
    stream_.flush();
    return stream_.good();
  }

  bool close() override {
    stream_.close();
    return !stream_.fail();
  }

private:
  std::fstream stream_;
};

} // namespace

std::optional<std::uint16_t> snapshot_record_size(const std::uint16_t depth) noexcept {
  if (depth < 1 || depth > 50) {
    return std::nullopt;
  }
  const auto entries = checked_multiply<std::uint32_t>(kSnapshotDepthEntrySize, depth);
  const auto total =
      entries ? checked_add<std::uint32_t>(kSnapshotFixedRecordSize, *entries) : std::nullopt;
  return total ? checked_integral_cast<std::uint16_t>(*total) : std::nullopt;
}

SnapshotWriter::SnapshotWriter(std::unique_ptr<SnapshotWriterOutput> output,
                               const std::uint16_t expected_symbol_count, const std::uint16_t depth,
                               const std::uint16_t record_size, std::filesystem::path final_path,
                               std::filesystem::path partial_path)
    : output_{std::move(output)}, expected_symbol_count_{expected_symbol_count}, depth_{depth},
      record_size_{record_size}, final_path_{std::move(final_path)},
      partial_path_{std::move(partial_path)} {}

SnapshotWriter::~SnapshotWriter() {
  if (!closed_ && output_ != nullptr) {
    static_cast<void>(output_->close());
  }
}

std::optional<DiagnosticWriteError> SnapshotWriter::terminal_error(const std::string_view message) {
  failed_ = true;
  return writer_error(ErrorCode::disk_write, std::string{message});
}

std::optional<DiagnosticWriteError> SnapshotWriter::initialise() {
  const auto prefix_size = static_cast<std::size_t>(kInterchangeHeaderSize) +
                           static_cast<std::size_t>(expected_symbol_count_) *
                               static_cast<std::size_t>(kInterchangeSymbolEntrySize);
  const std::vector<std::byte> placeholder(prefix_size, std::byte{0});
  if (!output_->write(placeholder)) {
    return terminal_error("Snapshot partial header reservation failed.");
  }
  return std::nullopt;
}

std::optional<DiagnosticWriteError>
SnapshotWriter::write_snapshot(const DiagnosticSnapshot& snapshot) {
  if (failed_) {
    return writer_error(ErrorCode::disk_write,
                        "Cannot write a snapshot after a snapshot-writer failure.");
  }
  if (closed_ || finalised_) {
    return writer_error(ErrorCode::disk_write,
                        "Cannot write a snapshot after snapshot output was closed.");
  }
  if (last_message_index_ && snapshot.message_index <= *last_message_index_) {
    return writer_error(ErrorCode::invariant,
                        "Snapshot message indices must be strictly increasing.");
  }
  if (last_timestamp_ns_ && snapshot.timestamp_ns < *last_timestamp_ns_) {
    return writer_error(ErrorCode::invariant, "Snapshot timestamps must be non-decreasing.");
  }
  if (snapshot.symbol_id > expected_symbol_count_) {
    return writer_error(ErrorCode::invariant,
                        "Snapshot SymbolId is absent from the expected dictionary.");
  }
  if (record_count_ == std::numeric_limits<std::uint64_t>::max()) {
    return writer_error(ErrorCode::internal, "Snapshot-v1 record count overflowed.");
  }

  std::vector<std::byte> record;
  if (const auto error = encode_snapshot_record(snapshot, depth_, record_size_, record)) {
    return error;
  }
  if (!output_->write(record)) {
    return terminal_error("Snapshot-v1 record write failed.");
  }
  ++record_count_;
  last_message_index_ = snapshot.message_index;
  last_timestamp_ns_ = snapshot.timestamp_ns;
  return std::nullopt;
}

std::optional<DiagnosticWriteError> SnapshotWriter::finalise(const EventFileMetadata& metadata) {
  if (failed_) {
    return writer_error(ErrorCode::disk_write,
                        "Cannot finalise snapshot output after a writer failure.");
  }
  if (closed_ || finalised_) {
    return writer_error(ErrorCode::disk_write, "Snapshot output is already closed.");
  }

  std::vector<std::byte> prefix;
  if (const auto error = encode_prefix(metadata, expected_symbol_count_, depth_, record_size_,
                                       record_count_, prefix)) {
    return error;
  }
  if (!output_->seek(0)) {
    return terminal_error("Snapshot partial header seek failed.");
  }
  if (!output_->write(prefix)) {
    return terminal_error("Snapshot partial header finalisation write failed.");
  }
  if (!output_->flush()) {
    return terminal_error("Snapshot partial flush failed during finalisation.");
  }
  if (!output_->close()) {
    closed_ = true;
    return terminal_error("Snapshot partial close failed during finalisation.");
  }
  closed_ = true;
  finalised_ = true;
  return std::nullopt;
}

std::optional<DiagnosticWriteError> SnapshotWriter::close_partial() {
  if (closed_) {
    return std::nullopt;
  }
  const auto flush_succeeded = output_->flush();
  const auto close_succeeded = output_->close();
  closed_ = true;
  if (!flush_succeeded) {
    failed_ = true;
    return writer_error(ErrorCode::disk_write, "Snapshot partial flush failed.");
  }
  if (!close_succeeded) {
    failed_ = true;
    return writer_error(ErrorCode::disk_write, "Snapshot partial close failed.");
  }
  return std::nullopt;
}

SnapshotWriterOpenResult make_snapshot_writer(std::unique_ptr<SnapshotWriterOutput> output,
                                              const std::uint16_t expected_symbol_count,
                                              const std::uint16_t depth,
                                              std::filesystem::path final_path,
                                              std::filesystem::path partial_path) {
  if (output == nullptr) {
    return SnapshotWriterOpenResult{
        nullptr, writer_error(ErrorCode::output_path, "Snapshot writer output is absent.")};
  }
  if (expected_symbol_count == 0) {
    return SnapshotWriterOpenResult{
        nullptr, writer_error(ErrorCode::config_schema,
                              "Snapshot writer requires at least one selected symbol.")};
  }
  const auto record_size = snapshot_record_size(depth);
  if (!record_size) {
    return SnapshotWriterOpenResult{
        nullptr, writer_error(ErrorCode::depth, "Snapshot depth must be between 1 and 50.")};
  }
  auto writer = std::unique_ptr<SnapshotWriter>{
      new SnapshotWriter{std::move(output), expected_symbol_count, depth, *record_size,
                         std::move(final_path), std::move(partial_path)}};
  if (const auto error = writer->initialise()) {
    return SnapshotWriterOpenResult{nullptr, error};
  }
  return SnapshotWriterOpenResult{std::move(writer), std::nullopt};
}

SnapshotWriterOpenResult open_snapshot_writer(const std::filesystem::path& final_path,
                                              const std::uint16_t expected_symbol_count,
                                              const std::uint16_t depth) {
  if (final_path.empty() || final_path.filename().empty()) {
    return SnapshotWriterOpenResult{
        nullptr, writer_error(ErrorCode::output_path, "Snapshot final path is empty.")};
  }
  auto partial_path = final_path;
  partial_path += ".partial";

  std::error_code filesystem_error;
  for (const auto& path : {final_path, partial_path}) {
    if (std::filesystem::exists(path, filesystem_error) || filesystem_error) {
      return SnapshotWriterOpenResult{
          nullptr, writer_error(ErrorCode::output_path,
                                "Snapshot output already exists; choose a fresh run-owned path.")};
    }
  }

  std::fstream stream{partial_path,
                      std::ios::binary | std::ios::in | std::ios::out | std::ios::trunc};
  if (!stream.is_open()) {
    return SnapshotWriterOpenResult{
        nullptr, writer_error(ErrorCode::disk_write, "Snapshot partial file could not be opened.")};
  }
  return make_snapshot_writer(std::make_unique<FileSnapshotWriterOutput>(std::move(stream)),
                              expected_symbol_count, depth, final_path, partial_path);
}

} // namespace itchlab
