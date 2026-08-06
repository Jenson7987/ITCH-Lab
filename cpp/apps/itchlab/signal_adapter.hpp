#pragma once

#include "itchlab/core/cancellation.hpp"

#include <csignal>

namespace itchlab::cli {

class SignalAdapter {
public:
  SignalAdapter() noexcept;
  SignalAdapter(const SignalAdapter&) = delete;
  SignalAdapter& operator=(const SignalAdapter&) = delete;
  ~SignalAdapter();

  [[nodiscard]] CancellationToken token() const noexcept;
  [[nodiscard]] bool installed() const noexcept { return installed_; }

private:
  using SignalHandler = void (*)(int);

  SignalHandler previous_{};
  bool installed_{};
};

} // namespace itchlab::cli
