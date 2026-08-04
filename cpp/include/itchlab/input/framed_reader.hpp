#pragma once

#include "itchlab/core/types.hpp"
#include "itchlab/input/byte_source.hpp"

#include <array>
#include <cstddef>
#include <cstdint>
#include <optional>
#include <span>
#include <string>

namespace itchlab {

inline constexpr std::size_t kMaximumFramePayload = 512;

struct Frame {
  MessageIndex message_index{};
  std::uint64_t source_offset{};
  std::span<const std::byte> payload;
};

struct FrameError {
  ErrorCode code{ErrorCode::framing};
  MessageIndex message_index{};
  std::uint64_t source_offset{};
  std::string message;

  friend bool operator==(const FrameError&, const FrameError&) = default;
};

struct FrameReadResult {
  std::optional<Frame> frame;
  std::optional<FrameError> error;

  [[nodiscard]] bool end_of_file() const noexcept {
    return !frame.has_value() && !error.has_value();
  }
};

class FramedMessageReader {
public:
  explicit FramedMessageReader(ByteSource& source) noexcept;

  // The returned payload remains valid until the next call. Null frame/error means clean EOF.
  [[nodiscard]] FrameReadResult next();

private:
  [[nodiscard]] FrameReadResult fail(ErrorCode code, std::string message);
  [[nodiscard]] FrameReadResult fail(SourceError error);

  ByteSource& source_;
  std::array<std::byte, kMaximumFramePayload> payload_{};
  MessageIndex next_message_index_{};
  std::uint64_t next_source_offset_{};
  bool end_of_file_{};
  std::optional<FrameError> terminal_error_;
};

} // namespace itchlab
