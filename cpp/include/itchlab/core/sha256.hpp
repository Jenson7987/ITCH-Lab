#pragma once

#include "itchlab/core/types.hpp"

#include <optional>
#include <span>
#include <string>
#include <string_view>

namespace itchlab {

// FIPS 180-4 SHA-256 used for deterministic artefact integrity, not authentication.
[[nodiscard]] ContentHash sha256(std::span<const std::byte> input) noexcept;
[[nodiscard]] ContentHash sha256(std::string_view input) noexcept;
[[nodiscard]] std::string content_hash_to_hex(const ContentHash& hash);
[[nodiscard]] std::optional<ContentHash> content_hash_from_hex(std::string_view value) noexcept;

} // namespace itchlab
