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

TEST_CASE("UT-DEC-001 decodes exact independent S R A and D fields",
          "[TASK-005][UT-DEC-001][decoder]") {
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

  SECTION("Order Delete") {
    const auto payload = bytes_from_hex("44000100061f1aced9f7d000000000000003e9");
    const auto message = decode_as<itchlab::OrderDelete>(payload);
    REQUIRE(message.header == itchlab::MessageHeader{1, 6, 34'200'000'002'000});
    REQUIRE(message.order_reference == 1'001);
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

TEST_CASE("UT-DEC-002 rejects every wrong known length before field decoding",
          "[TASK-005][UT-DEC-002][decoder][security]") {
  struct Case {
    std::uint8_t source_type;
    std::size_t expected_length;
  };
  constexpr std::array cases{
      Case{'S', 12},
      Case{'R', 39},
      Case{'A', 36},
      Case{'D', 19},
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

TEST_CASE("TASK-005 decoder returns typed errors for unknown types and invalid fields",
          "[TASK-005][decoder][error]") {
  const itchlab::ItchDecoder decoder;

  SECTION("unknown type retains observed type and length") {
    constexpr std::array payload{std::byte{'Z'}, std::byte{0x01}, std::byte{0x02}};
    const auto result = decoder.decode(payload);
    REQUIRE(result.error->code == itchlab::ErrorCode::unknown_message);
    REQUIRE(result.error->source_type == static_cast<std::uint8_t>('Z'));
    REQUIRE(result.error->observed_length == payload.size());
    REQUIRE_FALSE(result.error->expected_length.has_value());
  }

  SECTION("later MVP type is unknown until TASK-009") {
    std::vector<std::byte> payload(25, std::byte{0});
    payload.front() = std::byte{'H'};
    const auto result = decoder.decode(payload);
    REQUIRE(result.error->code == itchlab::ErrorCode::unknown_message);
    REQUIRE(result.error->source_type == static_cast<std::uint8_t>('H'));
  }

  SECTION("timestamp at one complete day is invalid") {
    auto payload = bytes_from_hex("53000000010000000003e84f");
    write_big_endian(payload, 5, 6, itchlab::kNanosecondsPerDay);
    const auto result = decoder.decode(payload);
    REQUIRE(result.error->code == itchlab::ErrorCode::timestamp);
    REQUIRE(result.error->source_type == static_cast<std::uint8_t>('S'));
  }

  SECTION("Add Order side is a closed domain") {
    auto payload = maximum_add_payload();
    payload[19] = std::byte{'X'};
    const auto result = decoder.decode(payload);
    REQUIRE(result.error->code == itchlab::ErrorCode::invariant);
    REQUIRE(result.error->source_type == static_cast<std::uint8_t>('A'));
  }
}

TEST_CASE("TASK-005 decoder retains no state between calls", "[TASK-005][decoder][stateless]") {
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
