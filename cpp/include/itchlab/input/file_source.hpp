#pragma once

#include "itchlab/input/byte_source.hpp"

#include <filesystem>

namespace itchlab {

// Opens a readable regular file as an uncompressed sequential byte source.
[[nodiscard]] ByteSourceOpenResult open_file_source(const std::filesystem::path& path);

} // namespace itchlab
