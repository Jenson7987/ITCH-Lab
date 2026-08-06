#pragma once

#include <atomic>
#include <csignal>

namespace itchlab {

// Copyable read-only view over either a normal atomic cancellation source or a signal-safe flag.
class CancellationToken {
public:
  constexpr CancellationToken() noexcept = default;
  explicit CancellationToken(const std::atomic_bool& requested) noexcept;
  explicit CancellationToken(const volatile std::sig_atomic_t& requested) noexcept;

  [[nodiscard]] bool is_cancellation_requested() const noexcept;

private:
  const std::atomic_bool* atomic_requested_{};
  const volatile std::sig_atomic_t* signal_requested_{};
};

class CancellationSource {
public:
  void request_cancellation() noexcept;
  [[nodiscard]] CancellationToken token() const noexcept;

private:
  std::atomic_bool requested_{};
};

} // namespace itchlab
