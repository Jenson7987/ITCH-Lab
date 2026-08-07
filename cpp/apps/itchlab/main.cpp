#include "itchlab/cli.hpp"
#include "signal_adapter.hpp"

#include <iostream>
#include <string_view>
#include <vector>

int main(int argc, char* argv[]) {
  std::vector<std::string_view> arguments;
  if (argc > 1) {
    arguments.reserve(static_cast<std::size_t>(argc - 1));
  }
  for (int index = 1; index < argc; ++index) {
    arguments.emplace_back(argv[index]);
  }
  if (!arguments.empty() && arguments.front() == "replay") {
    const itchlab::cli::SignalAdapter signals;
    if (!signals.installed()) {
      std::cerr << "ERR_INTERNAL: Could not install the replay interrupt handler.\n";
      return 70;
    }
    return itchlab::cli::run(arguments, std::cout, std::cerr,
                             itchlab::cli::RuntimeContext{signals.token(), nullptr, argv[0]});
  }
  return itchlab::cli::run(arguments, std::cout, std::cerr);
}
