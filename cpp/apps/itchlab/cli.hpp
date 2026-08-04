#pragma once

#include <iosfwd>
#include <span>
#include <string_view>

namespace itchlab::cli {

// Runs arguments excluding argv[0]. Streams are injected so command contracts can be tested
// without spawning a shell.
int run(std::span<const std::string_view> arguments, std::ostream& output, std::ostream& error);

} // namespace itchlab::cli
