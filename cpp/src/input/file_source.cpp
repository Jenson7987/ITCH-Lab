#include "itchlab/input/file_source.hpp"

#include "itchlab/core/types.hpp"

#include <algorithm>
#include <fstream>
#include <limits>
#include <memory>
#include <system_error>
#include <utility>

namespace itchlab {
namespace {

class FileByteSource final : public ByteSource {
public:
  explicit FileByteSource(std::ifstream stream) : stream_{std::move(stream)} {}

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

    constexpr auto maximum_stream_size =
        static_cast<std::size_t>(std::numeric_limits<std::streamsize>::max());
    const auto request_size = std::min(destination.size(), maximum_stream_size);
    stream_.read(reinterpret_cast<char*>(destination.data()),
                 static_cast<std::streamsize>(request_size));
    const auto observed = stream_.gcount();
    if (observed > 0) {
      const auto bytes_read = static_cast<std::size_t>(observed);
      const auto next_progress = checked_add(progress_.uncompressed_bytes_delivered,
                                             static_cast<std::uint64_t>(bytes_read));
      if (!next_progress) {
        terminal_error_ = SourceError{ErrorCode::internal, "Uncompressed byte count overflowed."};
        return ReadResult::failure(*terminal_error_);
      }
      progress_.source_bytes_consumed = *next_progress;
      progress_.uncompressed_bytes_delivered = *next_progress;
      if (stream_.bad()) {
        terminal_error_ = SourceError{ErrorCode::input_path, "Input file read failed."};
      } else if (stream_.eof()) {
        end_of_file_ = true;
      } else if (stream_.fail()) {
        terminal_error_ = SourceError{ErrorCode::input_path, "Input file read failed."};
      }
      return ReadResult::data(bytes_read);
    }

    if (stream_.eof()) {
      end_of_file_ = true;
      return ReadResult::eof();
    }
    terminal_error_ = SourceError{ErrorCode::input_path, "Input file read failed."};
    return ReadResult::failure(*terminal_error_);
  }

  [[nodiscard]] SourceProgress progress() const noexcept override { return progress_; }

private:
  std::ifstream stream_;
  SourceProgress progress_{};
  bool end_of_file_{};
  std::optional<SourceError> terminal_error_;
};

SourceError open_error() {
  return SourceError{ErrorCode::input_path, "Input path is not a readable regular file."};
}

} // namespace

ByteSourceOpenResult open_file_source(const std::filesystem::path& path) {
  std::error_code filesystem_error;
  if (!std::filesystem::is_regular_file(path, filesystem_error) || filesystem_error) {
    return ByteSourceOpenResult{nullptr, open_error()};
  }

  std::ifstream stream{path, std::ios::binary};
  if (!stream.is_open()) {
    return ByteSourceOpenResult{nullptr, open_error()};
  }
  return ByteSourceOpenResult{std::make_unique<FileByteSource>(std::move(stream)), std::nullopt};
}

} // namespace itchlab
