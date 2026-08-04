#include "itchlab/input/gzip_source.hpp"

#include "itchlab/core/types.hpp"

#include <zlib.h>

#include <algorithm>
#include <array>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <limits>
#include <memory>
#include <optional>
#include <span>
#include <string>
#include <utility>

namespace itchlab {
namespace {

constexpr unsigned kGzipBufferSize = 64U * 1024U;

class GzipByteSource final : public ByteSource {
public:
  explicit GzipByteSource(gzFile stream) noexcept : stream_{stream} {}

  ~GzipByteSource() override {
    if (stream_ != nullptr) {
      static_cast<void>(gzclose(stream_));
    }
  }

  ReadResult read(const std::span<std::byte> destination) override {
    if (destination.empty()) {
      return ReadResult::data(0);
    }
    if (terminal_error_) {
      return ReadResult::failure(*terminal_error_);
    }
    if (end_of_file_) {
      return ReadResult::eof();
    }

    constexpr auto maximum_request = static_cast<std::size_t>(std::numeric_limits<unsigned>::max());
    const auto request_size = static_cast<unsigned>(std::min(destination.size(), maximum_request));
    const auto observed = gzread(stream_, destination.data(), request_size);
    update_source_progress();
    if (observed > 0) {
      const auto bytes_read = static_cast<std::size_t>(observed);
      const auto next_uncompressed = checked_add(progress_.uncompressed_bytes_delivered,
                                                 static_cast<std::uint64_t>(bytes_read));
      if (!next_uncompressed) {
        terminal_error_ = SourceError{ErrorCode::internal, "Uncompressed byte count overflowed."};
        return ReadResult::failure(*terminal_error_);
      }
      progress_.uncompressed_bytes_delivered = *next_uncompressed;
      return ReadResult::data(bytes_read);
    }

    int zlib_code = Z_OK;
    static_cast<void>(gzerror(stream_, &zlib_code));
    if (gzeof(stream_) != 0 && zlib_code == Z_OK) {
      end_of_file_ = true;
      return ReadResult::eof();
    }

    if (zlib_code == Z_ERRNO) {
      terminal_error_ = SourceError{ErrorCode::input_path, "Compressed input file read failed."};
    } else {
      terminal_error_ = SourceError{ErrorCode::framing, "Invalid or incomplete gzip stream."};
    }
    return ReadResult::failure(*terminal_error_);
  }

  [[nodiscard]] SourceProgress progress() const noexcept override { return progress_; }

private:
  void update_source_progress() noexcept {
    const auto compressed_offset = gzoffset(stream_);
    if (compressed_offset < 0) {
      return;
    }
    const auto converted = checked_integral_cast<std::uint64_t>(compressed_offset);
    if (converted && *converted >= progress_.source_bytes_consumed) {
      progress_.source_bytes_consumed = *converted;
    }
  }

  gzFile stream_{};
  SourceProgress progress_{};
  bool end_of_file_{};
  std::optional<SourceError> terminal_error_;
};

SourceError open_error() {
  return SourceError{ErrorCode::input_path, "Input path is not a readable gzip file."};
}

} // namespace

ByteSourceOpenResult open_gzip_source(const std::filesystem::path& path) {
  std::error_code filesystem_error;
  if (!std::filesystem::is_regular_file(path, filesystem_error) || filesystem_error) {
    return ByteSourceOpenResult{nullptr, open_error()};
  }

  std::ifstream probe{path, std::ios::binary};
  if (!probe.is_open()) {
    return ByteSourceOpenResult{nullptr, open_error()};
  }
  std::array<unsigned char, 2> magic{};
  probe.read(reinterpret_cast<char*>(magic.data()), static_cast<std::streamsize>(magic.size()));
  if (probe.gcount() != static_cast<std::streamsize>(magic.size()) || magic[0] != 0x1fU ||
      magic[1] != 0x8bU) {
    return ByteSourceOpenResult{
        nullptr, SourceError{ErrorCode::framing, "Input does not have a complete gzip header."}};
  }

  auto* stream = gzopen(path.string().c_str(), "rb");
  if (stream == nullptr) {
    return ByteSourceOpenResult{nullptr, open_error()};
  }
  if (gzbuffer(stream, kGzipBufferSize) != 0) {
    static_cast<void>(gzclose(stream));
    return ByteSourceOpenResult{
        nullptr, SourceError{ErrorCode::internal, "Could not configure the gzip input buffer."}};
  }
  return ByteSourceOpenResult{std::make_unique<GzipByteSource>(stream), std::nullopt};
}

} // namespace itchlab
