#pragma once

#include "itchlab/input/byte_source.hpp"

#include <chrono>
#include <cstdint>
#include <functional>
#include <string_view>

namespace itchlab {

struct ReplayProgress {
  std::string_view stage{"replay"};
  std::uint64_t messages{};
  std::uint64_t source_bytes{};
  std::uint64_t selected_events{};
  std::uint64_t elapsed_ms{};
  std::uint64_t error_count{};
};

class ProgressClock {
public:
  using TimePoint = std::chrono::steady_clock::time_point;

  [[nodiscard]] virtual TimePoint now() const noexcept = 0;
  virtual ~ProgressClock() = default;
};

class SteadyProgressClock final : public ProgressClock {
public:
  [[nodiscard]] TimePoint now() const noexcept override;
};

class ProgressReporter {
public:
  using Callback = std::function<void(const ReplayProgress&)>;

  ProgressReporter(const ProgressClock& clock, Callback callback);

  void observe(std::uint64_t messages, SourceProgress source_progress,
               std::uint64_t selected_events, std::uint64_t error_count);

private:
  static constexpr auto kInitialDelay = std::chrono::seconds{5};
  static constexpr auto kMaximumInterval = std::chrono::seconds{30};
  static constexpr std::uint64_t kMessageInterval = 10'000'000;

  const ProgressClock& clock_;
  Callback callback_;
  ProgressClock::TimePoint started_at_;
  ProgressClock::TimePoint last_reported_at_;
  std::uint64_t last_reported_messages_{};
  bool reported_{};
};

} // namespace itchlab
