#include "itchlab/core/sha256.hpp"

#include <catch2/catch_test_macros.hpp>

#include <cstddef>
#include <span>
#include <string>

TEST_CASE("TASK-002 SHA-256 matches FIPS vectors", "[TASK-002][hash]") {
  REQUIRE(itchlab::content_hash_to_hex(itchlab::sha256("")) ==
          "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855");
  REQUIRE(itchlab::content_hash_to_hex(itchlab::sha256("abc")) ==
          "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
  REQUIRE(itchlab::content_hash_to_hex(
              itchlab::sha256("abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq")) ==
          "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1");
}

TEST_CASE("TASK-002 content hashes require lowercase fixed hexadecimal", "[TASK-002][hash]") {
  constexpr auto valid = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad";
  const auto parsed = itchlab::content_hash_from_hex(valid);
  REQUIRE(parsed.has_value());
  REQUIRE(itchlab::content_hash_to_hex(*parsed) == valid);
  REQUIRE_FALSE(itchlab::content_hash_from_hex("abc").has_value());
  REQUIRE_FALSE(itchlab::content_hash_from_hex(
                    "BA7816BF8F01CFEA414140DE5DAE2223B00361A396177A9CB410FF61F20015AD")
                    .has_value());
}

TEST_CASE("TASK-014 incremental SHA-256 is invariant to every chunk boundary",
          "[TASK-014][hash][incremental]") {
  const std::string payload(257, 'x');
  const auto bytes = std::as_bytes(std::span{payload.data(), payload.size()});
  const auto expected = itchlab::sha256(bytes);
  for (std::size_t split = 0; split <= bytes.size(); ++split) {
    itchlab::Sha256Hasher hasher;
    REQUIRE(hasher.update(bytes.first(split)));
    REQUIRE(hasher.update(bytes.subspan(split)));
    const auto actual = hasher.finalise();
    REQUIRE(actual.has_value());
    REQUIRE(itchlab::content_hash_to_hex(*actual) == itchlab::content_hash_to_hex(expected));
    REQUIRE_FALSE(hasher.finalise().has_value());
    REQUIRE_FALSE(hasher.update(bytes.first(1)));
  }
}
