#include "itchlab/output/binary_encode.hpp"

#include <cstddef>
#include <cstdint>
#include <span>
#include <string_view>

namespace itchlab {
namespace {

[[nodiscard]] bool contains_range(const std::span<std::byte> output, const std::size_t offset,
                                  const std::size_t width) noexcept {
  return offset <= output.size() && width <= output.size() - offset;
}

template <typename Integer>
[[nodiscard]] bool encode_little_endian(const std::span<std::byte> output, const std::size_t offset,
                                        const Integer value) noexcept {
  if (!contains_range(output, offset, sizeof(Integer))) {
    return false;
  }
  for (std::size_t index = 0; index < sizeof(Integer); ++index) {
    const auto shift = static_cast<unsigned>(index * 8U);
    output[offset + index] = static_cast<std::byte>(static_cast<std::uint8_t>(value >> shift));
  }
  return true;
}

[[nodiscard]] bool is_ascii(const std::string_view value) noexcept {
  for (const char character : value) {
    const auto byte = static_cast<unsigned char>(character);
    if (byte > 0x7fU) {
      return false;
    }
  }
  return true;
}

} // namespace

bool encode_little_endian_u16(const std::span<std::byte> output, const std::size_t offset,
                              const std::uint16_t value) noexcept {
  return encode_little_endian(output, offset, value);
}

bool encode_little_endian_u32(const std::span<std::byte> output, const std::size_t offset,
                              const std::uint32_t value) noexcept {
  return encode_little_endian(output, offset, value);
}

bool encode_little_endian_u64(const std::span<std::byte> output, const std::size_t offset,
                              const std::uint64_t value) noexcept {
  return encode_little_endian(output, offset, value);
}

bool encode_bytes(const std::span<std::byte> output, const std::size_t offset,
                  const std::span<const std::byte> value) noexcept {
  if (!contains_range(output, offset, value.size())) {
    return false;
  }
  for (std::size_t index = 0; index < value.size(); ++index) {
    output[offset + index] = value[index];
  }
  return true;
}

bool encode_padded_ascii(const std::span<std::byte> output, const std::size_t offset,
                         const std::size_t width, const std::string_view value,
                         const char padding) noexcept {
  if (!contains_range(output, offset, width) || value.size() > width || !is_ascii(value) ||
      static_cast<unsigned char>(padding) > 0x7fU) {
    return false;
  }
  for (std::size_t index = 0; index < width; ++index) {
    const auto character = index < value.size() ? value[index] : padding;
    output[offset + index] = static_cast<std::byte>(static_cast<unsigned char>(character));
  }
  return true;
}

} // namespace itchlab
