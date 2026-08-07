#pragma once

#include "itchlab/core/types.hpp"

#include <array>
#include <cstddef>
#include <cstdint>
#include <optional>
#include <span>
#include <string>
#include <string_view>

namespace itchlab {

// Incremental FIPS 180-4 SHA-256 for bounded file/artefact hashing.
class Sha256Hasher {
public:
  [[nodiscard]] bool update(std::span<const std::byte> input) noexcept;
  [[nodiscard]] std::optional<ContentHash> finalise() noexcept;

private:
  std::array<std::uint32_t, 8> state_{0x6a09e667U, 0xbb67ae85U, 0x3c6ef372U, 0xa54ff53aU,
                                      0x510e527fU, 0x9b05688cU, 0x1f83d9abU, 0x5be0cd19U};
  std::array<std::byte, 64> pending_{};
  std::size_t pending_size_{};
  std::uint64_t total_bytes_{};
  bool finalised_{};
};

// FIPS 180-4 SHA-256 used for deterministic artefact integrity, not authentication.
[[nodiscard]] ContentHash sha256(std::span<const std::byte> input) noexcept;
[[nodiscard]] ContentHash sha256(std::string_view input) noexcept;
[[nodiscard]] std::string content_hash_to_hex(const ContentHash& hash);
[[nodiscard]] std::optional<ContentHash> content_hash_from_hex(std::string_view value) noexcept;

} // namespace itchlab
