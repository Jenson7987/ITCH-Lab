#pragma once

#include "itchlab/core/errors.hpp"

#include <cstddef>
#include <cstdint>
#include <memory>
#include <optional>
#include <span>
#include <string>

namespace itchlab {

struct SourceProgress {
  std::uint64_t source_bytes_consumed{};
  std::uint64_t uncompressed_bytes_delivered{};

  friend bool operator==(const SourceProgress&, const SourceProgress&) = default;
};

struct SourceError {
  ErrorCode code{ErrorCode::input_path};
  std::string message;

  friend bool operator==(const SourceError&, const SourceError&) = default;
};

struct ReadResult {
  std::size_t bytes_read{};
  bool end_of_file{};
  std::optional<SourceError> error;

  [[nodiscard]] static ReadResult data(std::size_t size) noexcept;
  [[nodiscard]] static ReadResult eof() noexcept;
  [[nodiscard]] static ReadResult failure(SourceError source_error);
};

class ByteSource {
public:
  // A non-empty destination produces data, EOF or an error; it never reports no progress.
  virtual ReadResult read(std::span<std::byte> destination) = 0;
  [[nodiscard]] virtual SourceProgress progress() const noexcept = 0;
  virtual ~ByteSource() = default;
};

struct ByteSourceOpenResult {
  std::unique_ptr<ByteSource> source;
  std::optional<SourceError> error;

  [[nodiscard]] bool valid() const noexcept { return source != nullptr; }
};

} // namespace itchlab
