#pragma once

#include "itchlab/input/byte_source.hpp"

#include <cstdint>
#include <filesystem>
#include <memory>
#include <optional>
#include <string_view>

namespace itchlab {

enum class InputCompression : std::uint8_t {
  none,
  gzip,
};

[[nodiscard]] std::string_view input_compression_name(InputCompression compression) noexcept;

struct InputOpenResult {
  std::unique_ptr<ByteSource> source;
  std::optional<SourceError> error;
  InputCompression compression{InputCompression::none};
  std::uint64_t source_size_bytes{};

  [[nodiscard]] bool valid() const noexcept { return source != nullptr && !error.has_value(); }
};

// Detects supported compression from file bytes and opens a bounded streaming source.
[[nodiscard]] InputOpenResult open_input_source(const std::filesystem::path& path);

} // namespace itchlab
