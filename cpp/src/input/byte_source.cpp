#include "itchlab/input/byte_source.hpp"

#include <utility>

namespace itchlab {

ReadResult ReadResult::data(const std::size_t size) noexcept {
  return ReadResult{size, false, std::nullopt};
}

ReadResult ReadResult::eof() noexcept { return ReadResult{0, true, std::nullopt}; }

ReadResult ReadResult::failure(SourceError source_error) {
  return ReadResult{0, false, std::move(source_error)};
}

} // namespace itchlab
