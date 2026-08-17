#include "itchlab/itch/decoder.hpp"

#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <span>

extern "C" int LLVMFuzzerTestOneInput(const std::uint8_t* data, const std::size_t size) {
  const auto payload = std::span<const std::byte>{reinterpret_cast<const std::byte*>(data), size};
  const itchlab::ItchDecoder decoder;
  const auto result = decoder.decode(payload);
  if (result.valid() == result.error.has_value() ||
      result.message.has_value() == result.error.has_value()) {
    std::abort();
  }
  return 0;
}
