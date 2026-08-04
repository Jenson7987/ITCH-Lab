#include "itchlab/itch/byte_decode.hpp"

#include <cstddef>
#include <cstdint>
#include <optional>
#include <span>

namespace itchlab {
namespace {

[[nodiscard]] bool contains_range(const std::span<const std::byte> input, const std::size_t offset,
                                  const std::size_t width) noexcept {
  return offset <= input.size() && width <= input.size() - offset;
}

[[nodiscard]] std::optional<std::uint64_t>
read_big_endian_unsigned(const std::span<const std::byte> input, const std::size_t offset,
                         const std::size_t width) noexcept {
  if (!contains_range(input, offset, width)) {
    return std::nullopt;
  }

  std::uint64_t value{};
  for (std::size_t index = 0; index < width; ++index) {
    value = (value << 8U) | std::to_integer<std::uint8_t>(input[offset + index]);
  }
  return value;
}

} // namespace

std::optional<std::uint16_t> read_big_endian_u16(const std::span<const std::byte> input,
                                                 const std::size_t offset) noexcept {
  const auto value = read_big_endian_unsigned(input, offset, 2);
  if (!value) {
    return std::nullopt;
  }
  return static_cast<std::uint16_t>(*value);
}

std::optional<std::uint32_t> read_big_endian_u32(const std::span<const std::byte> input,
                                                 const std::size_t offset) noexcept {
  const auto value = read_big_endian_unsigned(input, offset, 4);
  if (!value) {
    return std::nullopt;
  }
  return static_cast<std::uint32_t>(*value);
}

std::optional<std::uint64_t> read_big_endian_u48(const std::span<const std::byte> input,
                                                 const std::size_t offset) noexcept {
  return read_big_endian_unsigned(input, offset, 6);
}

std::optional<std::uint64_t> read_big_endian_u64(const std::span<const std::byte> input,
                                                 const std::size_t offset) noexcept {
  return read_big_endian_unsigned(input, offset, 8);
}

bool read_alpha(const std::span<const std::byte> input, const std::size_t offset,
                const std::span<char> destination) noexcept {
  if (!contains_range(input, offset, destination.size())) {
    return false;
  }
  for (std::size_t index = 0; index < destination.size(); ++index) {
    destination[index] = static_cast<char>(std::to_integer<unsigned char>(input[offset + index]));
  }
  return true;
}

} // namespace itchlab
