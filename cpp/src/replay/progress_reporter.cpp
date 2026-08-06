#include "itchlab/replay/progress_reporter.hpp"

#include <chrono>
#include <utility>

namespace itchlab {

ProgressClock::TimePoint SteadyProgressClock::now() const noexcept {
  return std::chrono::steady_clock::now();
}

ProgressReporter::ProgressReporter(const ProgressClock& clock, Callback callback)
    : clock_{clock}, callback_{std::move(callback)}, started_at_{clock_.now()},
      last_reported_at_{started_at_} {}

void ProgressReporter::observe(const std::uint64_t messages, const SourceProgress source_progress,
                               const std::uint64_t selected_events,
                               const std::uint64_t error_count) {
  const auto now = clock_.now();
  const auto elapsed = now - started_at_;
  if (!reported_) {
    if (elapsed < kInitialDelay) {
      return;
    }
  } else {
    const auto message_delta =
        messages >= last_reported_messages_ ? messages - last_reported_messages_ : 0;
    if (now - last_reported_at_ < kMaximumInterval && message_delta < kMessageInterval) {
      return;
    }
  }

  const auto elapsed_ms = std::chrono::duration_cast<std::chrono::milliseconds>(elapsed).count();
  callback_(ReplayProgress{"replay", messages, source_progress.source_bytes_consumed,
                           selected_events, static_cast<std::uint64_t>(elapsed_ms), error_count});
  reported_ = true;
  last_reported_at_ = now;
  last_reported_messages_ = messages;
}

} // namespace itchlab
