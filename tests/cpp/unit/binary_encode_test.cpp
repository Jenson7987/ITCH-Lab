#include "itchlab/output/binary_encode.hpp"

#include <catch2/catch_test_macros.hpp>

#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdint>
#include <limits>

TEST_CASE("TASK-013 little-endian helpers encode exact bytes",
          "[TASK-013][UT-OUT-001][binary][endian]") {
  std::array<std::byte, 16> output{};

  REQUIRE(itchlab::encode_little_endian_u16(output, 0, 0x0123U));
  REQUIRE(itchlab::encode_little_endian_u32(output, 2, 0x456789abU));
  REQUIRE(itchlab::encode_little_endian_u64(output, 6, 0xcdef0123456789abULL));
  REQUIRE(itchlab::encode_padded_ascii(output, 14, 2, "Z"));

  const std::array expected{
      std::byte{0x23}, std::byte{0x01}, std::byte{0xab}, std::byte{0x89},
      std::byte{0x67}, std::byte{0x45}, std::byte{0xab}, std::byte{0x89},
      std::byte{0x67}, std::byte{0x45}, std::byte{0x23}, std::byte{0x01},
      std::byte{0xef}, std::byte{0xcd}, std::byte{'Z'},  std::byte{' '},
  };
  REQUIRE(std::ranges::equal(output, expected));
}

TEST_CASE("TASK-013 binary encoders reject invalid ranges atomically",
          "[TASK-013][UT-OUT-001][binary][bounds]") {
  std::array output{std::byte{0xaa}, std::byte{0xbb}, std::byte{0xcc}, std::byte{0xdd}};
  const auto original = output;

  REQUIRE_FALSE(itchlab::encode_little_endian_u16(output, 3, 1));
  REQUIRE_FALSE(itchlab::encode_little_endian_u32(output, 1, 1));
  REQUIRE_FALSE(itchlab::encode_little_endian_u64(output, 0, 1));
  REQUIRE_FALSE(
      itchlab::encode_little_endian_u16(output, std::numeric_limits<std::size_t>::max(), 1));
  REQUIRE_FALSE(itchlab::encode_padded_ascii(output, 0, 3, "four"));
  REQUIRE_FALSE(itchlab::encode_padded_ascii(output, 0, 2, "\xc2\xa3"));
  REQUIRE(std::ranges::equal(output, original));
}
