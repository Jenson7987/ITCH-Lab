#pragma once

#include <cstddef>
#include <cstdint>
#include <span>
#include <string_view>

namespace itchlab {

// Checked explicit little-endian encoding for versioned interchange files.
[[nodiscard]] bool encode_little_endian_u16(std::span<std::byte> output, std::size_t offset,
                                            std::uint16_t value) noexcept;
[[nodiscard]] bool encode_little_endian_u32(std::span<std::byte> output, std::size_t offset,
                                            std::uint32_t value) noexcept;
[[nodiscard]] bool encode_little_endian_u64(std::span<std::byte> output, std::size_t offset,
                                            std::uint64_t value) noexcept;
[[nodiscard]] bool encode_bytes(std::span<std::byte> output, std::size_t offset,
                                std::span<const std::byte> value) noexcept;

// Encodes ASCII text into an exact-width field, padding on the right. Invalid text or bounds leave
// the destination unchanged.
[[nodiscard]] bool encode_padded_ascii(std::span<std::byte> output, std::size_t offset,
                                       std::size_t width, std::string_view value,
                                       char padding = ' ') noexcept;

} // namespace itchlab
