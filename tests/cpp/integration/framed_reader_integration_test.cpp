#include "itchlab/core/sha256.hpp"
#include "itchlab/input/file_source.hpp"
#include "itchlab/input/framed_reader.hpp"
#include "itchlab/input/gzip_source.hpp"

#include <catch2/catch_test_macros.hpp>

#include <array>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <filesystem>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

namespace {

struct ObservedFrame {
  itchlab::MessageIndex index{};
  std::uint64_t offset{};
  std::vector<std::byte> payload;

  friend bool operator==(const ObservedFrame&, const ObservedFrame&) = default;
};

std::filesystem::path repository_path(const std::string_view relative_path) {
  return std::filesystem::path{ITCHLAB_SOURCE_DIR} / relative_path;
}

std::vector<ObservedFrame> drain_frames(itchlab::ByteSource& source) {
  itchlab::FramedMessageReader reader{source};
  std::vector<ObservedFrame> frames;
  while (true) {
    const auto result = reader.next();
    REQUIRE_FALSE(result.error.has_value());
    if (result.end_of_file()) {
      break;
    }
    frames.push_back(ObservedFrame{result.frame->message_index,
                                   result.frame->source_offset,
                                   {result.frame->payload.begin(), result.frame->payload.end()}});
  }
  return frames;
}

std::string payload_digest(const std::vector<ObservedFrame>& frames) {
  std::vector<std::byte> payloads;
  for (const auto& frame : frames) {
    payloads.insert(payloads.end(), frame.payload.begin(), frame.payload.end());
  }
  return itchlab::content_hash_to_hex(itchlab::sha256(payloads));
}

} // namespace

TEST_CASE("IT-001 uncompressed minimal fixture has exact frames offsets and types",
          "[TASK-004][IT-001][integration]") {
  auto opened = itchlab::open_file_source(repository_path("tests/fixtures/synthetic_minimal.itch"));
  REQUIRE(opened.valid());
  const auto frames = drain_frames(*opened.source);

  constexpr std::array<std::uint64_t, 9> expected_offsets{0, 14, 55, 69, 83, 121, 142, 156, 170};
  constexpr std::array<std::size_t, 9> expected_lengths{12, 39, 12, 12, 36, 19, 12, 12, 12};
  constexpr std::string_view expected_types{"SRSSADSSS"};
  REQUIRE(frames.size() == expected_offsets.size());
  for (std::size_t index = 0; index < frames.size(); ++index) {
    REQUIRE(frames[index].index == index);
    REQUIRE(frames[index].offset == expected_offsets[index]);
    REQUIRE(frames[index].payload.size() == expected_lengths[index]);
    REQUIRE(std::to_integer<char>(frames[index].payload.front()) == expected_types[index]);
  }
  REQUIRE(opened.source->progress().uncompressed_bytes_delivered == 184);
}

TEST_CASE("IT-002 gzip and plain mixed fixtures have identical framed payload digest",
          "[TASK-004][IT-002][integration][gzip]") {
  auto plain = itchlab::open_file_source(repository_path("tests/fixtures/synthetic_mixed.itch"));
  auto gzip = itchlab::open_gzip_source(repository_path("tests/fixtures/synthetic_mixed.itch.gz"));
  REQUIRE(plain.valid());
  REQUIRE(gzip.valid());

  const auto plain_frames = drain_frames(*plain.source);
  const auto gzip_frames = drain_frames(*gzip.source);
  REQUIRE(gzip_frames == plain_frames);
  REQUIRE(gzip_frames.size() == 31);
  REQUIRE(payload_digest(gzip_frames) == payload_digest(plain_frames));
}

TEST_CASE("TASK-004 corruption fixtures produce the documented framing errors",
          "[TASK-004][integration][security]") {
  struct Case {
    std::string_view path;
    itchlab::ErrorCode expected;
  };
  constexpr std::array cases{
      Case{"tests/fixtures/corrupt/synthetic_corrupt_truncated_length_prefix.itch",
           itchlab::ErrorCode::truncated_message},
      Case{"tests/fixtures/corrupt/synthetic_corrupt_zero_length_frame.itch",
           itchlab::ErrorCode::framing},
      Case{"tests/fixtures/corrupt/synthetic_corrupt_oversized_frame.itch",
           itchlab::ErrorCode::framing},
      Case{"tests/fixtures/corrupt/synthetic_corrupt_truncated_payload.itch",
           itchlab::ErrorCode::truncated_message},
  };

  for (const auto& test_case : cases) {
    auto opened = itchlab::open_file_source(repository_path(test_case.path));
    REQUIRE(opened.valid());
    itchlab::FramedMessageReader reader{*opened.source};
    const auto result = reader.next();
    REQUIRE(result.error->code == test_case.expected);
    REQUIRE(result.error->message_index == 0);
    REQUIRE(result.error->source_offset == 0);
  }
}

TEST_CASE("TASK-004 damaged gzip cannot become clean framed EOF",
          "[TASK-004][integration][gzip][security]") {
  for (const auto path : {
           "tests/fixtures/corrupt/synthetic_corrupt_truncated_gzip.itch.gz",
           "tests/fixtures/corrupt/synthetic_corrupt_gzip_checksum.itch.gz",
       }) {
    auto opened = itchlab::open_gzip_source(repository_path(path));
    REQUIRE(opened.valid());
    itchlab::FramedMessageReader reader{*opened.source};
    bool saw_error = false;
    for (std::size_t calls = 0; calls < 100; ++calls) {
      const auto result = reader.next();
      if (result.error) {
        REQUIRE(result.error->code == itchlab::ErrorCode::framing);
        saw_error = true;
        break;
      }
      REQUIRE_FALSE(result.end_of_file());
    }
    REQUIRE(saw_error);
  }
}

TEST_CASE("TASK-004 authorised official sample begins with verified framing",
          "[TASK-004][official-data]") {
  const auto* configured_path = std::getenv("ITCHLAB_OFFICIAL_SAMPLE");
  if (configured_path == nullptr || configured_path[0] == '\0') {
    SKIP("ITCHLAB_OFFICIAL_SAMPLE is not configured; public CI uses synthetic fixtures.");
  }

  auto opened = itchlab::open_gzip_source(configured_path);
  REQUIRE(opened.valid());
  itchlab::FramedMessageReader reader{*opened.source};
  for (std::size_t index = 0; index < 20; ++index) {
    const auto result = reader.next();
    REQUIRE(result.frame.has_value());
    REQUIRE(result.frame->message_index == index);
    REQUIRE(result.frame->source_offset == (index == 0 ? 0 : 14 + ((index - 1) * 41)));
    REQUIRE(result.frame->payload.size() == (index == 0 ? 12 : 39));
    REQUIRE(std::to_integer<char>(result.frame->payload.front()) == (index == 0 ? 'S' : 'R'));
  }
}
