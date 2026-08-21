#include "itchlab/itch/decoder.hpp"

#include "itchlab/core/types.hpp"
#include "itchlab/itch/messages.hpp"

#include <catch2/catch_test_macros.hpp>

#include <array>
#include <cstddef>
#include <cstdint>
#include <limits>
#include <string>
#include <string_view>
#include <type_traits>
#include <utility>
#include <variant>
#include <vector>

namespace {

std::uint8_t hex_digit(const char value) {
  if (value >= '0' && value <= '9') {
    return static_cast<std::uint8_t>(value - '0');
  }
  if (value >= 'a' && value <= 'f') {
    return static_cast<std::uint8_t>(value - 'a' + 10);
  }
  FAIL("invalid hexadecimal test vector");
  return 0;
}

std::vector<std::byte> bytes_from_hex(const std::string_view hex) {
  REQUIRE((hex.size() % 2) == 0);
  std::vector<std::byte> bytes;
  bytes.reserve(hex.size() / 2);
  for (std::size_t index = 0; index < hex.size(); index += 2) {
    const auto value =
        static_cast<std::uint8_t>((hex_digit(hex[index]) << 4U) | hex_digit(hex[index + 1]));
    bytes.push_back(static_cast<std::byte>(value));
  }
  return bytes;
}

template <typename Message> Message decode_as(const std::span<const std::byte> payload) {
  const itchlab::ItchDecoder decoder;
  const auto result = decoder.decode(payload);
  REQUIRE(result.valid());
  REQUIRE(result.message.has_value());
  REQUIRE_FALSE(result.error.has_value());
  REQUIRE(std::holds_alternative<Message>(*result.message));
  return std::get<Message>(*result.message);
}

void write_big_endian(std::vector<std::byte>& payload, const std::size_t offset,
                      const std::size_t width, const std::uint64_t value) {
  REQUIRE(offset <= payload.size());
  REQUIRE(width <= payload.size() - offset);
  for (std::size_t index = 0; index < width; ++index) {
    const auto shift = static_cast<unsigned>((width - index - 1) * 8);
    payload[offset + index] = static_cast<std::byte>((value >> shift) & 0xffU);
  }
}

std::vector<std::byte> maximum_add_payload() {
  std::vector<std::byte> payload(36, std::byte{0});
  payload[0] = std::byte{'A'};
  write_big_endian(payload, 1, 2, std::numeric_limits<std::uint16_t>::max());
  write_big_endian(payload, 3, 2, std::numeric_limits<std::uint16_t>::max());
  write_big_endian(payload, 5, 6, itchlab::kNanosecondsPerDay - 1);
  write_big_endian(payload, 11, 8, std::numeric_limits<std::uint64_t>::max());
  payload[19] = std::byte{'S'};
  write_big_endian(payload, 20, 4, std::numeric_limits<std::uint32_t>::max());
  constexpr std::string_view stock{"MAXVALUE"};
  for (std::size_t index = 0; index < stock.size(); ++index) {
    payload[24 + index] = static_cast<std::byte>(stock[index]);
  }
  write_big_endian(payload, 32, 4, std::numeric_limits<std::uint32_t>::max());
  return payload;
}

} // namespace

TEST_CASE("UT-DEC-001 decodes exact independent fields for every MVP type",
          "[TASK-009][UT-DEC-001][decoder]") {
  SECTION("System Event") {
    const auto payload = bytes_from_hex("53000000010000000003e84f");
    const auto message = decode_as<itchlab::SystemEvent>(payload);
    REQUIRE(message.header == itchlab::MessageHeader{0, 1, 1'000});
    REQUIRE(message.event_code == 'O');
  }

  SECTION("Stock Directory") {
    const auto payload = bytes_from_hex(
        "52000100020000000007d04141504c20202020514e000000644e432020504e4e314e000000014e");
    const auto message = decode_as<itchlab::StockDirectory>(payload);
    REQUIRE(message.header == itchlab::MessageHeader{1, 2, 2'000});
    REQUIRE(std::string{itchlab::trimmed_alpha(message.stock)} == "AAPL");
    REQUIRE(message.stock[4] == ' ');
    REQUIRE(message.market_category == 'Q');
    REQUIRE(message.financial_status == 'N');
    REQUIRE(message.round_lot_size == 100);
    REQUIRE(message.round_lots_only == 'N');
    REQUIRE(message.issue_classification == 'C');
    REQUIRE(itchlab::trimmed_alpha(message.issue_sub_type).empty());
    REQUIRE(message.issue_sub_type == itchlab::IssueSubTypeField{' ', ' '});
    REQUIRE(message.authenticity == 'P');
    REQUIRE(message.short_sale_threshold_indicator == 'N');
    REQUIRE(message.ipo_flag == 'N');
    REQUIRE(message.luld_reference_price_tier == '1');
    REQUIRE(message.etp_flag == 'N');
    REQUIRE(message.etp_leverage_factor == 1);
    REQUIRE(message.inverse_indicator == 'N');
  }

  SECTION("Stock Trading Action") {
    const auto payload = bytes_from_hex("480001001b2d79883d20004141504c2020202048204c554450");
    const auto message = decode_as<itchlab::TradingAction>(payload);
    REQUIRE(message.header == itchlab::MessageHeader{1, 27, 50'000'000'000'000});
    REQUIRE(std::string{itchlab::trimmed_alpha(message.stock)} == "AAPL");
    REQUIRE(message.stock[4] == ' ');
    REQUIRE(message.trading_state == 'H');
    REQUIRE(message.reserved == ' ');
    REQUIRE(std::string{itchlab::trimmed_alpha(message.reason)} == "LUDP");
  }

  SECTION("Add Order") {
    const auto payload =
        bytes_from_hex("41000100051f1aced9f3e800000000000003e942000000644141504c20202020000f4240");
    const auto message = decode_as<itchlab::AddOrder>(payload);
    REQUIRE(message.header == itchlab::MessageHeader{1, 5, 34'200'000'001'000});
    REQUIRE(message.order_reference == 1'001);
    REQUIRE(message.side == itchlab::Side::buy);
    REQUIRE(message.shares == 100);
    REQUIRE(std::string{itchlab::trimmed_alpha(message.stock)} == "AAPL");
    REQUIRE(message.price4 == 1'000'000);
  }

  SECTION("Add Order With MPID Attribution") {
    const auto payload = bytes_from_hex(
        "460001000b1f1aced9f7d000000000000003ea53000000c84141504c20202020000f462854455354");
    const auto message = decode_as<itchlab::AddOrderWithAttribution>(payload);
    REQUIRE(message.header == itchlab::MessageHeader{1, 11, 34'200'000'002'000});
    REQUIRE(message.order_reference == 1'002);
    REQUIRE(message.side == itchlab::Side::sell);
    REQUIRE(message.shares == 200);
    REQUIRE(std::string{itchlab::trimmed_alpha(message.stock)} == "AAPL");
    REQUIRE(message.price4 == 1'001'000);
    REQUIRE(std::string{itchlab::trimmed_alpha(message.attribution)} == "TEST");
  }

  SECTION("Order Executed") {
    const auto payload =
        bytes_from_hex("450001000e1f1aceda038800000000000003e9000000280000000000001389");
    const auto message = decode_as<itchlab::OrderExecuted>(payload);
    REQUIRE(message.header == itchlab::MessageHeader{1, 14, 34'200'000'005'000});
    REQUIRE(message.order_reference == 1'001);
    REQUIRE(message.executed_shares == 40);
    REQUIRE(message.match_number == 5'001);
  }

  SECTION("Order Executed With Price") {
    const auto payload =
        bytes_from_hex("43000100111f1aceda0f4000000000000003ea00000032000000000000138a59000f45c4");
    const auto message = decode_as<itchlab::OrderExecutedWithPrice>(payload);
    REQUIRE(message.header == itchlab::MessageHeader{1, 17, 34'200'000'008'000});
    REQUIRE(message.order_reference == 1'002);
    REQUIRE(message.executed_shares == 50);
    REQUIRE(message.match_number == 5'002);
    REQUIRE(message.printable == 'Y');
    REQUIRE(message.execution_price4 == 1'000'900);
  }

  SECTION("Order Cancel") {
    const auto payload = bytes_from_hex("580001000f1f1aceda077000000000000003e90000000a");
    const auto message = decode_as<itchlab::OrderCancel>(payload);
    REQUIRE(message.header == itchlab::MessageHeader{1, 15, 34'200'000'006'000});
    REQUIRE(message.order_reference == 1'001);
    REQUIRE(message.cancelled_shares == 10);
  }

  SECTION("Order Delete") {
    const auto payload = bytes_from_hex("44000100061f1aced9f7d000000000000003e9");
    const auto message = decode_as<itchlab::OrderDelete>(payload);
    REQUIRE(message.header == itchlab::MessageHeader{1, 6, 34'200'000'002'000});
    REQUIRE(message.order_reference == 1'001);
  }

  SECTION("Order Replace") {
    const auto payload =
        bytes_from_hex("55000100121f1aceda132800000000000003ea00000000000003eb0000007d000f468c");
    const auto message = decode_as<itchlab::OrderReplace>(payload);
    REQUIRE(message.header == itchlab::MessageHeader{1, 18, 34'200'000'009'000});
    REQUIRE(message.original_order_reference == 1'002);
    REQUIRE(message.new_order_reference == 1'003);
    REQUIRE(message.shares == 125);
    REQUIRE(message.price4 == 1'001'100);
  }

  SECTION("Trade") {
    const auto payload = bytes_from_hex(
        "50000100181f1aceda2a980000000000000000420000004b4141504c20202020000f44340000000000001771");
    const auto message = decode_as<itchlab::Trade>(payload);
    REQUIRE(message.header == itchlab::MessageHeader{1, 24, 34'200'000'015'000});
    REQUIRE(message.order_reference == 0);
    REQUIRE(message.buy_sell_indicator == itchlab::Side::buy);
    REQUIRE(message.shares == 75);
    REQUIRE(std::string{itchlab::trimmed_alpha(message.stock)} == "AAPL");
    REQUIRE(message.price4 == 1'000'500);
    REQUIRE(message.match_number == 6'001);
  }

  SECTION("Cross Trade") {
    const auto payload = bytes_from_hex(
        "510002001a1f1aceda326800000000000003e84d53465420202020001e84800000000000001b594f");
    const auto message = decode_as<itchlab::CrossTrade>(payload);
    REQUIRE(message.header == itchlab::MessageHeader{2, 26, 34'200'000'017'000});
    REQUIRE(message.shares == 1'000);
    REQUIRE(std::string{itchlab::trimmed_alpha(message.stock)} == "MSFT");
    REQUIRE(message.cross_price4 == 2'000'000);
    REQUIRE(message.match_number == 7'001);
    REQUIRE(message.cross_type == 'O');
  }

  SECTION("Broken Trade") {
    const auto payload = bytes_from_hex("42000100191f1aceda2e800000000000001771");
    const auto message = decode_as<itchlab::BrokenTrade>(payload);
    REQUIRE(message.header == itchlab::MessageHeader{1, 25, 34'200'000'016'000});
    REQUIRE(message.match_number == 6'001);
  }
}

TEST_CASE("UT-DEC-001 preserves maximum integer fields and six-byte timestamp",
          "[TASK-005][UT-DEC-001][decoder][boundary]") {
  const auto message = decode_as<itchlab::AddOrder>(maximum_add_payload());
  REQUIRE(message.header.stock_locate == std::numeric_limits<std::uint16_t>::max());
  REQUIRE(message.header.tracking_number == std::numeric_limits<std::uint16_t>::max());
  REQUIRE(message.header.timestamp_ns == itchlab::kNanosecondsPerDay - 1);
  REQUIRE(message.order_reference == std::numeric_limits<std::uint64_t>::max());
  REQUIRE(message.side == itchlab::Side::sell);
  REQUIRE(message.shares == std::numeric_limits<std::uint32_t>::max());
  REQUIRE(std::string{itchlab::trimmed_alpha(message.stock)} == "MAXVALUE");
  REQUIRE(message.price4 == std::numeric_limits<std::uint32_t>::max());
}

TEST_CASE("TASK-009 remaining decoders preserve wire-width integer boundaries",
          "[TASK-009][UT-DEC-001][decoder][boundary]") {
  constexpr auto max_u32 = std::numeric_limits<std::uint32_t>::max();
  constexpr auto max_u64 = std::numeric_limits<std::uint64_t>::max();

  SECTION("Stock Directory") {
    auto payload = bytes_from_hex(
        "52000100020000000007d04141504c20202020514e000000644e432020504e4e314e000000014e");
    write_big_endian(payload, 21, 4, max_u32);
    write_big_endian(payload, 34, 4, max_u32);
    const auto message = decode_as<itchlab::StockDirectory>(payload);
    REQUIRE(message.round_lot_size == max_u32);
    REQUIRE(message.etp_leverage_factor == max_u32);
  }

  SECTION("Add Order With MPID Attribution") {
    auto payload = bytes_from_hex(
        "460001000b1f1aced9f7d000000000000003ea53000000c84141504c20202020000f462854455354");
    write_big_endian(payload, 11, 8, max_u64);
    write_big_endian(payload, 20, 4, max_u32);
    write_big_endian(payload, 32, 4, max_u32);
    const auto message = decode_as<itchlab::AddOrderWithAttribution>(payload);
    REQUIRE(message.order_reference == max_u64);
    REQUIRE(message.shares == max_u32);
    REQUIRE(message.price4 == max_u32);
  }

  SECTION("Order Executed") {
    auto payload = bytes_from_hex("450001000e1f1aceda038800000000000003e9000000280000000000001389");
    write_big_endian(payload, 11, 8, max_u64);
    write_big_endian(payload, 19, 4, max_u32);
    write_big_endian(payload, 23, 8, max_u64);
    const auto message = decode_as<itchlab::OrderExecuted>(payload);
    REQUIRE(message.order_reference == max_u64);
    REQUIRE(message.executed_shares == max_u32);
    REQUIRE(message.match_number == max_u64);
  }

  SECTION("Order Executed With Price") {
    auto payload =
        bytes_from_hex("43000100111f1aceda0f4000000000000003ea00000032000000000000138a59000f45c4");
    write_big_endian(payload, 11, 8, max_u64);
    write_big_endian(payload, 19, 4, max_u32);
    write_big_endian(payload, 23, 8, max_u64);
    write_big_endian(payload, 32, 4, max_u32);
    const auto message = decode_as<itchlab::OrderExecutedWithPrice>(payload);
    REQUIRE(message.order_reference == max_u64);
    REQUIRE(message.executed_shares == max_u32);
    REQUIRE(message.match_number == max_u64);
    REQUIRE(message.execution_price4 == max_u32);
  }

  SECTION("Order Cancel") {
    auto payload = bytes_from_hex("580001000f1f1aceda077000000000000003e90000000a");
    write_big_endian(payload, 11, 8, max_u64);
    write_big_endian(payload, 19, 4, max_u32);
    const auto message = decode_as<itchlab::OrderCancel>(payload);
    REQUIRE(message.order_reference == max_u64);
    REQUIRE(message.cancelled_shares == max_u32);
  }

  SECTION("Order Replace") {
    auto payload =
        bytes_from_hex("55000100121f1aceda132800000000000003ea00000000000003eb0000007d000f468c");
    write_big_endian(payload, 11, 8, max_u64);
    write_big_endian(payload, 19, 8, max_u64);
    write_big_endian(payload, 27, 4, max_u32);
    write_big_endian(payload, 31, 4, max_u32);
    const auto message = decode_as<itchlab::OrderReplace>(payload);
    REQUIRE(message.original_order_reference == max_u64);
    REQUIRE(message.new_order_reference == max_u64);
    REQUIRE(message.shares == max_u32);
    REQUIRE(message.price4 == max_u32);
  }

  SECTION("Trade") {
    auto payload = bytes_from_hex(
        "50000100181f1aceda2a980000000000000000420000004b4141504c20202020000f44340000000000001771");
    write_big_endian(payload, 11, 8, max_u64);
    write_big_endian(payload, 20, 4, max_u32);
    write_big_endian(payload, 32, 4, max_u32);
    write_big_endian(payload, 36, 8, max_u64);
    const auto message = decode_as<itchlab::Trade>(payload);
    REQUIRE(message.order_reference == max_u64);
    REQUIRE(message.shares == max_u32);
    REQUIRE(message.price4 == max_u32);
    REQUIRE(message.match_number == max_u64);
  }

  SECTION("Cross Trade") {
    auto payload = bytes_from_hex(
        "510002001a1f1aceda326800000000000003e84d53465420202020001e84800000000000001b594f");
    write_big_endian(payload, 11, 8, max_u64);
    write_big_endian(payload, 27, 4, max_u32);
    write_big_endian(payload, 31, 8, max_u64);
    const auto message = decode_as<itchlab::CrossTrade>(payload);
    REQUIRE(message.shares == max_u64);
    REQUIRE(message.cross_price4 == max_u32);
    REQUIRE(message.match_number == max_u64);
  }

  SECTION("Broken Trade") {
    auto payload = bytes_from_hex("42000100191f1aceda2e800000000000001771");
    write_big_endian(payload, 11, 8, max_u64);
    const auto message = decode_as<itchlab::BrokenTrade>(payload);
    REQUIRE(message.match_number == max_u64);
  }
}

TEST_CASE("TASK-009 accepts minimum raw integer values and zero-share crosses",
          "[TASK-009][UT-DEC-001][decoder][boundary]") {
  auto cross_payload = bytes_from_hex(
      "510002001a1f1aceda326800000000000003e84d53465420202020001e84800000000000001b594f");
  write_big_endian(cross_payload, 1, 2, 0);
  write_big_endian(cross_payload, 3, 2, 0);
  write_big_endian(cross_payload, 5, 6, 0);
  write_big_endian(cross_payload, 11, 8, 0);
  write_big_endian(cross_payload, 27, 4, 0);
  write_big_endian(cross_payload, 31, 8, 0);
  const auto cross = decode_as<itchlab::CrossTrade>(cross_payload);
  REQUIRE(cross.header == itchlab::MessageHeader{0, 0, 0});
  REQUIRE(cross.shares == 0);
  REQUIRE(cross.cross_price4 == 0);
  REQUIRE(cross.match_number == 0);

  auto trade_payload = bytes_from_hex(
      "50000100181f1aceda2a980000000000000000420000004b4141504c20202020000f44340000000000001771");
  write_big_endian(trade_payload, 11, 8, 0);
  write_big_endian(trade_payload, 20, 4, 0);
  write_big_endian(trade_payload, 32, 4, 0);
  write_big_endian(trade_payload, 36, 8, 0);
  const auto trade = decode_as<itchlab::Trade>(trade_payload);
  REQUIRE(trade.order_reference == 0);
  REQUIRE(trade.shares == 0);
  REQUIRE(trade.price4 == 0);
  REQUIRE(trade.match_number == 0);
}

TEST_CASE("TASK-009 validates six-byte timestamps for every MVP type",
          "[TASK-009][UT-DEC-002][decoder][boundary]") {
  struct Case {
    std::string_view payload_hex;
    std::uint8_t source_type;
  };
  constexpr std::array cases{
      Case{"53000000010000000003e84f", 'S'},
      Case{"52000100020000000007d04141504c20202020514e000000644e432020504e4e314e000000014e", 'R'},
      Case{"48000100061f1aced9e4484141504c20202020542020202020", 'H'},
      Case{"410001000a1f1aced9f3e800000000000003e942000000644141504c20202020000f4240", 'A'},
      Case{"460001000b1f1aced9f7d000000000000003ea53000000c84141504c20202020000f462854455354", 'F'},
      Case{"450001000e1f1aceda038800000000000003e9000000280000000000001389", 'E'},
      Case{"43000100111f1aceda0f4000000000000003ea00000032000000000000138a59000f45c4", 'C'},
      Case{"580001000f1f1aceda077000000000000003e90000000a", 'X'},
      Case{"44000100101f1aceda0b5800000000000003e9", 'D'},
      Case{"55000100121f1aceda132800000000000003ea00000000000003eb0000007d000f468c", 'U'},
      Case{"50000100181f1aceda2a980000000000000000420000004b4141504c20202020000f4434000000000000177"
           "1",
           'P'},
      Case{"510002001a1f1aceda326800000000000003e84d53465420202020001e84800000000000001b594f", 'Q'},
      Case{"42000100191f1aceda2e800000000000001771", 'B'},
  };

  const itchlab::ItchDecoder decoder;
  for (const auto& test_case : cases) {
    auto payload = bytes_from_hex(test_case.payload_hex);
    write_big_endian(payload, 1, 2, std::numeric_limits<std::uint16_t>::max());
    write_big_endian(payload, 3, 2, std::numeric_limits<std::uint16_t>::max());
    write_big_endian(payload, 5, 6, itchlab::kNanosecondsPerDay - 1);
    const auto maximum = decoder.decode(payload);
    REQUIRE(maximum.valid());
    const auto header = std::visit([](const auto& typed_message) { return typed_message.header; },
                                   *maximum.message);
    REQUIRE(header.stock_locate == std::numeric_limits<std::uint16_t>::max());
    REQUIRE(header.tracking_number == std::numeric_limits<std::uint16_t>::max());
    REQUIRE(header.timestamp_ns == itchlab::kNanosecondsPerDay - 1);

    write_big_endian(payload, 5, 6, itchlab::kNanosecondsPerDay);
    const auto invalid = decoder.decode(payload);
    REQUIRE(invalid.error->code == itchlab::ErrorCode::timestamp);
    REQUIRE(invalid.error->source_type == test_case.source_type);
  }
}

TEST_CASE("TASK-031 structurally validates and types known non-book messages",
          "[TASK-031][decoder][boundary]") {
  struct Case {
    std::uint8_t source_type;
    std::size_t expected_length;
  };
  constexpr std::array cases{
      Case{'Y', 20}, Case{'L', 26}, Case{'V', 35}, Case{'W', 12}, Case{'K', 28},
      Case{'I', 50}, Case{'N', 20}, Case{'J', 35}, Case{'h', 21},
  };
  const itchlab::ItchDecoder decoder;

  for (const auto& test_case : cases) {
    std::vector<std::byte> payload(test_case.expected_length, std::byte{0});
    payload.front() = static_cast<std::byte>(test_case.source_type);
    write_big_endian(payload, 1, 2, 7);
    write_big_endian(payload, 3, 2, 11);
    write_big_endian(payload, 5, 6, itchlab::kNanosecondsPerDay - 1);

    const auto result = decoder.decode(payload);
    REQUIRE(result.valid());
    const auto message = std::get<itchlab::IgnoredMessage>(*result.message);
    REQUIRE(message.header == itchlab::MessageHeader{7, 11, itchlab::kNanosecondsPerDay - 1});
    REQUIRE(message.source_type == static_cast<char>(test_case.source_type));

    write_big_endian(payload, 5, 6, itchlab::kNanosecondsPerDay);
    const auto invalid = decoder.decode(payload);
    REQUIRE(invalid.error->code == itchlab::ErrorCode::timestamp);
    REQUIRE(invalid.error->source_type == test_case.source_type);
  }
}

TEST_CASE("UT-DEC-002 rejects every wrong known length before field decoding",
          "[TASK-009][UT-DEC-002][decoder][security]") {
  struct Case {
    std::uint8_t source_type;
    std::size_t expected_length;
  };
  constexpr std::array cases{
      Case{'S', 12}, Case{'R', 39}, Case{'H', 25}, Case{'A', 36}, Case{'F', 40}, Case{'E', 31},
      Case{'C', 36}, Case{'X', 23}, Case{'D', 19}, Case{'U', 35}, Case{'P', 44}, Case{'Q', 40},
      Case{'B', 19}, Case{'Y', 20}, Case{'L', 26}, Case{'V', 35}, Case{'W', 12}, Case{'K', 28},
      Case{'I', 50}, Case{'N', 20}, Case{'J', 35}, Case{'h', 21},
  };
  const itchlab::ItchDecoder decoder;

  const auto empty = decoder.decode({});
  REQUIRE(empty.error->code == itchlab::ErrorCode::message_length);
  REQUIRE(empty.error->observed_length == 0);
  REQUIRE_FALSE(empty.error->source_type.has_value());
  REQUIRE_FALSE(empty.error->expected_length.has_value());

  for (const auto& test_case : cases) {
    for (std::size_t observed_length = 1; observed_length < test_case.expected_length;
         ++observed_length) {
      std::vector<std::byte> payload(observed_length, std::byte{0xff});
      payload.front() = static_cast<std::byte>(test_case.source_type);
      const auto result = decoder.decode(payload);
      REQUIRE_FALSE(result.message.has_value());
      REQUIRE(result.error->code == itchlab::ErrorCode::message_length);
      REQUIRE(result.error->source_type == test_case.source_type);
      REQUIRE(result.error->observed_length == observed_length);
      REQUIRE(result.error->expected_length == test_case.expected_length);
    }

    std::vector<std::byte> oversized(test_case.expected_length + 1, std::byte{0xff});
    oversized.front() = static_cast<std::byte>(test_case.source_type);
    const auto result = decoder.decode(oversized);
    REQUIRE(result.error->code == itchlab::ErrorCode::message_length);
    REQUIRE(result.error->source_type == test_case.source_type);
    REQUIRE(result.error->observed_length == test_case.expected_length + 1);
    REQUIRE(result.error->expected_length == test_case.expected_length);
  }
}

TEST_CASE("TASK-009 decoder returns typed errors for unknown types and invalid fields",
          "[TASK-009][decoder][error]") {
  const itchlab::ItchDecoder decoder;

  SECTION("unknown type retains observed type and length") {
    constexpr std::array payload{std::byte{'Z'}, std::byte{0x01}, std::byte{0x02}};
    const auto result = decoder.decode(payload);
    REQUIRE(result.error->code == itchlab::ErrorCode::unknown_message);
    REQUIRE(result.error->source_type == static_cast<std::uint8_t>('Z'));
    REQUIRE(result.error->observed_length == payload.size());
    REQUIRE_FALSE(result.error->expected_length.has_value());
  }

  SECTION("timestamp at one complete day is invalid") {
    auto payload = bytes_from_hex("53000000010000000003e84f");
    write_big_endian(payload, 5, 6, itchlab::kNanosecondsPerDay);
    const auto result = decoder.decode(payload);
    REQUIRE(result.error->code == itchlab::ErrorCode::timestamp);
    REQUIRE(result.error->source_type == static_cast<std::uint8_t>('S'));
  }

  SECTION("buy/sell indicators are a closed domain") {
    constexpr std::array cases{
        std::pair{std::string_view{
                      "410001000a1f1aced9f3e800000000000003e942000000644141504c20202020000f4240"},
                  static_cast<std::uint8_t>('A')},
        std::pair{
            std::string_view{
                "460001000b1f1aced9f7d000000000000003ea53000000c84141504c20202020000f462854455354"},
            static_cast<std::uint8_t>('F')},
        std::pair{std::string_view{"50000100181f1aceda2a980000000000000000420000004b4141504c2020202"
                                   "0000f44340000000000001771"},
                  static_cast<std::uint8_t>('P')},
    };
    for (const auto& [hex, source_type] : cases) {
      auto payload = bytes_from_hex(hex);
      payload[19] = std::byte{'X'};
      const auto result = decoder.decode(payload);
      REQUIRE(result.error->code == itchlab::ErrorCode::invariant);
      REQUIRE(result.error->source_type == source_type);
    }
  }
}

TEST_CASE("TASK-009 decoder retains no state between calls", "[TASK-009][decoder][stateless]") {
  STATIC_REQUIRE(std::is_empty_v<itchlab::ItchDecoder>);
  const itchlab::ItchDecoder decoder;
  const auto add_payload = maximum_add_payload();
  const auto delete_payload = bytes_from_hex("44000100061f1aced9f7d000000000000003e9");

  const auto first = decoder.decode(add_payload);
  const auto middle = decoder.decode(delete_payload);
  const auto second = decoder.decode(add_payload);
  REQUIRE(first.message == second.message);
  REQUIRE(middle.valid());
}
