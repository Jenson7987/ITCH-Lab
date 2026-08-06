#include "signal_adapter.hpp"

#include <cstdlib>

namespace itchlab::cli {
namespace {

volatile std::sig_atomic_t interrupt_count{};

extern "C" void handle_interrupt(int) {
  if (interrupt_count == 0) {
    interrupt_count = 1;
    return;
  }
  std::_Exit(130);
}

} // namespace

SignalAdapter::SignalAdapter() noexcept {
  interrupt_count = 0;
  previous_ = std::signal(SIGINT, handle_interrupt);
  installed_ = previous_ != SIG_ERR;
}

SignalAdapter::~SignalAdapter() {
  if (installed_) {
    static_cast<void>(std::signal(SIGINT, previous_));
  }
}

CancellationToken SignalAdapter::token() const noexcept {
  return installed_ ? CancellationToken{interrupt_count} : CancellationToken{};
}

} // namespace itchlab::cli
