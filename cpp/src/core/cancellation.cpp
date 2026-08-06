#include "itchlab/core/cancellation.hpp"

namespace itchlab {

CancellationToken::CancellationToken(const std::atomic_bool& requested) noexcept
    : atomic_requested_{&requested} {}

CancellationToken::CancellationToken(const volatile std::sig_atomic_t& requested) noexcept
    : signal_requested_{&requested} {}

bool CancellationToken::is_cancellation_requested() const noexcept {
  return (atomic_requested_ != nullptr && atomic_requested_->load(std::memory_order_relaxed)) ||
         (signal_requested_ != nullptr && *signal_requested_ != 0);
}

void CancellationSource::request_cancellation() noexcept {
  requested_.store(true, std::memory_order_relaxed);
}

CancellationToken CancellationSource::token() const noexcept {
  return CancellationToken{requested_};
}

} // namespace itchlab
