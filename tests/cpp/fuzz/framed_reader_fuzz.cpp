#include "itchlab/input/framed_reader.hpp"

#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <span>

namespace {

class FuzzByteSource final : public itchlab::ByteSource {
public:
  FuzzByteSource(const std::span<const std::byte> input, const std::size_t maximum_chunk) noexcept
      : input_{input}, maximum_chunk_{maximum_chunk} {}

  itchlab::ReadResult read(const std::span<std::byte> destination) override {
    if (offset_ == input_.size()) {
      return itchlab::ReadResult::eof();
    }
    const auto size = std::min({destination.size(), maximum_chunk_, input_.size() - offset_});
    std::copy_n(input_.data() + offset_, size, destination.data());
    offset_ += size;
    return itchlab::ReadResult::data(size);
  }

  [[nodiscard]] itchlab::SourceProgress progress() const noexcept override {
    const auto delivered = static_cast<std::uint64_t>(offset_);
    return itchlab::SourceProgress{delivered, delivered};
  }

private:
  std::span<const std::byte> input_;
  std::size_t maximum_chunk_;
  std::size_t offset_{};
};

[[noreturn]] void invariant_failure() { std::abort(); }

} // namespace

extern "C" int LLVMFuzzerTestOneInput(const std::uint8_t* data, const std::size_t size) {
  const auto input = std::span<const std::byte>{reinterpret_cast<const std::byte*>(data), size};
  const auto maximum_chunk =
      size == 0 ? std::size_t{1} : static_cast<std::size_t>(data[0] % 31U) + 1U;
  FuzzByteSource source{input, maximum_chunk};
  itchlab::FramedMessageReader reader{source};

  itchlab::MessageIndex expected_index{};
  std::uint64_t expected_offset{};
  for (std::size_t iteration = 0; iteration < size + 2U; ++iteration) {
    const auto result = reader.next();
    if (result.frame) {
      const auto& frame = *result.frame;
      if (result.error || frame.message_index != expected_index ||
          frame.source_offset != expected_offset || frame.payload.empty() ||
          frame.payload.size() > itchlab::kMaximumFramePayload) {
        invariant_failure();
      }
      expected_index += 1U;
      expected_offset += static_cast<std::uint64_t>(frame.payload.size()) + 2U;
      continue;
    }
    if (result.error) {
      const auto repeated = reader.next();
      if (!repeated.error || repeated.frame || *repeated.error != *result.error) {
        invariant_failure();
      }
    } else if (!result.end_of_file()) {
      invariant_failure();
    }
    return 0;
  }

  invariant_failure();
}
