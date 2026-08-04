#include "itchlab/input/framed_reader.hpp"

#include <catch2/catch_test_macros.hpp>

#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <span>
#include <vector>

namespace {

class RepeatingFrameSource final : public itchlab::ByteSource {
public:
  explicit RepeatingFrameSource(const std::uint64_t frame_count) : frames_remaining_{frame_count} {}

  itchlab::ReadResult read(const std::span<std::byte> destination) override {
    if (destination.empty()) {
      return itchlab::ReadResult::data(0);
    }
    if (frames_remaining_ == 0 && frame_position_ == 0) {
      return itchlab::ReadResult::eof();
    }
    const auto count = std::min(destination.size(), frame_.size() - frame_position_);
    std::copy_n(frame_.begin() + static_cast<std::ptrdiff_t>(frame_position_),
                static_cast<std::ptrdiff_t>(count), destination.begin());
    frame_position_ += count;
    delivered_ += count;
    if (frame_position_ == frame_.size()) {
      frame_position_ = 0;
      --frames_remaining_;
    }
    return itchlab::ReadResult::data(count);
  }

  [[nodiscard]] itchlab::SourceProgress progress() const noexcept override {
    return itchlab::SourceProgress{delivered_, delivered_};
  }

private:
  const std::vector<std::byte> frame_{std::byte{0x00}, std::byte{0x01}, std::byte{'S'}};
  std::uint64_t frames_remaining_;
  std::size_t frame_position_{};
  std::uint64_t delivered_{};
};

} // namespace

TEST_CASE("TASK-004 large generated stream retains exact monotonic framing",
          "[TASK-004][framing][property][streaming]") {
  constexpr std::uint64_t frame_count = 100'000;
  RepeatingFrameSource source{frame_count};
  itchlab::FramedMessageReader reader{source};
  for (std::uint64_t index = 0; index < frame_count; ++index) {
    const auto result = reader.next();
    REQUIRE(result.frame.has_value());
    REQUIRE(result.frame->message_index == index);
    REQUIRE(result.frame->source_offset == index * 3);
    REQUIRE(result.frame->payload.size() == 1);
    REQUIRE(std::to_integer<char>(result.frame->payload.front()) == 'S');
  }
  REQUIRE(reader.next().end_of_file());
  REQUIRE(source.progress().uncompressed_bytes_delivered == frame_count * 3);
}
