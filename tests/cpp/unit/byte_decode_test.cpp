#include "itchlab/itch/byte_decode.hpp"

#include <catch2/catch_test_macros.hpp>

#include <array>
#include <cstddef>
#include <cstdint>
#include <limits>

TEST_CASE("TASK-005 big-endian helpers decode exact widths",
          "[TASK-005][UT-DEC-001][decoder][endian]") {
  constexpr std::array input{
      std::byte{0x01}, std::byte{0x23}, std::byte{0x45}, std::byte{0x67},
      std::byte{0x89}, std::byte{0xab}, std::byte{0xcd}, std::byte{0xef},
  };

  REQUIRE(itchlab::read_big_endian_u16(input, 0) == 0x0123U);
  REQUIRE(itchlab::read_big_endian_u32(input, 1) == 0x23456789U);
  REQUIRE(itchlab::read_big_endian_u48(input, 2) == 0x456789abcdefULL);
  REQUIRE(itchlab::read_big_endian_u64(input, 0) == 0x0123456789abcdefULL);
}

TEST_CASE("TASK-005 byte helpers reject every insufficient range without partial alpha writes",
          "[TASK-005][UT-DEC-002][decoder][boundary]") {
  constexpr std::array input{std::byte{'A'}, std::byte{'B'}, std::byte{' '}};

  REQUIRE_FALSE(itchlab::read_big_endian_u16(input, 2).has_value());
  REQUIRE_FALSE(itchlab::read_big_endian_u32(input, 0).has_value());
  REQUIRE_FALSE(itchlab::read_big_endian_u48(input, 0).has_value());
  REQUIRE_FALSE(itchlab::read_big_endian_u64(input, 0).has_value());
  REQUIRE_FALSE(
      itchlab::read_big_endian_u16(input, std::numeric_limits<std::size_t>::max()).has_value());

  std::array destination{'x', 'x'};
  REQUIRE_FALSE(itchlab::read_alpha(input, 2, destination));
  REQUIRE(destination == std::array{'x', 'x'});
  REQUIRE_FALSE(itchlab::read_alpha(input, std::numeric_limits<std::size_t>::max(), destination));
  REQUIRE(destination == std::array{'x', 'x'});

  REQUIRE(itchlab::read_alpha(input, 0, destination));
  REQUIRE(destination == std::array{'A', 'B'});
}
