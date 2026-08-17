#include <algorithm>
#include <charconv>
#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <span>
#include <string>
#include <string_view>
#include <vector>

extern "C" int LLVMFuzzerTestOneInput(const std::uint8_t* data, std::size_t size);

namespace {

struct Options {
  std::size_t runs{1000};
  std::size_t maximum_length{4096};
  std::uint64_t seed{1};
  std::filesystem::path corpus;
};

bool parse_number(const std::string_view value, std::uint64_t& destination) {
  const auto* begin = value.data();
  const auto* end = begin + value.size();
  const auto result = std::from_chars(begin, end, destination);
  return result.ec == std::errc{} && result.ptr == end;
}

bool parse_option(const std::string_view argument, const std::string_view name,
                  std::uint64_t& destination) {
  const auto prefix = std::string{name} + '=';
  return argument.starts_with(prefix) && parse_number(argument.substr(prefix.size()), destination);
}

Options parse_arguments(const std::span<char*> arguments) {
  Options options;
  for (const auto* raw : arguments.subspan(1)) {
    const std::string_view argument{raw};
    std::uint64_t value{};
    if (parse_option(argument, "-runs", value)) {
      options.runs = static_cast<std::size_t>(value);
    } else if (parse_option(argument, "-max_len", value)) {
      options.maximum_length = static_cast<std::size_t>(value);
    } else if (parse_option(argument, "-seed", value)) {
      options.seed = value;
    } else if (!argument.starts_with('-')) {
      options.corpus = argument;
    }
  }
  return options;
}

std::vector<std::uint8_t> read_seed(const std::filesystem::path& path,
                                    const std::size_t maximum_length) {
  std::ifstream stream{path, std::ios::binary | std::ios::ate};
  if (!stream.good()) {
    return {};
  }
  const auto end = stream.tellg();
  if (end < 0 || static_cast<std::uintmax_t>(end) > maximum_length) {
    return {};
  }
  std::vector<std::uint8_t> content(static_cast<std::size_t>(end));
  stream.seekg(0);
  stream.read(reinterpret_cast<char*>(content.data()),
              static_cast<std::streamsize>(content.size()));
  if (stream.fail() && !content.empty()) {
    return {};
  }
  return content;
}

std::vector<std::vector<std::uint8_t>> load_corpus(const Options& options) {
  std::vector<std::filesystem::path> paths;
  for (const auto& entry : std::filesystem::directory_iterator{options.corpus}) {
    const auto status = entry.symlink_status();
    if (std::filesystem::is_regular_file(status)) {
      paths.push_back(entry.path());
    }
  }
  std::ranges::sort(paths);
  std::vector<std::vector<std::uint8_t>> corpus;
  corpus.reserve(paths.size());
  for (const auto& path : paths) {
    corpus.push_back(read_seed(path, options.maximum_length));
  }
  return corpus;
}

std::uint64_t next_random(std::uint64_t& state) {
  state ^= state << 13U;
  state ^= state >> 7U;
  state ^= state << 17U;
  return state;
}

std::size_t random_index(std::uint64_t& state, const std::size_t size) {
  return static_cast<std::size_t>(next_random(state) % static_cast<std::uint64_t>(size));
}

void mutate(std::vector<std::uint8_t>& unit, std::uint64_t& state, const std::size_t maximum_length,
            const std::size_t iteration) {
  if (unit.empty()) {
    unit.push_back(static_cast<std::uint8_t>(next_random(state)));
    return;
  }
  switch (iteration % 4U) {
  case 0:
    unit[random_index(state, unit.size())] ^=
        static_cast<std::uint8_t>(1U << (next_random(state) % 8U));
    break;
  case 1:
    unit.resize(random_index(state, unit.size()));
    break;
  case 2:
    if (unit.size() < maximum_length) {
      unit.insert(unit.begin() + static_cast<std::ptrdiff_t>(random_index(state, unit.size() + 1U)),
                  static_cast<std::uint8_t>(next_random(state)));
    }
    break;
  default:
    unit[random_index(state, unit.size())] = static_cast<std::uint8_t>(next_random(state));
    break;
  }
}

} // namespace

int main(const int argc, char** argv) {
  const auto options = parse_arguments(std::span<char*>{argv, static_cast<std::size_t>(argc)});
  if (options.runs == 0 || options.maximum_length == 0 || options.corpus.empty() ||
      !std::filesystem::is_directory(options.corpus)) {
    std::cerr << "standalone-fuzz: invalid run, length or corpus argument\n";
    return 2;
  }
  const auto corpus = load_corpus(options);
  if (corpus.empty()) {
    std::cerr << "standalone-fuzz: maintained corpus is empty\n";
    return 2;
  }

  auto random_state = options.seed == 0 ? std::uint64_t{1} : options.seed;
  for (std::size_t iteration = 0; iteration < options.runs; ++iteration) {
    auto unit = corpus[iteration % corpus.size()];
    if (iteration >= corpus.size()) {
      mutate(unit, random_state, options.maximum_length, iteration);
    }
    static_cast<void>(LLVMFuzzerTestOneInput(unit.data(), unit.size()));
  }
  std::cout << "standalone-fuzz: runs=" << options.runs << " corpus=" << corpus.size() << '\n';
  return 0;
}
