#include <catch2/catch_test_macros.hpp>
#include <nlohmann/json.hpp>

#include <atomic>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <optional>
#include <string>
#include <string_view>
#include <system_error>
#include <thread>
#include <vector>

#if defined(__APPLE__) || defined(__linux__)
#include <csignal>
#include <fcntl.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>
#endif

namespace {

using Json = nlohmann::json;

std::filesystem::path repository_path(const std::string_view relative_path) {
  return std::filesystem::path{ITCHLAB_SOURCE_DIR} / relative_path;
}

std::string read_file(const std::filesystem::path& path) {
  std::ifstream stream{path, std::ios::binary};
  REQUIRE(stream.good());
  return {std::istreambuf_iterator<char>{stream}, std::istreambuf_iterator<char>{}};
}

void write_file(const std::filesystem::path& path, const std::string_view bytes) {
  std::ofstream stream{path, std::ios::binary};
  REQUIRE(stream.good());
  stream.write(bytes.data(), static_cast<std::streamsize>(bytes.size()));
  stream.close();
  REQUIRE_FALSE(stream.fail());
}

class TemporaryDirectory {
public:
  TemporaryDirectory() {
    static std::atomic<std::uint64_t> sequence{};
    const auto timestamp = std::chrono::steady_clock::now().time_since_epoch().count();
    path_ = std::filesystem::temp_directory_path() /
            ("itchlab-task012-cancel-" + std::to_string(timestamp) + '-' +
             std::to_string(sequence.fetch_add(1)));
    std::error_code error;
    REQUIRE(std::filesystem::create_directory(path_, error));
    REQUIRE_FALSE(error);
  }

  TemporaryDirectory(const TemporaryDirectory&) = delete;
  TemporaryDirectory& operator=(const TemporaryDirectory&) = delete;

  ~TemporaryDirectory() {
    std::error_code ignored;
    std::filesystem::remove_all(path_, ignored);
  }

  [[nodiscard]] const std::filesystem::path& path() const noexcept { return path_; }

private:
  std::filesystem::path path_;
};

std::string first_frame_of_type(const std::string_view stream, const char source_type) {
  std::size_t offset{};
  while (offset + 2 <= stream.size()) {
    const auto high = static_cast<std::uint8_t>(stream[offset]);
    const auto low = static_cast<std::uint8_t>(stream[offset + 1]);
    const auto payload_size = static_cast<std::size_t>((static_cast<std::uint16_t>(high) << 8U) |
                                                       static_cast<std::uint16_t>(low));
    const auto frame_size = payload_size + 2;
    REQUIRE(offset + frame_size <= stream.size());
    if (payload_size != 0 && stream[offset + 2] == source_type) {
      return std::string{stream.substr(offset, frame_size)};
    }
    offset += frame_size;
  }
  FAIL("Requested source type is absent from the synthetic stream");
  return {};
}

std::string with_timestamp(std::string frame, const std::uint64_t timestamp_ns) {
  REQUIRE(frame.size() >= 13);
  for (std::size_t index = 0; index < 6; ++index) {
    const auto shift = static_cast<unsigned>((5 - index) * 8U);
    frame[7 + index] = static_cast<char>(static_cast<std::uint8_t>(timestamp_ns >> shift));
  }
  return frame;
}

std::filesystem::path write_config(const std::filesystem::path& destination,
                                   const std::filesystem::path& input) {
  auto config = Json::parse(read_file(repository_path("configs/replay.diagnostic.example.json")));
  config["input"]["path"] = input.string();
  config["selection"]["session_start_ns"] = 0;
  config["selection"]["session_end_ns"] = 86'400'000'000'000ULL;
  config["selection"]["require_trading_state"] = false;
  config["output"]["emit_unchanged_trade_snapshots"] = false;
  write_file(destination, config.dump(2) + '\n');
  return destination;
}

#if defined(__APPLE__) || defined(__linux__)

pid_t spawn_replay(const std::filesystem::path& config, const std::filesystem::path& output_root,
                   const std::filesystem::path& stdout_path,
                   const std::filesystem::path& stderr_path) {
  const auto child = ::fork();
  REQUIRE(child >= 0);
  if (child == 0) {
    const auto stdout_fd = ::open(stdout_path.c_str(), O_WRONLY | O_CREAT | O_TRUNC, 0600);
    const auto stderr_fd = ::open(stderr_path.c_str(), O_WRONLY | O_CREAT | O_TRUNC, 0600);
    if (stdout_fd < 0 || stderr_fd < 0 || ::dup2(stdout_fd, STDOUT_FILENO) < 0 ||
        ::dup2(stderr_fd, STDERR_FILENO) < 0) {
      ::_exit(126);
    }
    static_cast<void>(::close(stdout_fd));
    static_cast<void>(::close(stderr_fd));

    const std::string binary{ITCHLAB_BINARY_PATH};
    const std::string config_text = config.string();
    const std::string output_text = output_root.string();
    ::execl(binary.c_str(), binary.c_str(), "replay", "--config", config_text.c_str(),
            "--output-root", output_text.c_str(), "--format", "json", "--quiet",
            static_cast<char*>(nullptr));
    ::_exit(127);
  }
  return child;
}

int wait_for_child(const pid_t child) {
  int status{};
  REQUIRE(::waitpid(child, &status, 0) == child);
  REQUIRE(WIFEXITED(status));
  return WEXITSTATUS(status);
}

std::optional<std::filesystem::path> find_file_named(const std::filesystem::path& root,
                                                     const std::string_view filename) {
  if (!std::filesystem::is_directory(root)) {
    return std::nullopt;
  }
  for (const auto& entry : std::filesystem::recursive_directory_iterator{root}) {
    if (entry.is_regular_file() && entry.path().filename() == filename) {
      return entry.path();
    }
  }
  return std::nullopt;
}

#endif

} // namespace

TEST_CASE("E2E-004 TASK-012 SIGINT closes partial outputs, exits 130 and permits a clean rerun",
          "[TASK-012][E2E-004][cancellation][process][security]") {
#if !defined(__APPLE__) && !defined(__linux__)
  SKIP("TASK-012 process-signal E2E is supported on the declared macOS/Linux targets");
#else
  TemporaryDirectory temporary;
  const auto mixed = read_file(repository_path("tests/fixtures/synthetic_mixed.itch"));
  const auto trade_frame = with_timestamp(first_frame_of_type(mixed, 'P'), 80'000'000'000'000ULL);
  std::string long_source = mixed;
  constexpr std::size_t repetitions = 500'000;
  long_source.reserve(mixed.size() + trade_frame.size() * repetitions);
  for (std::size_t index = 0; index < repetitions; ++index) {
    long_source.append(trade_frame);
  }
  const auto source_path = temporary.path() / "long.itch";
  write_file(source_path, long_source);
  const auto config = write_config(temporary.path() / "cancel.json", source_path);
  const auto output_root = temporary.path() / "cancel-output";
  const auto stdout_path = temporary.path() / "cancel.stdout";
  const auto stderr_path = temporary.path() / "cancel.stderr";

  const auto child = spawn_replay(config, output_root, stdout_path, stderr_path);
  std::optional<std::filesystem::path> event_partial;
  bool output_started{};
  bool child_exited{};
  int early_status{};
  for (std::size_t attempt = 0; attempt < 5'000; ++attempt) {
    std::error_code error;
    event_partial = find_file_named(output_root, "events.ilb.partial");
    if (event_partial) {
      const auto size = std::filesystem::file_size(*event_partial, error);
      if (!error && size > 104 + 16) {
        output_started = true;
        break;
      }
    }
    const auto waited = ::waitpid(child, &early_status, WNOHANG);
    if (waited == child) {
      child_exited = true;
      break;
    }
    REQUIRE(waited >= 0);
    std::this_thread::sleep_for(std::chrono::milliseconds{1});
  }
  if (!output_started && !child_exited) {
    static_cast<void>(::kill(child, SIGKILL));
    static_cast<void>(::waitpid(child, &early_status, 0));
  }
  REQUIRE_FALSE(child_exited);
  REQUIRE(output_started);
  REQUIRE(::kill(child, SIGINT) == 0);
  REQUIRE(wait_for_child(child) == 130);

  REQUIRE(read_file(source_path) == long_source);
  REQUIRE_FALSE(find_file_named(output_root, "events.ilb").has_value());
  REQUIRE_FALSE(find_file_named(output_root, "snapshots.ilb").has_value());
  REQUIRE_FALSE(find_file_named(output_root, "replay-manifest.json").has_value());
  REQUIRE(event_partial.has_value());
  REQUIRE(std::filesystem::exists(*event_partial));
  REQUIRE(find_file_named(output_root, "snapshots.ilb.partial").has_value());
  REQUIRE(std::filesystem::file_size(*event_partial) > 104 + 16);

  const auto cancelled = Json::parse(read_file(stdout_path));
  REQUIRE(cancelled.at("status") == "cancelled");
  REQUIRE(cancelled.at("error").at("code") == "ERR_CANCELLED");
  REQUIRE(read_file(stderr_path).find("Cancellation requested; closing partial outputs") !=
          std::string::npos);

  const auto clean_source = repository_path("tests/fixtures/synthetic_minimal.itch");
  const auto clean_config = write_config(temporary.path() / "clean.json", clean_source);
  const auto clean_root = temporary.path() / "clean-output";
  const auto clean_child = spawn_replay(clean_config, clean_root, temporary.path() / "clean.stdout",
                                        temporary.path() / "clean.stderr");
  REQUIRE(wait_for_child(clean_child) == 0);
  REQUIRE(find_file_named(clean_root, "events.ilb").has_value());
  REQUIRE(find_file_named(clean_root, "snapshots.ilb").has_value());
  REQUIRE(find_file_named(clean_root, "replay-manifest.json").has_value());
#endif
}
