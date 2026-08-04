#include "itchlab/itch/decoder.hpp"

#include "itchlab/core/types.hpp"
#include "itchlab/itch/byte_decode.hpp"

#include <cctype>
#include <cstddef>
#include <cstdint>
#include <optional>
#include <span>
#include <string>
#include <utility>

namespace itchlab {
namespace {

constexpr std::size_t kSystemEventLength = 12;
constexpr std::size_t kStockDirectoryLength = 39;
constexpr std::size_t kAddOrderLength = 36;
constexpr std::size_t kOrderDeleteLength = 19;

[[nodiscard]] std::optional<std::size_t> expected_length(const std::uint8_t source_type) noexcept {
  switch (source_type) {
  case 'S':
    return kSystemEventLength;
  case 'R':
    return kStockDirectoryLength;
  case 'A':
    return kAddOrderLength;
  case 'D':
    return kOrderDeleteLength;
  default:
    return std::nullopt;
  }
}

[[nodiscard]] std::string type_description(const std::uint8_t source_type) {
  if (std::isprint(source_type) != 0) {
    return std::string{"'"} + static_cast<char>(source_type) + "'";
  }
  return std::to_string(source_type);
}

[[nodiscard]] char alpha_at(const std::span<const std::byte> payload,
                            const std::size_t offset) noexcept {
  return static_cast<char>(std::to_integer<unsigned char>(payload[offset]));
}

[[nodiscard]] DecodeResult fail(const ErrorCode code, const std::optional<std::uint8_t> source_type,
                                const std::size_t observed_length,
                                const std::optional<std::size_t> required_length,
                                std::string message) {
  return DecodeResult::failure(
      DecodeError{code, source_type, observed_length, required_length, std::move(message)});
}

[[nodiscard]] std::optional<MessageHeader>
decode_header(const std::span<const std::byte> payload) noexcept {
  const auto stock_locate = read_big_endian_u16(payload, 1);
  const auto tracking_number = read_big_endian_u16(payload, 3);
  const auto timestamp_ns = read_big_endian_u48(payload, 5);
  if (!stock_locate || !tracking_number || !timestamp_ns) {
    return std::nullopt;
  }
  return MessageHeader{*stock_locate, *tracking_number, *timestamp_ns};
}

[[nodiscard]] DecodeResult invalid_internal_field(const std::uint8_t source_type,
                                                  const std::size_t observed_length) {
  return fail(ErrorCode::internal, source_type, observed_length, observed_length,
              "Validated ITCH payload could not be read safely.");
}

[[nodiscard]] DecodeResult decode_system_event(const std::span<const std::byte> payload,
                                               const MessageHeader& header) {
  return DecodeResult::success(SystemEvent{header, alpha_at(payload, 11)});
}

[[nodiscard]] DecodeResult decode_stock_directory(const std::span<const std::byte> payload,
                                                  const MessageHeader& header) {
  StockField stock{};
  IssueSubTypeField issue_sub_type{};
  const auto round_lot_size = read_big_endian_u32(payload, 21);
  const auto etp_leverage_factor = read_big_endian_u32(payload, 34);
  if (!read_alpha(payload, 11, stock) || !round_lot_size ||
      !read_alpha(payload, 27, issue_sub_type) || !etp_leverage_factor) {
    return invalid_internal_field('R', payload.size());
  }

  return DecodeResult::success(StockDirectory{
      header,
      stock,
      alpha_at(payload, 19),
      alpha_at(payload, 20),
      *round_lot_size,
      alpha_at(payload, 25),
      alpha_at(payload, 26),
      issue_sub_type,
      alpha_at(payload, 29),
      alpha_at(payload, 30),
      alpha_at(payload, 31),
      alpha_at(payload, 32),
      alpha_at(payload, 33),
      *etp_leverage_factor,
      alpha_at(payload, 38),
  });
}

[[nodiscard]] DecodeResult decode_add_order(const std::span<const std::byte> payload,
                                            const MessageHeader& header) {
  const auto order_reference = read_big_endian_u64(payload, 11);
  const auto source_shares = read_big_endian_u32(payload, 20);
  const auto price4 = read_big_endian_u32(payload, 32);
  StockField stock{};
  if (!order_reference || !source_shares || !read_alpha(payload, 24, stock) || !price4) {
    return invalid_internal_field('A', payload.size());
  }

  const auto side_byte = std::to_integer<std::uint8_t>(payload[19]);
  Side side{Side::not_applicable};
  if (side_byte == 'B') {
    side = Side::buy;
  } else if (side_byte == 'S') {
    side = Side::sell;
  } else {
    return fail(ErrorCode::invariant, static_cast<std::uint8_t>('A'), payload.size(),
                payload.size(), "Add Order buy/sell indicator must be 'B' or 'S'.");
  }

  return DecodeResult::success(AddOrder{header, *order_reference, side,
                                        static_cast<Shares>(*source_shares), stock, *price4});
}

[[nodiscard]] DecodeResult decode_order_delete(const std::span<const std::byte> payload,
                                               const MessageHeader& header) {
  const auto order_reference = read_big_endian_u64(payload, 11);
  if (!order_reference) {
    return invalid_internal_field('D', payload.size());
  }
  return DecodeResult::success(OrderDelete{header, *order_reference});
}

} // namespace

DecodeResult DecodeResult::success(ItchMessage decoded_message) {
  return DecodeResult{std::move(decoded_message), std::nullopt};
}

DecodeResult DecodeResult::failure(DecodeError decode_error) {
  return DecodeResult{std::nullopt, std::move(decode_error)};
}

DecodeResult ItchDecoder::decode(const std::span<const std::byte> payload) const {
  if (payload.empty()) {
    return fail(ErrorCode::message_length, std::nullopt, 0, std::nullopt,
                "ITCH payload is empty; a message type byte is required.");
  }

  const auto source_type = std::to_integer<std::uint8_t>(payload.front());
  const auto required_length = expected_length(source_type);
  if (!required_length) {
    return fail(ErrorCode::unknown_message, source_type, payload.size(), std::nullopt,
                "Unsupported ITCH message type " + type_description(source_type) + " has length " +
                    std::to_string(payload.size()) + ".");
  }
  if (payload.size() != *required_length) {
    return fail(ErrorCode::message_length, source_type, payload.size(), required_length,
                "ITCH message type " + type_description(source_type) + " has length " +
                    std::to_string(payload.size()) + "; expected " +
                    std::to_string(*required_length) + ".");
  }

  const auto header = decode_header(payload);
  if (!header) {
    return invalid_internal_field(source_type, payload.size());
  }
  if (!is_valid_timestamp(header->timestamp_ns)) {
    return fail(ErrorCode::timestamp, source_type, payload.size(), required_length,
                "ITCH timestamp must be less than one exchange day in nanoseconds.");
  }

  switch (source_type) {
  case 'S':
    return decode_system_event(payload, *header);
  case 'R':
    return decode_stock_directory(payload, *header);
  case 'A':
    return decode_add_order(payload, *header);
  case 'D':
    return decode_order_delete(payload, *header);
  default:
    return invalid_internal_field(source_type, payload.size());
  }
}

} // namespace itchlab
