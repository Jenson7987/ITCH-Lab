#include <iostream>
#include <string_view>

namespace {

constexpr std::string_view kProgramName{"itchlab"};

void print_help() {
  std::cout << "Offline Nasdaq ITCH research platform\n\n"
            << "Usage: itchlab [--help] [--version]\n\n"
            << "Options:\n"
            << "  --help       Show this help text.\n"
            << "  --version    Show the application version.\n\n"
            << "Data inspection and replay commands are not yet implemented.\n";
}

} // namespace

int main(int argc, char* argv[]) {
  if (argc == 1) {
    print_help();
    return 0;
  }

  if (argc == 2) {
    const std::string_view argument{argv[1]};
    if (argument == "--help") {
      print_help();
      return 0;
    }
    if (argument == "--version") {
      std::cout << kProgramName << ' ' << ITCHLAB_VERSION << '\n';
      return 0;
    }
  }

  std::cerr << kProgramName << ": unrecognised argument(s).\n"
            << "Try '" << kProgramName << " --help' for usage.\n";
  return 2;
}
