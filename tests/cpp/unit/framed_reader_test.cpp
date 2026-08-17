#include "itchlab/input/framed_reader.hpp"

#include <catch2/catch_test_macros.hpp>

#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdint>
#include <span>
#include <utility>
#include <vector>

namespace {

class MemoryByteSource final : public itchlab::ByteSource {
public:
  explicit MemoryByteSource(std::vector<std::byte> bytes,
                            const std::size_t maximum_chunk = SIZE_MAX)
      : bytes_{std::move(bytes)}, maximum_chunk_{maximum_chunk} {}

  itchlab::ReadResult read(const std::span<std::byte> destination) override {
    if (destination.empty()) {
      return itchlab::ReadResult::data(0);
    }
    ++read_calls_;
    largest_request_ = std::max(largest_request_, destination.size());
    if (offset_ == bytes_.size()) {
      return itchlab::ReadResult::eof();
    }
    const auto count = std::min({destination.size(), maximum_chunk_, bytes_.size() - offset_});
    std::copy_n(bytes_.begin() + static_cast<std::ptrdiff_t>(offset_),
                static_cast<std::ptrdiff_t>(count), destination.begin());
    offset_ += count;
    progress_ = itchlab::SourceProgress{offset_, offset_};
    return itchlab::ReadResult::data(count);
  }

  [[nodiscard]] itchlab::SourceProgress progress() const noexcept override { return progress_; }
  [[nodiscard]] std::size_t read_calls() const noexcept { return read_calls_; }
  [[nodiscard]] std::size_t largest_request() const noexcept { return largest_request_; }

private:
  std::vector<std::byte> bytes_;
  std::size_t maximum_chunk_;
  std::size_t offset_{};
  std::size_t read_calls_{};
  std::size_t largest_request_{};
  itchlab::SourceProgress progress_{};
};

class FailingByteSource final : public itchlab::ByteSource {
public:
  itchlab::ReadResult read(std::span<std::byte>) override {
    return itchlab::ReadResult::failure(
        itchlab::SourceError{itchlab::ErrorCode::input_path, "Injected read failure."});
  }
  [[nodiscard]] itchlab::SourceProgress progress() const noexcept override { return {}; }
};

class StalledByteSource final : public itchlab::ByteSource {
public:
  itchlab::ReadResult read(std::span<std::byte>) override { return itchlab::ReadResult::data(0); }
  [[nodiscard]] itchlab::SourceProgress progress() const noexcept override { return {}; }
};

std::vector<std::byte> frame(const std::span<const std::byte> payload) {
  const auto size = static_cast<std::uint16_t>(payload.size());
  std::vector<std::byte> result{static_cast<std::byte>(size >> 8U),
                                static_cast<std::byte>(size & 0xffU)};
  result.insert(result.end(), payload.begin(), payload.end());
  return result;
}

std::vector<std::byte> payload(const std::size_t size, const std::uint8_t seed) {
  std::vector<std::byte> result(size);
  for (std::size_t index = 0; index < size; ++index) {
    result[index] = static_cast<std::byte>(static_cast<std::uint8_t>(seed + index));
  }
  return result;
}

} // namespace

TEST_CASE("TASK-004 framed reader distinguishes clean EOF from truncated input",
          "[TASK-004][framing][boundary]") {
  SECTION("empty input is clean EOF") {
    MemoryByteSource source{{}};
    itchlab::FramedMessageReader reader{source};
    REQUIRE(reader.next().end_of_file());
    REQUIRE(reader.next().end_of_file());
  }

  SECTION("one prefix byte is truncated") {
    MemoryByteSource source{{std::byte{0x00}}};
    itchlab::FramedMessageReader reader{source};
    const auto result = reader.next();
    REQUIRE(result.error->code == itchlab::ErrorCode::truncated_message);
    REQUIRE(result.error->source_offset == 0);
    REQUIRE(result.error->message_index == 0);
  }

  SECTION("declared payload is truncated") {
    MemoryByteSource source{{std::byte{0x00}, std::byte{0x03}, std::byte{'S'}, std::byte{0x01}}};
    itchlab::FramedMessageReader reader{source};
    const auto result = reader.next();
    REQUIRE(result.error->code == itchlab::ErrorCode::truncated_message);
    REQUIRE(result.error->source_offset == 0);
  }

  SECTION("truncation after a complete frame reports the next exact position") {
    const auto complete = frame(payload(3, 7));
    auto stream = complete;
    stream.push_back(std::byte{0x00});
    MemoryByteSource source{std::move(stream)};
    itchlab::FramedMessageReader reader{source};
    REQUIRE(reader.next().frame.has_value());
    const auto result = reader.next();
    REQUIRE(result.error->code == itchlab::ErrorCode::truncated_message);
    REQUIRE(result.error->message_index == 1);
    REQUIRE(result.error->source_offset == complete.size());
  }
}

TEST_CASE("TASK-004 framed reader rejects zero and oversized lengths before payload reads",
          "[TASK-004][framing][security]") {
  for (const auto& test_case : {
           std::pair{std::array{std::byte{0x00}, std::byte{0x00}}, itchlab::ErrorCode::framing},
           std::pair{std::array{std::byte{0x02}, std::byte{0x01}}, itchlab::ErrorCode::framing},
       }) {
    MemoryByteSource source{{test_case.first.begin(), test_case.first.end()}};
    itchlab::FramedMessageReader reader{source};
    const auto result = reader.next();
    REQUIRE(result.error->code == test_case.second);
    REQUIRE(source.progress().uncompressed_bytes_delivered == 2);
    REQUIRE(source.largest_request() == 2);
  }
}

TEST_CASE("TASK-004 framed reader accepts the exact 512-byte project limit",
          "[TASK-004][framing][boundary]") {
  const auto expected = payload(itchlab::kMaximumFramePayload, 17);
  MemoryByteSource source{frame(expected), 3};
  itchlab::FramedMessageReader reader{source};
  const auto result = reader.next();
  REQUIRE(result.frame.has_value());
  REQUIRE(result.frame->payload.size() == itchlab::kMaximumFramePayload);
  REQUIRE(std::ranges::equal(result.frame->payload, expected));
  REQUIRE(reader.next().end_of_file());
}

TEST_CASE("TASK-004 framed reader handles every short-read split with exact positions",
          "[TASK-004][framing][property]") {
  std::vector<std::byte> stream;
  std::vector<std::vector<std::byte>> expected_payloads;
  for (const auto size : {std::size_t{1}, std::size_t{2}, std::size_t{11}, std::size_t{512}}) {
    expected_payloads.push_back(payload(size, static_cast<std::uint8_t>(size)));
    const auto encoded = frame(expected_payloads.back());
    stream.insert(stream.end(), encoded.begin(), encoded.end());
  }

  for (const auto maximum_chunk :
       {std::size_t{1}, std::size_t{2}, std::size_t{3}, std::size_t{7}, std::size_t{513}}) {
    MemoryByteSource source{stream, maximum_chunk};
    itchlab::FramedMessageReader reader{source};
    std::uint64_t expected_offset{};
    for (std::size_t index = 0; index < expected_payloads.size(); ++index) {
      const auto result = reader.next();
      REQUIRE(result.frame.has_value());
      REQUIRE(result.frame->message_index == index);
      REQUIRE(result.frame->source_offset == expected_offset);
      REQUIRE(std::ranges::equal(result.frame->payload, expected_payloads[index]));
      expected_offset += expected_payloads[index].size() + 2;
    }
    REQUIRE(reader.next().end_of_file());
  }
}

TEST_CASE("TASK-004 every legal frame length round-trips through bounded storage",
          "[TASK-004][framing][property][boundary]") {
  for (std::size_t size = 1; size <= itchlab::kMaximumFramePayload; ++size) {
    const auto expected = payload(size, static_cast<std::uint8_t>(size));
    MemoryByteSource source{frame(expected), (size % 17) + 1};
    itchlab::FramedMessageReader reader{source};
    const auto result = reader.next();
    REQUIRE(result.frame.has_value());
    REQUIRE(result.frame->payload.size() == size);
    REQUIRE(std::ranges::equal(result.frame->payload, expected));
    REQUIRE(reader.next().end_of_file());
  }
}

TEST_CASE("TASK-004 caller can stop between frames without reading ahead",
          "[TASK-004][framing][cancellation]") {
  const auto first = frame(payload(3, 1));
  const auto second = frame(payload(4, 2));
  auto stream = first;
  stream.insert(stream.end(), second.begin(), second.end());
  MemoryByteSource source{std::move(stream)};
  itchlab::FramedMessageReader reader{source};

  REQUIRE(reader.next().frame.has_value());
  REQUIRE(source.progress().uncompressed_bytes_delivered == first.size());
  REQUIRE(source.read_calls() == 2);
}

TEST_CASE("TASK-004 source failures retain frame context and become terminal",
          "[TASK-004][framing][error]") {
  FailingByteSource source;
  itchlab::FramedMessageReader reader{source};
  const auto first = reader.next();
  REQUIRE(first.error->code == itchlab::ErrorCode::input_path);
  REQUIRE(first.error->message_index == 0);
  REQUIRE(first.error->source_offset == 0);
  REQUIRE(reader.next().error == first.error);

  StalledByteSource stalled;
  itchlab::FramedMessageReader stalled_reader{stalled};
  REQUIRE(stalled_reader.next().error->code == itchlab::ErrorCode::internal);
}
