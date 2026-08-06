#include "itchlab/output/event_writer.hpp"

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

constexpr std::array<std::byte, 8> kEventMagic{
    std::byte{'I'}, std::byte{'T'}, std::byte{'C'}, std::byte{'H'},
    std::byte{'L'}, std::byte{'E'}, std::byte{'1'}, std::byte{0},
};

[[nodiscard]] DiagnosticWriteError writer_error(const ErrorCode code, std::string message) {
  return DiagnosticWriteError{code, std::move(message)};
}

[[nodiscard]] bool all_zero(const ContentHash& hash) noexcept {
  return std::ranges::all_of(hash, [](const std::byte value) { return value == std::byte{0}; });
}

[[nodiscard]] bool valid_ascii(const std::string_view value) noexcept {
  return std::ranges::all_of(
      value, [](const char character) { return static_cast<unsigned char>(character) <= 0x7fU; });
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

[[nodiscard]] bool kind_matches_source(const EventKindCode kind, const char source_type) noexcept {
  switch (kind) {
  case EventKindCode::add:
    return source_type == 'A' || source_type == 'F';
  case EventKindCode::execute:
    return source_type == 'E';
  case EventKindCode::execute_price:
    return source_type == 'C';
  case EventKindCode::cancel:
    return source_type == 'X';
  case EventKindCode::delete_order:
    return source_type == 'D';
  case EventKindCode::replace:
    return source_type == 'U';
  case EventKindCode::trade:
    return source_type == 'P';
  case EventKindCode::cross:
    return source_type == 'Q';
  case EventKindCode::broken_trade:
    return source_type == 'B';
  case EventKindCode::trading_state:
    return source_type == 'H';
  }
  return false;
}

[[nodiscard]] std::optional<DiagnosticWriteError>
encode_event_record(const DiagnosticEvent& event, std::array<std::byte, kEventRecordSize>& output) {
  const auto kind = event_kind_code(event.event_kind);
  if (!kind || !kind_matches_source(*kind, event.source_type)) {
    return writer_error(ErrorCode::invariant,
                        "Normalised event kind and source type are inconsistent.");
  }
  if (!is_valid_timestamp(event.timestamp_ns)) {
    return writer_error(ErrorCode::timestamp, "Normalised event timestamp is outside one day.");
  }
  if (event.symbol_id == 0) {
    return writer_error(ErrorCode::invariant, "Normalised event SymbolId must be non-zero.");
  }
  if (event.side && *event.side == Side::not_applicable) {
    return writer_error(ErrorCode::invariant, "A valid normalised event side must be buy or sell.");
  }
  if (event.aux_code && (event.aux_code->size() > 4 || !valid_ascii(*event.aux_code))) {
    return writer_error(ErrorCode::invariant,
                        "Normalised event auxiliary code must be at most four ASCII bytes.");
  }
  if (event.event_subtype && static_cast<unsigned char>(*event.event_subtype) > 0x7fU) {
    return writer_error(ErrorCode::invariant, "Normalised event subtype must be an ASCII byte.");
  }

  std::uint16_t flags{};
  if (event.primary_reference) {
    flags |= event_primary_reference_valid;
  }
  if (event.secondary_reference) {
    flags |= event_secondary_reference_valid;
  }
  if (event.side) {
    flags |= event_side_valid;
  }
  if (event.price4) {
    flags |= event_price4_valid;
  }
  if (event.quantity) {
    flags |= event_quantity_valid;
  }
  if (event.remaining_quantity) {
    flags |= event_remaining_quantity_valid;
  }
  if (event.execution_price4) {
    flags |= event_execution_price4_valid;
  }
  if (event.aux_code) {
    flags |= event_aux_code_valid;
  }
  if (event.event_subtype) {
    flags |= event_subtype_valid;
  }
  if (event.in_session) {
    flags |= event_in_session;
  }

  std::uint32_t remaining_quantity{};
  if (event.remaining_quantity) {
    const auto narrowed = checked_integral_cast<std::uint32_t>(*event.remaining_quantity);
    if (!narrowed) {
      return writer_error(ErrorCode::quantity,
                          "Normalised event remaining quantity exceeds event-v1 storage.");
    }
    remaining_quantity = *narrowed;
  }

  output.fill(std::byte{0});
  const auto encoded =
      encode_little_endian_u64(output, 0, event.message_index) &&
      encode_little_endian_u64(output, 8, event.timestamp_ns) &&
      encode_little_endian_u64(output, 16, event.primary_reference.value_or(0)) &&
      encode_little_endian_u64(output, 24, event.secondary_reference.value_or(0)) &&
      encode_little_endian_u64(output, 32, event.quantity.value_or(0)) &&
      encode_little_endian_u32(output, 40, event.price4.value_or(0)) &&
      encode_little_endian_u32(output, 44, remaining_quantity) &&
      encode_little_endian_u32(output, 48, event.execution_price4.value_or(0)) &&
      encode_little_endian_u16(output, 52, event.symbol_id) &&
      encode_little_endian_u16(output, 57, flags);
  if (!encoded) {
    return writer_error(ErrorCode::internal, "Event-v1 record encoding bounds are inconsistent.");
  }

  output[54] = static_cast<std::byte>(static_cast<std::uint8_t>(*kind));
  output[55] = static_cast<std::byte>(
      event.side ? static_cast<std::uint8_t>(static_cast<std::int8_t>(*event.side)) : 0U);
  output[56] = static_cast<std::byte>(static_cast<unsigned char>(event.source_type));
  if (event.aux_code && !encode_padded_ascii(output, 60, 4, *event.aux_code)) {
    return writer_error(ErrorCode::internal, "Event-v1 auxiliary encoding failed.");
  }
  if (event.event_subtype) {
    output[64] = static_cast<std::byte>(static_cast<unsigned char>(*event.event_subtype));
  }
  return std::nullopt;
}

[[nodiscard]] std::optional<DiagnosticWriteError>
encode_prefix(const EventFileMetadata& metadata, const std::uint16_t expected_symbol_count,
              const std::uint64_t record_count, std::vector<std::byte>& output) {
  if (!valid_trading_date(metadata.trading_date)) {
    return writer_error(ErrorCode::trading_date,
                        "Event-v1 metadata has an invalid YYYYMMDD trading date.");
  }
  if (metadata.instruments.size() != expected_symbol_count) {
    return writer_error(ErrorCode::invariant,
                        "Event-v1 symbol dictionary count changed during replay.");
  }
  if (all_zero(metadata.config_sha256) || all_zero(metadata.source_sha256)) {
    return writer_error(ErrorCode::hash_mismatch,
                        "Final event-v1 metadata cannot contain placeholder hashes.");
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
                          "Event-v1 symbol dictionary is not valid requested-symbol order.");
    }
  }

  const auto prefix_size = static_cast<std::size_t>(kInterchangeHeaderSize) +
                           static_cast<std::size_t>(expected_symbol_count) *
                               static_cast<std::size_t>(kInterchangeSymbolEntrySize);
  output.assign(prefix_size, std::byte{0});
  const auto header_flags = static_cast<std::uint16_t>(metadata.degraded ? 1U : 0U);
  const auto encoded = encode_bytes(output, 0, kEventMagic) &&
                       encode_little_endian_u16(output, 8, kInterchangeSchemaVersion) &&
                       encode_little_endian_u16(output, 10, kInterchangeHeaderSize) &&
                       encode_little_endian_u16(output, 12, kEventRecordSize) &&
                       encode_little_endian_u16(output, 14, 0) &&
                       encode_little_endian_u32(output, 16, kInterchangePriceScale) &&
                       encode_little_endian_u32(output, 20, metadata.trading_date) &&
                       encode_little_endian_u16(output, 24, expected_symbol_count) &&
                       encode_little_endian_u16(output, 26, header_flags) &&
                       encode_little_endian_u64(output, 28, record_count) &&
                       encode_bytes(output, 36, metadata.config_sha256) &&
                       encode_bytes(output, 68, metadata.source_sha256);
  if (!encoded) {
    return writer_error(ErrorCode::internal, "Event-v1 header encoding bounds are inconsistent.");
  }

  for (std::size_t index = 0; index < metadata.instruments.size(); ++index) {
    const auto& instrument = metadata.instruments[index];
    const auto offset = static_cast<std::size_t>(kInterchangeHeaderSize) +
                        index * static_cast<std::size_t>(kInterchangeSymbolEntrySize);
    if (!encode_little_endian_u16(output, offset, instrument.symbol_id) ||
        !encode_little_endian_u16(output, offset + 2, instrument.stock_locate) ||
        !encode_padded_ascii(output, offset + 4, 8, instrument.symbol) ||
        !encode_little_endian_u32(output, offset + 12, instrument.round_lot_size)) {
      return writer_error(ErrorCode::internal,
                          "Event-v1 dictionary encoding bounds are inconsistent.");
    }
  }
  return std::nullopt;
}

class FileEventWriterOutput final : public EventWriterOutput {
public:
  explicit FileEventWriterOutput(std::fstream stream) : stream_{std::move(stream)} {}

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

std::optional<EventKindCode> event_kind_code(const std::string_view event_kind) noexcept {
  if (event_kind == "add") {
    return EventKindCode::add;
  }
  if (event_kind == "execute") {
    return EventKindCode::execute;
  }
  if (event_kind == "execute_price") {
    return EventKindCode::execute_price;
  }
  if (event_kind == "cancel") {
    return EventKindCode::cancel;
  }
  if (event_kind == "delete") {
    return EventKindCode::delete_order;
  }
  if (event_kind == "replace") {
    return EventKindCode::replace;
  }
  if (event_kind == "trade") {
    return EventKindCode::trade;
  }
  if (event_kind == "cross") {
    return EventKindCode::cross;
  }
  if (event_kind == "broken_trade") {
    return EventKindCode::broken_trade;
  }
  if (event_kind == "trading_state") {
    return EventKindCode::trading_state;
  }
  return std::nullopt;
}

EventWriter::EventWriter(std::unique_ptr<EventWriterOutput> output,
                         const std::uint16_t expected_symbol_count,
                         std::filesystem::path final_path, std::filesystem::path partial_path)
    : output_{std::move(output)}, expected_symbol_count_{expected_symbol_count},
      final_path_{std::move(final_path)}, partial_path_{std::move(partial_path)} {}

EventWriter::~EventWriter() {
  if (!closed_ && output_ != nullptr) {
    static_cast<void>(output_->close());
  }
}

std::optional<DiagnosticWriteError> EventWriter::terminal_error(const std::string_view message) {
  failed_ = true;
  return writer_error(ErrorCode::disk_write, std::string{message});
}

std::optional<DiagnosticWriteError> EventWriter::initialise() {
  const auto prefix_size = static_cast<std::size_t>(kInterchangeHeaderSize) +
                           static_cast<std::size_t>(expected_symbol_count_) *
                               static_cast<std::size_t>(kInterchangeSymbolEntrySize);
  const std::vector<std::byte> placeholder(prefix_size, std::byte{0});
  if (!output_->write(placeholder)) {
    return terminal_error("Event partial header reservation failed.");
  }
  return std::nullopt;
}

std::optional<DiagnosticWriteError> EventWriter::write_event(const DiagnosticEvent& event) {
  if (failed_) {
    return writer_error(ErrorCode::disk_write,
                        "Cannot write an event after an event-writer failure.");
  }
  if (closed_ || finalised_) {
    return writer_error(ErrorCode::disk_write,
                        "Cannot write an event after event output was closed.");
  }
  if (last_message_index_ && event.message_index <= *last_message_index_) {
    return writer_error(ErrorCode::invariant,
                        "Normalised event message indices must be strictly increasing.");
  }
  if (last_timestamp_ns_ && event.timestamp_ns < *last_timestamp_ns_) {
    return writer_error(ErrorCode::invariant,
                        "Normalised event timestamps must be non-decreasing.");
  }
  if (event.symbol_id > expected_symbol_count_) {
    return writer_error(ErrorCode::invariant,
                        "Normalised event SymbolId is absent from the expected dictionary.");
  }
  if (record_count_ == std::numeric_limits<std::uint64_t>::max()) {
    return writer_error(ErrorCode::internal, "Event-v1 record count overflowed.");
  }

  std::array<std::byte, kEventRecordSize> record{};
  if (const auto error = encode_event_record(event, record)) {
    return error;
  }
  if (!output_->write(record)) {
    return terminal_error("Event-v1 record write failed.");
  }
  ++record_count_;
  last_message_index_ = event.message_index;
  last_timestamp_ns_ = event.timestamp_ns;
  return std::nullopt;
}

std::optional<DiagnosticWriteError> EventWriter::finalise(const EventFileMetadata& metadata) {
  if (failed_) {
    return writer_error(ErrorCode::disk_write,
                        "Cannot finalise event output after a writer failure.");
  }
  if (closed_ || finalised_) {
    return writer_error(ErrorCode::disk_write, "Event output is already closed.");
  }

  std::vector<std::byte> prefix;
  if (const auto error = encode_prefix(metadata, expected_symbol_count_, record_count_, prefix)) {
    return error;
  }
  if (!output_->seek(0)) {
    return terminal_error("Event partial header seek failed.");
  }
  if (!output_->write(prefix)) {
    return terminal_error("Event partial header finalisation write failed.");
  }
  if (!output_->flush()) {
    return terminal_error("Event partial flush failed during finalisation.");
  }
  if (!output_->close()) {
    closed_ = true;
    return terminal_error("Event partial close failed during finalisation.");
  }
  closed_ = true;
  finalised_ = true;
  return std::nullopt;
}

std::optional<DiagnosticWriteError> EventWriter::close_partial() {
  if (closed_) {
    return std::nullopt;
  }
  const auto flush_succeeded = output_->flush();
  const auto close_succeeded = output_->close();
  closed_ = true;
  if (!flush_succeeded) {
    failed_ = true;
    return writer_error(ErrorCode::disk_write, "Event partial flush failed.");
  }
  if (!close_succeeded) {
    failed_ = true;
    return writer_error(ErrorCode::disk_write, "Event partial close failed.");
  }
  return std::nullopt;
}

EventWriterOpenResult make_event_writer(std::unique_ptr<EventWriterOutput> output,
                                        const std::uint16_t expected_symbol_count,
                                        std::filesystem::path final_path,
                                        std::filesystem::path partial_path) {
  if (output == nullptr) {
    return EventWriterOpenResult{
        nullptr, writer_error(ErrorCode::output_path, "Event writer output is absent.")};
  }
  if (expected_symbol_count == 0) {
    return EventWriterOpenResult{
        nullptr, writer_error(ErrorCode::config_schema,
                              "Event writer requires at least one selected symbol.")};
  }
  auto writer = std::unique_ptr<EventWriter>{new EventWriter{
      std::move(output), expected_symbol_count, std::move(final_path), std::move(partial_path)}};
  if (const auto error = writer->initialise()) {
    return EventWriterOpenResult{nullptr, error};
  }
  return EventWriterOpenResult{std::move(writer), std::nullopt};
}

EventWriterOpenResult open_event_writer(const std::filesystem::path& final_path,
                                        const std::uint16_t expected_symbol_count) {
  if (final_path.empty() || final_path.filename().empty()) {
    return EventWriterOpenResult{
        nullptr, writer_error(ErrorCode::output_path, "Event final path is empty.")};
  }
  auto partial_path = final_path;
  partial_path += ".partial";

  std::error_code filesystem_error;
  for (const auto& path : {final_path, partial_path}) {
    if (std::filesystem::exists(path, filesystem_error) || filesystem_error) {
      return EventWriterOpenResult{
          nullptr, writer_error(ErrorCode::output_path,
                                "Event output already exists; choose a fresh run-owned path.")};
    }
  }

  std::fstream stream{partial_path,
                      std::ios::binary | std::ios::in | std::ios::out | std::ios::trunc};
  if (!stream.is_open()) {
    return EventWriterOpenResult{
        nullptr, writer_error(ErrorCode::disk_write, "Event partial file could not be opened.")};
  }
  return make_event_writer(std::make_unique<FileEventWriterOutput>(std::move(stream)),
                           expected_symbol_count, final_path, partial_path);
}

} // namespace itchlab
