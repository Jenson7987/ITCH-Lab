#pragma once

#include "itchlab/core/cancellation.hpp"
#include "itchlab/replay/progress_reporter.hpp"

#include <filesystem>
#include <iosfwd>
#include <span>
#include <string_view>
#include <utility>

namespace itchlab::cli {

struct RuntimeContext {
  RuntimeContext() = default;
  RuntimeContext(CancellationToken cancellation_value, const ProgressClock* clock,
                 std::filesystem::path path = {})
      : cancellation{cancellation_value}, progress_clock{clock}, executable_path{std::move(path)} {}

  CancellationToken cancellation;
  const ProgressClock* progress_clock{};
  std::filesystem::path executable_path;
};

// Runs arguments excluding argv[0]. Streams are injected so command contracts can be tested
// without spawning a shell.
int run(std::span<const std::string_view> arguments, std::ostream& output, std::ostream& error);
int run(std::span<const std::string_view> arguments, std::ostream& output, std::ostream& error,
        RuntimeContext runtime);

} // namespace itchlab::cli
