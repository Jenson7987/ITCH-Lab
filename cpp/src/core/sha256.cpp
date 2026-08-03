#include "itchlab/core/sha256.hpp"

#include <array>
#include <bit>
#include <cstddef>
#include <cstdint>

namespace itchlab {
namespace {

constexpr std::array<std::uint32_t, 64> kRoundConstants{
    0x428a2f98U, 0x71374491U, 0xb5c0fbcfU, 0xe9b5dba5U, 0x3956c25bU, 0x59f111f1U, 0x923f82a4U,
    0xab1c5ed5U, 0xd807aa98U, 0x12835b01U, 0x243185beU, 0x550c7dc3U, 0x72be5d74U, 0x80deb1feU,
    0x9bdc06a7U, 0xc19bf174U, 0xe49b69c1U, 0xefbe4786U, 0x0fc19dc6U, 0x240ca1ccU, 0x2de92c6fU,
    0x4a7484aaU, 0x5cb0a9dcU, 0x76f988daU, 0x983e5152U, 0xa831c66dU, 0xb00327c8U, 0xbf597fc7U,
    0xc6e00bf3U, 0xd5a79147U, 0x06ca6351U, 0x14292967U, 0x27b70a85U, 0x2e1b2138U, 0x4d2c6dfcU,
    0x53380d13U, 0x650a7354U, 0x766a0abbU, 0x81c2c92eU, 0x92722c85U, 0xa2bfe8a1U, 0xa81a664bU,
    0xc24b8b70U, 0xc76c51a3U, 0xd192e819U, 0xd6990624U, 0xf40e3585U, 0x106aa070U, 0x19a4c116U,
    0x1e376c08U, 0x2748774cU, 0x34b0bcb5U, 0x391c0cb3U, 0x4ed8aa4aU, 0x5b9cca4fU, 0x682e6ff3U,
    0x748f82eeU, 0x78a5636fU, 0x84c87814U, 0x8cc70208U, 0x90befffaU, 0xa4506cebU, 0xbef9a3f7U,
    0xc67178f2U,
};

using State = std::array<std::uint32_t, 8>;

constexpr std::uint32_t choose(const std::uint32_t x, const std::uint32_t y,
                               const std::uint32_t z) noexcept {
  return (x & y) ^ (~x & z);
}

constexpr std::uint32_t majority(const std::uint32_t x, const std::uint32_t y,
                                 const std::uint32_t z) noexcept {
  return (x & y) ^ (x & z) ^ (y & z);
}

constexpr std::uint32_t big_sigma0(const std::uint32_t value) noexcept {
  return std::rotr(value, 2) ^ std::rotr(value, 13) ^ std::rotr(value, 22);
}

constexpr std::uint32_t big_sigma1(const std::uint32_t value) noexcept {
  return std::rotr(value, 6) ^ std::rotr(value, 11) ^ std::rotr(value, 25);
}

constexpr std::uint32_t small_sigma0(const std::uint32_t value) noexcept {
  return std::rotr(value, 7) ^ std::rotr(value, 18) ^ (value >> 3U);
}

constexpr std::uint32_t small_sigma1(const std::uint32_t value) noexcept {
  return std::rotr(value, 17) ^ std::rotr(value, 19) ^ (value >> 10U);
}

void process_block(State& state, const std::span<const std::byte, 64> block) noexcept {
  std::array<std::uint32_t, 64> words{};
  for (std::size_t index = 0; index < 16; ++index) {
    const auto offset = index * 4;
    words[index] =
        (static_cast<std::uint32_t>(std::to_integer<std::uint8_t>(block[offset])) << 24U) |
        (static_cast<std::uint32_t>(std::to_integer<std::uint8_t>(block[offset + 1])) << 16U) |
        (static_cast<std::uint32_t>(std::to_integer<std::uint8_t>(block[offset + 2])) << 8U) |
        static_cast<std::uint32_t>(std::to_integer<std::uint8_t>(block[offset + 3]));
  }
  for (std::size_t index = 16; index < words.size(); ++index) {
    words[index] = small_sigma1(words[index - 2]) + words[index - 7] +
                   small_sigma0(words[index - 15]) + words[index - 16];
  }

  auto a = state[0];
  auto b = state[1];
  auto c = state[2];
  auto d = state[3];
  auto e = state[4];
  auto f = state[5];
  auto g = state[6];
  auto h = state[7];

  for (std::size_t index = 0; index < words.size(); ++index) {
    const auto temp1 = h + big_sigma1(e) + choose(e, f, g) + kRoundConstants[index] + words[index];
    const auto temp2 = big_sigma0(a) + majority(a, b, c);
    h = g;
    g = f;
    f = e;
    e = d + temp1;
    d = c;
    c = b;
    b = a;
    a = temp1 + temp2;
  }

  state[0] += a;
  state[1] += b;
  state[2] += c;
  state[3] += d;
  state[4] += e;
  state[5] += f;
  state[6] += g;
  state[7] += h;
}

constexpr std::optional<std::uint8_t> hex_nibble(const char character) noexcept {
  if (character >= '0' && character <= '9') {
    return static_cast<std::uint8_t>(character - '0');
  }
  if (character >= 'a' && character <= 'f') {
    return static_cast<std::uint8_t>(character - 'a' + 10);
  }
  return std::nullopt;
}

} // namespace

ContentHash sha256(const std::span<const std::byte> input) noexcept {
  State state{0x6a09e667U, 0xbb67ae85U, 0x3c6ef372U, 0xa54ff53aU,
              0x510e527fU, 0x9b05688cU, 0x1f83d9abU, 0x5be0cd19U};

  std::size_t offset = 0;
  while (input.size() - offset >= 64) {
    process_block(state, std::span<const std::byte, 64>{input.data() + offset, 64});
    offset += 64;
  }

  std::array<std::byte, 128> tail{};
  const auto remaining = input.size() - offset;
  for (std::size_t index = 0; index < remaining; ++index) {
    tail[index] = input[offset + index];
  }
  tail[remaining] = std::byte{0x80};

  const auto padded_size = remaining < 56 ? std::size_t{64} : std::size_t{128};
  const auto bit_length = static_cast<std::uint64_t>(input.size()) * 8U;
  for (std::size_t index = 0; index < 8; ++index) {
    const auto shift = static_cast<unsigned>((7 - index) * 8);
    tail[padded_size - 8 + index] =
        static_cast<std::byte>(static_cast<std::uint8_t>(bit_length >> shift));
  }

  process_block(state, std::span<const std::byte, 64>{tail.data(), 64});
  if (padded_size == 128) {
    process_block(state, std::span<const std::byte, 64>{tail.data() + 64, 64});
  }

  ContentHash digest{};
  for (std::size_t word = 0; word < state.size(); ++word) {
    for (std::size_t byte = 0; byte < 4; ++byte) {
      const auto shift = static_cast<unsigned>((3 - byte) * 8);
      digest[word * 4 + byte] =
          static_cast<std::byte>(static_cast<std::uint8_t>(state[word] >> shift));
    }
  }
  return digest;
}

ContentHash sha256(const std::string_view input) noexcept {
  return sha256(std::as_bytes(std::span{input.data(), input.size()}));
}

std::string content_hash_to_hex(const ContentHash& hash) {
  constexpr std::string_view digits{"0123456789abcdef"};
  std::string result;
  result.resize(hash.size() * 2);
  for (std::size_t index = 0; index < hash.size(); ++index) {
    const auto value = std::to_integer<std::uint8_t>(hash[index]);
    result[index * 2] = digits[value >> 4U];
    result[index * 2 + 1] = digits[value & 0x0fU];
  }
  return result;
}

std::optional<ContentHash> content_hash_from_hex(const std::string_view value) noexcept {
  if (value.size() != 64) {
    return std::nullopt;
  }
  ContentHash result{};
  for (std::size_t index = 0; index < result.size(); ++index) {
    const auto high = hex_nibble(value[index * 2]);
    const auto low = hex_nibble(value[index * 2 + 1]);
    if (!high || !low) {
      return std::nullopt;
    }
    result[index] = static_cast<std::byte>(static_cast<std::uint8_t>((*high << 4U) | *low));
  }
  return result;
}

} // namespace itchlab
