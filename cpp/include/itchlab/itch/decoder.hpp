#pragma once

#include "itchlab/core/errors.hpp"
#include "itchlab/itch/messages.hpp"

#include <cstddef>
#include <cstdint>
#include <optional>
#include <span>
#include <string>

namespace itchlab {

struct DecodeError {
  ErrorCode code{ErrorCode::message_length};
  std::optional<std::uint8_t> source_type;
  std::size_t observed_length{};
  std::optional<std::size_t> expected_length;
  std::string message;

  friend bool operator==(const DecodeError&, const DecodeError&) = default;
};

struct DecodeResult {
  std::optional<ItchMessage> message;
  std::optional<DecodeError> error;

  [[nodiscard]] static DecodeResult success(ItchMessage decoded_message);
  [[nodiscard]] static DecodeResult failure(DecodeError decode_error);
  [[nodiscard]] bool valid() const noexcept { return message.has_value() && !error.has_value(); }
};

class ItchDecoder {
public:
  // Decodes one unframed payload. Exact known-type length is checked before field access.
  [[nodiscard]] DecodeResult decode(std::span<const std::byte> payload) const;
};

} // namespace itchlab
