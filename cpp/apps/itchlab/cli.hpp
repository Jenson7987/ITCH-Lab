#pragma once

#include "itchlab/core/cancellation.hpp"
#include "itchlab/replay/progress_reporter.hpp"

#include <iosfwd>
#include <span>
#include <string_view>

namespace itchlab::cli {

struct RuntimeContext {
  CancellationToken cancellation;
  const ProgressClock* progress_clock{};
};

// Runs arguments excluding argv[0]. Streams are injected so command contracts can be tested
// without spawning a shell.
int run(std::span<const std::string_view> arguments, std::ostream& output, std::ostream& error);
int run(std::span<const std::string_view> arguments, std::ostream& output, std::ostream& error,
        RuntimeContext runtime);

} // namespace itchlab::cli
