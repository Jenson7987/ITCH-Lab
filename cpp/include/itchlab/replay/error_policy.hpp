#pragma once

#include "itchlab/config/replay_config.hpp"
#include "itchlab/core/errors.hpp"

#include <cstdint>
#include <map>

namespace itchlab {

enum class ReplayErrorOrigin : std::uint8_t {
  decoder,
  book_apply,
};

enum class ErrorDisposition : std::uint8_t {
  stop,
  skip,
  budget_exceeded,
};

struct ErrorPolicyDecision {
  ErrorDisposition disposition{ErrorDisposition::stop};
  bool counter_overflow{};
};

class ErrorPolicy {
public:
  explicit ErrorPolicy(ReplayValidationConfig config) noexcept;

  [[nodiscard]] ErrorPolicyDecision observe(ReplayErrorOrigin origin, ErrorCode code);
  [[nodiscard]] bool safely_skippable(ReplayErrorOrigin origin, ErrorCode code) const noexcept;
  [[nodiscard]] std::uint64_t error_count() const noexcept { return error_count_; }
  [[nodiscard]] std::uint64_t skipped_messages() const noexcept { return skipped_messages_; }
  [[nodiscard]] bool degraded() const noexcept { return skipped_messages_ != 0; }
  [[nodiscard]] const std::map<ErrorCode, std::uint64_t>& counts_by_code() const noexcept {
    return counts_by_code_;
  }

private:
  ReplayValidationConfig config_;
  std::uint64_t error_count_{};
  std::uint64_t skipped_messages_{};
  std::map<ErrorCode, std::uint64_t> counts_by_code_;
};

} // namespace itchlab
