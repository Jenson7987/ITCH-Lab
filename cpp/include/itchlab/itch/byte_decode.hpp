#pragma once

#include <cstddef>
#include <cstdint>
#include <optional>
#include <span>

namespace itchlab {

[[nodiscard]] std::optional<std::uint16_t> read_big_endian_u16(std::span<const std::byte> input,
                                                               std::size_t offset) noexcept;
[[nodiscard]] std::optional<std::uint32_t> read_big_endian_u32(std::span<const std::byte> input,
                                                               std::size_t offset) noexcept;
[[nodiscard]] std::optional<std::uint64_t> read_big_endian_u48(std::span<const std::byte> input,
                                                               std::size_t offset) noexcept;
[[nodiscard]] std::optional<std::uint64_t> read_big_endian_u64(std::span<const std::byte> input,
                                                               std::size_t offset) noexcept;

// Copies an exact fixed-width alpha field without trimming or interpreting its bytes.
[[nodiscard]] bool read_alpha(std::span<const std::byte> input, std::size_t offset,
                              std::span<char> destination) noexcept;

} // namespace itchlab
