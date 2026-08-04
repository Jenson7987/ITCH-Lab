#include "itchlab/input/framed_reader.hpp"

#include "itchlab/core/types.hpp"

#include <array>
#include <cstddef>
#include <cstdint>
#include <span>
#include <string>
#include <utility>

namespace itchlab {
namespace {

struct ExactReadResult {
  std::size_t bytes_read{};
  bool end_of_file{};
  std::optional<SourceError> error;
};

ExactReadResult read_exact(ByteSource& source, const std::span<std::byte> destination) {
  std::size_t total{};
  while (total < destination.size()) {
    auto result = source.read(destination.subspan(total));
    if (result.error) {
      return ExactReadResult{total, false, std::move(result.error)};
    }
    if (result.end_of_file) {
      return ExactReadResult{total, true, std::nullopt};
    }
    if (result.bytes_read == 0 || result.bytes_read > destination.size() - total) {
      return ExactReadResult{
          total, false,
          SourceError{ErrorCode::internal, "Byte source violated its bounded-read contract."}};
    }
    total += result.bytes_read;
  }
  return ExactReadResult{total, false, std::nullopt};
}

} // namespace

FramedMessageReader::FramedMessageReader(ByteSource& source) noexcept : source_{source} {}

FrameReadResult FramedMessageReader::fail(const ErrorCode code, std::string message) {
  terminal_error_ = FrameError{code, next_message_index_, next_source_offset_, std::move(message)};
  return FrameReadResult{std::nullopt, terminal_error_};
}

FrameReadResult FramedMessageReader::fail(SourceError error) {
  return fail(error.code, std::move(error.message));
}

FrameReadResult FramedMessageReader::next() {
  if (terminal_error_) {
    return FrameReadResult{std::nullopt, terminal_error_};
  }
  if (end_of_file_) {
    return FrameReadResult{};
  }

  std::array<std::byte, 2> prefix{};
  auto prefix_result = read_exact(source_, prefix);
  if (prefix_result.error) {
    return fail(std::move(*prefix_result.error));
  }
  if (prefix_result.end_of_file) {
    if (prefix_result.bytes_read == 0) {
      end_of_file_ = true;
      return FrameReadResult{};
    }
    return fail(ErrorCode::truncated_message,
                "Input ended within the two-byte frame-length prefix.");
  }

  const auto payload_length = static_cast<std::size_t>(
      (static_cast<std::uint16_t>(std::to_integer<std::uint8_t>(prefix[0])) << 8U) |
      static_cast<std::uint16_t>(std::to_integer<std::uint8_t>(prefix[1])));
  if (payload_length == 0) {
    return fail(ErrorCode::framing, "Frame payload length must be positive.");
  }
  if (payload_length > kMaximumFramePayload) {
    return fail(ErrorCode::framing, "Frame payload length exceeds the 512-byte project limit.");
  }

  auto payload_result = read_exact(source_, std::span{payload_}.first(payload_length));
  if (payload_result.error) {
    return fail(std::move(*payload_result.error));
  }
  if (payload_result.end_of_file) {
    return fail(ErrorCode::truncated_message, "Input ended within a declared frame payload.");
  }

  const auto frame_size = static_cast<std::uint64_t>(payload_length) + 2U;
  const auto following_offset = checked_add(next_source_offset_, frame_size);
  const auto following_index = checked_add(next_message_index_, MessageIndex{1});
  if (!following_offset || !following_index) {
    return fail(ErrorCode::internal, "Frame position counter overflowed.");
  }

  const Frame frame{next_message_index_, next_source_offset_,
                    std::span<const std::byte>{payload_.data(), payload_length}};
  next_source_offset_ = *following_offset;
  next_message_index_ = *following_index;
  return FrameReadResult{frame, std::nullopt};
}

} // namespace itchlab
