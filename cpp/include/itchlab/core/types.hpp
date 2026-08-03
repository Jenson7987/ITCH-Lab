#pragma once

#include <array>
#include <concepts>
#include <cstddef>
#include <cstdint>
#include <limits>
#include <optional>
#include <type_traits>
#include <utility>

namespace itchlab {

using MessageIndex = std::uint64_t;
using TimestampNs = std::uint64_t;
using StockLocate = std::uint16_t;
using SymbolId = std::uint16_t;
using OrderReference = std::uint64_t;
using MatchNumber = std::uint64_t;
using Price4 = std::uint32_t;
using Shares = std::uint64_t;
using Microusd = std::int64_t;
using RandomSeed = std::uint64_t;
using TradingDate = std::uint32_t;
using ContentHash = std::array<std::byte, 32>;

enum class Side : std::int8_t {
  sell = -1,
  not_applicable = 0,
  buy = 1,
};

inline constexpr TimestampNs kNanosecondsPerDay = 86'400'000'000'000ULL;
inline constexpr std::uint32_t kPriceScale = 10'000U;
inline constexpr RandomSeed kMaxJsonInteger = 9'007'199'254'740'991ULL;

[[nodiscard]] constexpr bool is_valid_timestamp(const TimestampNs value) noexcept {
  return value < kNanosecondsPerDay;
}

template <std::integral To, std::integral From>
  requires(!std::same_as<std::remove_cv_t<To>, bool> && !std::same_as<std::remove_cv_t<From>, bool>)
[[nodiscard]] constexpr std::optional<To> checked_integral_cast(const From value) noexcept {
  if (!std::in_range<To>(value)) {
    return std::nullopt;
  }
  return static_cast<To>(value);
}

template <std::integral T>
  requires(!std::same_as<std::remove_cv_t<T>, bool>)
[[nodiscard]] constexpr std::optional<T> checked_add(const T lhs, const T rhs) noexcept {
  if constexpr (std::is_unsigned_v<T>) {
    if (rhs > std::numeric_limits<T>::max() - lhs) {
      return std::nullopt;
    }
  } else {
    if ((rhs > 0 && lhs > std::numeric_limits<T>::max() - rhs) ||
        (rhs < 0 && lhs < std::numeric_limits<T>::min() - rhs)) {
      return std::nullopt;
    }
  }
  return static_cast<T>(lhs + rhs);
}

template <std::integral T>
  requires(!std::same_as<std::remove_cv_t<T>, bool>)
[[nodiscard]] constexpr std::optional<T> checked_subtract(const T lhs, const T rhs) noexcept {
  if constexpr (std::is_unsigned_v<T>) {
    if (rhs > lhs) {
      return std::nullopt;
    }
  } else {
    if ((rhs > 0 && lhs < std::numeric_limits<T>::min() + rhs) ||
        (rhs < 0 && lhs > std::numeric_limits<T>::max() + rhs)) {
      return std::nullopt;
    }
  }
  return static_cast<T>(lhs - rhs);
}

template <std::integral T>
  requires(!std::same_as<std::remove_cv_t<T>, bool>)
[[nodiscard]] constexpr std::optional<T> checked_multiply(const T lhs, const T rhs) noexcept {
  if (lhs == 0 || rhs == 0) {
    return T{0};
  }

  if constexpr (std::is_unsigned_v<T>) {
    if (lhs > std::numeric_limits<T>::max() / rhs) {
      return std::nullopt;
    }
  } else if ((lhs > 0 && rhs > 0 && lhs > std::numeric_limits<T>::max() / rhs) ||
             (lhs > 0 && rhs < 0 && rhs < std::numeric_limits<T>::min() / lhs) ||
             (lhs < 0 && rhs > 0 && lhs < std::numeric_limits<T>::min() / rhs) ||
             (lhs < 0 && rhs < 0 && lhs < std::numeric_limits<T>::max() / rhs)) {
    return std::nullopt;
  }

  return static_cast<T>(lhs * rhs);
}

} // namespace itchlab
