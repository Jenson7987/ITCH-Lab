#include "itchlab/replay/error_policy.hpp"

#include <limits>

namespace itchlab {

ErrorPolicy::ErrorPolicy(const ReplayValidationConfig config) noexcept : config_{config} {}

bool ErrorPolicy::safely_skippable(const ReplayErrorOrigin origin,
                                   const ErrorCode code) const noexcept {
  if (origin == ReplayErrorOrigin::decoder) {
    return code == ErrorCode::message_length || code == ErrorCode::unknown_message ||
           code == ErrorCode::timestamp || code == ErrorCode::invariant;
  }
  return code == ErrorCode::order_reference || code == ErrorCode::quantity;
}

ErrorPolicyDecision ErrorPolicy::observe(const ReplayErrorOrigin origin, const ErrorCode code) {
  auto& code_count = counts_by_code_[code];
  if (error_count_ == std::numeric_limits<std::uint64_t>::max() ||
      code_count == std::numeric_limits<std::uint64_t>::max()) {
    return ErrorPolicyDecision{ErrorDisposition::stop, true};
  }
  ++error_count_;
  ++code_count;

  if (config_.mode == ValidationMode::strict || !safely_skippable(origin, code)) {
    return ErrorPolicyDecision{ErrorDisposition::stop, false};
  }
  if (skipped_messages_ >= config_.max_skipped_messages) {
    return ErrorPolicyDecision{ErrorDisposition::budget_exceeded, false};
  }
  ++skipped_messages_;
  return ErrorPolicyDecision{ErrorDisposition::skip, false};
}

} // namespace itchlab
