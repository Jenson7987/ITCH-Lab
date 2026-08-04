#include "itchlab/input/source_factory.hpp"

#include "itchlab/core/types.hpp"
#include "itchlab/input/file_source.hpp"
#include "itchlab/input/gzip_source.hpp"

#include <array>
#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <span>
#include <string_view>
#include <utility>

namespace itchlab {
namespace {

constexpr std::array<unsigned char, 2> kGzipMagic{0x1fU, 0x8bU};
constexpr std::array<unsigned char, 3> kBzip2Magic{'B', 'Z', 'h'};
constexpr std::array<unsigned char, 4> kZipMagic{'P', 'K', 0x03U, 0x04U};
constexpr std::array<unsigned char, 4> kZstdMagic{0x28U, 0xb5U, 0x2fU, 0xfdU};
constexpr std::array<unsigned char, 6> kXzMagic{0xfdU, '7', 'z', 'X', 'Z', 0x00U};

template <std::size_t Width>
[[nodiscard]] bool starts_with(const std::span<const unsigned char> observed,
                               const std::array<unsigned char, Width>& magic) noexcept {
  if (observed.size() < magic.size()) {
    return false;
  }
  for (std::size_t index = 0; index < magic.size(); ++index) {
    if (observed[index] != magic[index]) {
      return false;
    }
  }
  return true;
}

[[nodiscard]] SourceError path_error() {
  return SourceError{ErrorCode::input_path, "Input path is not a readable regular file."};
}

} // namespace

std::string_view input_compression_name(const InputCompression compression) noexcept {
  switch (compression) {
  case InputCompression::none:
    return "none";
  case InputCompression::gzip:
    return "gzip";
  }
  return "none";
}

InputOpenResult open_input_source(const std::filesystem::path& path) {
  std::error_code filesystem_error;
  if (!std::filesystem::is_regular_file(path, filesystem_error) || filesystem_error) {
    return InputOpenResult{nullptr, path_error()};
  }

  const auto source_size = std::filesystem::file_size(path, filesystem_error);
  if (filesystem_error) {
    return InputOpenResult{nullptr, path_error()};
  }
  const auto checked_size = checked_integral_cast<std::uint64_t>(source_size);
  if (!checked_size) {
    return InputOpenResult{nullptr, SourceError{ErrorCode::input_path,
                                                "Input file size exceeds the supported range."}};
  }

  std::ifstream probe{path, std::ios::binary};
  if (!probe.is_open()) {
    return InputOpenResult{nullptr, path_error()};
  }
  std::array<unsigned char, 6> prefix{};
  probe.read(reinterpret_cast<char*>(prefix.data()), static_cast<std::streamsize>(prefix.size()));
  const auto observed_size = probe.gcount();
  if (observed_size < 0) {
    return InputOpenResult{nullptr, path_error()};
  }
  const auto observed =
      std::span<const unsigned char>{prefix.data(), static_cast<std::size_t>(observed_size)};

  if (starts_with(observed, kGzipMagic)) {
    auto opened = open_gzip_source(path);
    return InputOpenResult{std::move(opened.source), std::move(opened.error),
                           InputCompression::gzip, *checked_size};
  }
  if (starts_with(observed, kBzip2Magic) || starts_with(observed, kZipMagic) ||
      starts_with(observed, kZstdMagic) || starts_with(observed, kXzMagic)) {
    return InputOpenResult{
        nullptr,
        SourceError{ErrorCode::unsupported_compression,
                    "Input compression is unsupported; use gzip or uncompressed framing."},
        InputCompression::none, *checked_size};
  }

  auto opened = open_file_source(path);
  return InputOpenResult{std::move(opened.source), std::move(opened.error), InputCompression::none,
                         *checked_size};
}

} // namespace itchlab
