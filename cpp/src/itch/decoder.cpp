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
constexpr std::size_t kTradingActionLength = 25;
constexpr std::size_t kAddOrderLength = 36;
constexpr std::size_t kAddOrderWithAttributionLength = 40;
constexpr std::size_t kOrderExecutedLength = 31;
constexpr std::size_t kOrderExecutedWithPriceLength = 36;
constexpr std::size_t kOrderCancelLength = 23;
constexpr std::size_t kOrderDeleteLength = 19;
constexpr std::size_t kOrderReplaceLength = 35;
constexpr std::size_t kTradeLength = 44;
constexpr std::size_t kCrossTradeLength = 40;
constexpr std::size_t kBrokenTradeLength = 19;
constexpr std::size_t kRegShoRestrictionLength = 20;
constexpr std::size_t kMarketParticipantPositionLength = 26;
constexpr std::size_t kMwcbDeclineLevelLength = 35;
constexpr std::size_t kMwcbStatusLength = 12;
constexpr std::size_t kIpoQuotingPeriodLength = 28;
constexpr std::size_t kNoiiLength = 50;
constexpr std::size_t kRetailPriceImprovementLength = 20;
constexpr std::size_t kLuldAuctionCollarLength = 35;
constexpr std::size_t kOperationalHaltLength = 21;

[[nodiscard]] std::optional<std::size_t> expected_length(const std::uint8_t source_type) noexcept {
  switch (source_type) {
  case 'S':
    return kSystemEventLength;
  case 'R':
    return kStockDirectoryLength;
  case 'H':
    return kTradingActionLength;
  case 'A':
    return kAddOrderLength;
  case 'F':
    return kAddOrderWithAttributionLength;
  case 'E':
    return kOrderExecutedLength;
  case 'C':
    return kOrderExecutedWithPriceLength;
  case 'X':
    return kOrderCancelLength;
  case 'D':
    return kOrderDeleteLength;
  case 'U':
    return kOrderReplaceLength;
  case 'P':
    return kTradeLength;
  case 'Q':
    return kCrossTradeLength;
  case 'B':
    return kBrokenTradeLength;
  case 'Y':
    return kRegShoRestrictionLength;
  case 'L':
    return kMarketParticipantPositionLength;
  case 'V':
    return kMwcbDeclineLevelLength;
  case 'W':
    return kMwcbStatusLength;
  case 'K':
    return kIpoQuotingPeriodLength;
  case 'I':
    return kNoiiLength;
  case 'N':
    return kRetailPriceImprovementLength;
  case 'J':
    return kLuldAuctionCollarLength;
  case 'h':
    return kOperationalHaltLength;
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

[[nodiscard]] DecodeResult decode_trading_action(const std::span<const std::byte> payload,
                                                 const MessageHeader& header) {
  StockField stock{};
  TradingReasonField reason{};
  if (!read_alpha(payload, 11, stock) || !read_alpha(payload, 21, reason)) {
    return invalid_internal_field('H', payload.size());
  }
  return DecodeResult::success(
      TradingAction{header, stock, alpha_at(payload, 19), alpha_at(payload, 20), reason});
}

[[nodiscard]] std::optional<Side> decode_side(const std::span<const std::byte> payload,
                                              const std::size_t offset) noexcept {
  const auto side_byte = std::to_integer<std::uint8_t>(payload[offset]);
  if (side_byte == 'B') {
    return Side::buy;
  }
  if (side_byte == 'S') {
    return Side::sell;
  }
  return std::nullopt;
}

[[nodiscard]] DecodeResult invalid_side(const std::uint8_t source_type,
                                        const std::size_t observed_length) {
  return fail(ErrorCode::invariant, source_type, observed_length, observed_length,
              "ITCH buy/sell indicator must be 'B' or 'S'.");
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

  const auto side = decode_side(payload, 19);
  if (!side) {
    return invalid_side('A', payload.size());
  }

  return DecodeResult::success(AddOrder{header, *order_reference, *side,
                                        static_cast<Shares>(*source_shares), stock, *price4});
}

[[nodiscard]] DecodeResult
decode_add_order_with_attribution(const std::span<const std::byte> payload,
                                  const MessageHeader& header) {
  const auto order_reference = read_big_endian_u64(payload, 11);
  const auto source_shares = read_big_endian_u32(payload, 20);
  const auto price4 = read_big_endian_u32(payload, 32);
  StockField stock{};
  AttributionField attribution{};
  if (!order_reference || !source_shares || !read_alpha(payload, 24, stock) || !price4 ||
      !read_alpha(payload, 36, attribution)) {
    return invalid_internal_field('F', payload.size());
  }
  const auto side = decode_side(payload, 19);
  if (!side) {
    return invalid_side('F', payload.size());
  }

  return DecodeResult::success(AddOrderWithAttribution{header, *order_reference, *side,
                                                       static_cast<Shares>(*source_shares), stock,
                                                       *price4, attribution});
}

[[nodiscard]] DecodeResult decode_order_executed(const std::span<const std::byte> payload,
                                                 const MessageHeader& header) {
  const auto order_reference = read_big_endian_u64(payload, 11);
  const auto executed_shares = read_big_endian_u32(payload, 19);
  const auto match_number = read_big_endian_u64(payload, 23);
  if (!order_reference || !executed_shares || !match_number) {
    return invalid_internal_field('E', payload.size());
  }
  return DecodeResult::success(OrderExecuted{header, *order_reference,
                                             static_cast<Shares>(*executed_shares), *match_number});
}

[[nodiscard]] DecodeResult
decode_order_executed_with_price(const std::span<const std::byte> payload,
                                 const MessageHeader& header) {
  const auto order_reference = read_big_endian_u64(payload, 11);
  const auto executed_shares = read_big_endian_u32(payload, 19);
  const auto match_number = read_big_endian_u64(payload, 23);
  const auto execution_price4 = read_big_endian_u32(payload, 32);
  if (!order_reference || !executed_shares || !match_number || !execution_price4) {
    return invalid_internal_field('C', payload.size());
  }
  return DecodeResult::success(
      OrderExecutedWithPrice{header, *order_reference, static_cast<Shares>(*executed_shares),
                             *match_number, alpha_at(payload, 31), *execution_price4});
}

[[nodiscard]] DecodeResult decode_order_cancel(const std::span<const std::byte> payload,
                                               const MessageHeader& header) {
  const auto order_reference = read_big_endian_u64(payload, 11);
  const auto cancelled_shares = read_big_endian_u32(payload, 19);
  if (!order_reference || !cancelled_shares) {
    return invalid_internal_field('X', payload.size());
  }
  return DecodeResult::success(
      OrderCancel{header, *order_reference, static_cast<Shares>(*cancelled_shares)});
}

[[nodiscard]] DecodeResult decode_order_delete(const std::span<const std::byte> payload,
                                               const MessageHeader& header) {
  const auto order_reference = read_big_endian_u64(payload, 11);
  if (!order_reference) {
    return invalid_internal_field('D', payload.size());
  }
  return DecodeResult::success(OrderDelete{header, *order_reference});
}

[[nodiscard]] DecodeResult decode_order_replace(const std::span<const std::byte> payload,
                                                const MessageHeader& header) {
  const auto original_order_reference = read_big_endian_u64(payload, 11);
  const auto new_order_reference = read_big_endian_u64(payload, 19);
  const auto source_shares = read_big_endian_u32(payload, 27);
  const auto price4 = read_big_endian_u32(payload, 31);
  if (!original_order_reference || !new_order_reference || !source_shares || !price4) {
    return invalid_internal_field('U', payload.size());
  }
  return DecodeResult::success(OrderReplace{header, *original_order_reference, *new_order_reference,
                                            static_cast<Shares>(*source_shares), *price4});
}

[[nodiscard]] DecodeResult decode_trade(const std::span<const std::byte> payload,
                                        const MessageHeader& header) {
  const auto order_reference = read_big_endian_u64(payload, 11);
  const auto source_shares = read_big_endian_u32(payload, 20);
  const auto price4 = read_big_endian_u32(payload, 32);
  const auto match_number = read_big_endian_u64(payload, 36);
  StockField stock{};
  if (!order_reference || !source_shares || !read_alpha(payload, 24, stock) || !price4 ||
      !match_number) {
    return invalid_internal_field('P', payload.size());
  }
  const auto buy_sell_indicator = decode_side(payload, 19);
  if (!buy_sell_indicator) {
    return invalid_side('P', payload.size());
  }
  return DecodeResult::success(Trade{header, *order_reference, *buy_sell_indicator,
                                     static_cast<Shares>(*source_shares), stock, *price4,
                                     *match_number});
}

[[nodiscard]] DecodeResult decode_cross_trade(const std::span<const std::byte> payload,
                                              const MessageHeader& header) {
  const auto shares = read_big_endian_u64(payload, 11);
  const auto cross_price4 = read_big_endian_u32(payload, 27);
  const auto match_number = read_big_endian_u64(payload, 31);
  StockField stock{};
  if (!shares || !read_alpha(payload, 19, stock) || !cross_price4 || !match_number) {
    return invalid_internal_field('Q', payload.size());
  }
  return DecodeResult::success(
      CrossTrade{header, *shares, stock, *cross_price4, *match_number, alpha_at(payload, 39)});
}

[[nodiscard]] DecodeResult decode_broken_trade(const std::span<const std::byte> payload,
                                               const MessageHeader& header) {
  const auto match_number = read_big_endian_u64(payload, 11);
  if (!match_number) {
    return invalid_internal_field('B', payload.size());
  }
  return DecodeResult::success(BrokenTrade{header, *match_number});
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
  case 'H':
    return decode_trading_action(payload, *header);
  case 'A':
    return decode_add_order(payload, *header);
  case 'F':
    return decode_add_order_with_attribution(payload, *header);
  case 'E':
    return decode_order_executed(payload, *header);
  case 'C':
    return decode_order_executed_with_price(payload, *header);
  case 'X':
    return decode_order_cancel(payload, *header);
  case 'D':
    return decode_order_delete(payload, *header);
  case 'U':
    return decode_order_replace(payload, *header);
  case 'P':
    return decode_trade(payload, *header);
  case 'Q':
    return decode_cross_trade(payload, *header);
  case 'B':
    return decode_broken_trade(payload, *header);
  case 'Y':
  case 'L':
  case 'V':
  case 'W':
  case 'K':
  case 'I':
  case 'N':
  case 'J':
  case 'h':
    return DecodeResult::success(IgnoredMessage{*header, static_cast<char>(source_type)});
  default:
    return invalid_internal_field(source_type, payload.size());
  }
}

} // namespace itchlab
