#include "itchlab/core/types.hpp"

#include <catch2/catch_test_macros.hpp>

#include <cstdint>
#include <limits>

TEST_CASE("TASK-002 domain primitives have fixed storage", "[TASK-002][types]") {
  STATIC_REQUIRE(sizeof(itchlab::MessageIndex) == 8);
  STATIC_REQUIRE(sizeof(itchlab::TimestampNs) == 8);
  STATIC_REQUIRE(sizeof(itchlab::StockLocate) == 2);
  STATIC_REQUIRE(sizeof(itchlab::SymbolId) == 2);
  STATIC_REQUIRE(sizeof(itchlab::OrderReference) == 8);
  STATIC_REQUIRE(sizeof(itchlab::MatchNumber) == 8);
  STATIC_REQUIRE(sizeof(itchlab::Price4) == 4);
  STATIC_REQUIRE(sizeof(itchlab::Shares) == 8);
  STATIC_REQUIRE(sizeof(itchlab::Microusd) == 8);
  STATIC_REQUIRE(sizeof(itchlab::Side) == 1);
  STATIC_REQUIRE(sizeof(itchlab::ContentHash) == 32);

  REQUIRE(static_cast<std::int8_t>(itchlab::Side::buy) == 1);
  REQUIRE(static_cast<std::int8_t>(itchlab::Side::sell) == -1);
  REQUIRE(static_cast<std::int8_t>(itchlab::Side::not_applicable) == 0);
}

TEST_CASE("TASK-002 checked integral casts reject narrowing and sign loss",
          "[TASK-002][types][boundary]") {
  REQUIRE(itchlab::checked_integral_cast<std::uint16_t>(65'535U) == 65'535U);
  REQUIRE_FALSE(itchlab::checked_integral_cast<std::uint16_t>(65'536U).has_value());
  REQUIRE_FALSE(itchlab::checked_integral_cast<std::uint64_t>(-1).has_value());
  REQUIRE(itchlab::checked_integral_cast<std::int64_t>(std::uint32_t{4'000'000'000U}) ==
          4'000'000'000LL);
}

TEST_CASE("TASK-002 checked arithmetic is atomic at integer boundaries",
          "[TASK-002][types][boundary]") {
  using Signed = std::int64_t;
  using Unsigned = std::uint64_t;
  constexpr auto signed_max = std::numeric_limits<Signed>::max();
  constexpr auto signed_min = std::numeric_limits<Signed>::min();
  constexpr auto unsigned_max = std::numeric_limits<Unsigned>::max();

  REQUIRE(itchlab::checked_add<Unsigned>(unsigned_max - 1, 1) == unsigned_max);
  REQUIRE_FALSE(itchlab::checked_add<Unsigned>(unsigned_max, 1).has_value());
  REQUIRE(itchlab::checked_add<Signed>(signed_min, 1) == signed_min + 1);
  REQUIRE_FALSE(itchlab::checked_add<Signed>(signed_max, 1).has_value());
  REQUIRE_FALSE(itchlab::checked_add<Signed>(signed_min, -1).has_value());

  REQUIRE(itchlab::checked_subtract<Unsigned>(1, 1) == 0);
  REQUIRE_FALSE(itchlab::checked_subtract<Unsigned>(0, 1).has_value());
  REQUIRE_FALSE(itchlab::checked_subtract<Signed>(signed_min, 1).has_value());
  REQUIRE_FALSE(itchlab::checked_subtract<Signed>(signed_max, -1).has_value());

  REQUIRE(itchlab::checked_multiply<Signed>(-3, -7) == 21);
  REQUIRE(itchlab::checked_multiply<Unsigned>(unsigned_max, 1) == unsigned_max);
  REQUIRE_FALSE(itchlab::checked_multiply<Unsigned>(unsigned_max, 2).has_value());
  REQUIRE_FALSE(itchlab::checked_multiply<Signed>(signed_min, -1).has_value());
  REQUIRE_FALSE(itchlab::checked_multiply<Signed>(signed_max, 2).has_value());
}

TEST_CASE("TASK-002 timestamps use the exchange-day half-open range",
          "[TASK-002][types][boundary]") {
  REQUIRE(itchlab::is_valid_timestamp(0));
  REQUIRE(itchlab::is_valid_timestamp(itchlab::kNanosecondsPerDay - 1));
  REQUIRE_FALSE(itchlab::is_valid_timestamp(itchlab::kNanosecondsPerDay));
}
