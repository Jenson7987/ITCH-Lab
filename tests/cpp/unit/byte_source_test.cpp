#include "itchlab/input/file_source.hpp"
#include "itchlab/input/gzip_source.hpp"

#include <catch2/catch_test_macros.hpp>

#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <span>
#include <string>
#include <vector>

namespace {

std::filesystem::path repository_path(const std::string& relative_path) {
  return std::filesystem::path{ITCHLAB_SOURCE_DIR} / relative_path;
}

std::vector<std::byte> read_file(const std::filesystem::path& path) {
  std::ifstream stream{path, std::ios::binary};
  REQUIRE(stream.good());
  const std::vector<char> characters{std::istreambuf_iterator<char>{stream},
                                     std::istreambuf_iterator<char>{}};
  std::vector<std::byte> bytes;
  bytes.reserve(characters.size());
  for (const auto character : characters) {
    bytes.push_back(static_cast<std::byte>(static_cast<unsigned char>(character)));
  }
  return bytes;
}

std::vector<std::byte> drain(itchlab::ByteSource& source, const std::size_t chunk_size) {
  std::vector<std::byte> result;
  std::vector<std::byte> buffer(chunk_size);
  while (true) {
    const auto read = source.read(buffer);
    REQUIRE_FALSE(read.error.has_value());
    if (read.end_of_file) {
      break;
    }
    REQUIRE(read.bytes_read > 0);
    REQUIRE(read.bytes_read <= buffer.size());
    result.insert(result.end(), buffer.begin(),
                  buffer.begin() + static_cast<std::ptrdiff_t>(read.bytes_read));
  }
  return result;
}

} // namespace

TEST_CASE("TASK-004 plain byte source streams bounded reads and exact progress",
          "[TASK-004][input][boundary]") {
  const auto path = repository_path("tests/fixtures/synthetic_minimal.itch");
  auto opened = itchlab::open_file_source(path);
  REQUIRE(opened.valid());
  REQUIRE_FALSE(opened.error.has_value());

  std::array<std::byte, 0> empty{};
  const auto empty_read = opened.source->read(empty);
  REQUIRE(empty_read.bytes_read == 0);
  REQUIRE_FALSE(empty_read.end_of_file);
  REQUIRE_FALSE(empty_read.error.has_value());
  REQUIRE(opened.source->progress() == itchlab::SourceProgress{});

  const auto observed = drain(*opened.source, 7);
  const auto expected = read_file(path);
  REQUIRE(std::ranges::equal(observed, expected));
  REQUIRE(opened.source->progress() == itchlab::SourceProgress{expected.size(), expected.size()});
  REQUIRE(opened.source->read(std::span<std::byte>{}).bytes_read == 0);
}

TEST_CASE("TASK-004 gzip byte source is semantically equal to the plain fixture",
          "[TASK-004][IT-002][input][gzip]") {
  const auto gzip_path = repository_path("tests/fixtures/synthetic_minimal.itch.gz");
  const auto plain_path = repository_path("tests/fixtures/synthetic_minimal.itch");
  auto opened = itchlab::open_gzip_source(gzip_path);
  REQUIRE(opened.valid());

  const auto observed = drain(*opened.source, 5);
  const auto expected = read_file(plain_path);
  REQUIRE(std::ranges::equal(observed, expected));
  REQUIRE(opened.source->progress().uncompressed_bytes_delivered == expected.size());
  REQUIRE(opened.source->progress().source_bytes_consumed == std::filesystem::file_size(gzip_path));
}

TEST_CASE("TASK-004 byte source opening reports stable typed errors", "[TASK-004][input][error]") {
  const auto missing = repository_path("tests/fixtures/does-not-exist.itch");
  auto plain = itchlab::open_file_source(missing);
  REQUIRE_FALSE(plain.valid());
  REQUIRE(plain.error->code == itchlab::ErrorCode::input_path);

  auto gzip = itchlab::open_gzip_source(missing);
  REQUIRE_FALSE(gzip.valid());
  REQUIRE(gzip.error->code == itchlab::ErrorCode::input_path);

  auto not_gzip =
      itchlab::open_gzip_source(repository_path("tests/fixtures/synthetic_minimal.itch"));
  REQUIRE_FALSE(not_gzip.valid());
  REQUIRE(not_gzip.error->code == itchlab::ErrorCode::framing);
}

TEST_CASE("TASK-004 damaged gzip members fail after bounded output without clean EOF",
          "[TASK-004][input][gzip][security]") {
  for (const auto* relative_path : {
           "tests/fixtures/corrupt/synthetic_corrupt_truncated_gzip.itch.gz",
           "tests/fixtures/corrupt/synthetic_corrupt_gzip_checksum.itch.gz",
       }) {
    auto opened = itchlab::open_gzip_source(repository_path(relative_path));
    REQUIRE(opened.valid());
    std::array<std::byte, 11> buffer{};
    bool saw_error = false;
    for (std::size_t calls = 0; calls < 1'000; ++calls) {
      const auto read = opened.source->read(buffer);
      if (read.error) {
        REQUIRE(read.error->code == itchlab::ErrorCode::framing);
        saw_error = true;
        break;
      }
      REQUIRE_FALSE(read.end_of_file);
      REQUIRE(read.bytes_read > 0);
    }
    REQUIRE(saw_error);
  }
}
