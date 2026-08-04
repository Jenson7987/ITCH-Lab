#pragma once

#include "itchlab/input/byte_source.hpp"

#include <filesystem>

namespace itchlab {

// Opens a gzip file as a bounded sequential source. Member checksums are validated at EOF.
[[nodiscard]] ByteSourceOpenResult open_gzip_source(const std::filesystem::path& path);

} // namespace itchlab
