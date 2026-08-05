#pragma once

#include "itchlab/core/errors.hpp"
#include "itchlab/core/types.hpp"
#include "itchlab/itch/messages.hpp"

#include <cstdint>
#include <optional>
#include <string>
#include <string_view>
#include <unordered_map>
#include <vector>

namespace itchlab {

// Numeric values match the documented snapshot-state encoding.
enum class TradingState : std::uint8_t {
  unknown = 0,
  preopen = 1,
  trading = 2,
  halted = 3,
  paused = 4,
  quotation_only = 5,
  closed = 6,
};

[[nodiscard]] std::string_view trading_state_name(TradingState state) noexcept;

struct GlobalSessionEvent {
  MessageIndex message_index{};
  TimestampNs timestamp_ns{};
  char event_code{};

  friend bool operator==(const GlobalSessionEvent&, const GlobalSessionEvent&) = default;
};

struct TradingStateChange {
  TradingState previous{TradingState::unknown};
  TradingState current{TradingState::unknown};
  bool changed{};

  friend bool operator==(const TradingStateChange&, const TradingStateChange&) = default;
};

struct SessionError {
  ErrorCode code{ErrorCode::invariant};
  std::string message;
};

struct SessionApplyResult {
  std::optional<TradingStateChange> state_change;
  std::optional<SessionError> error;

  [[nodiscard]] bool valid() const noexcept { return !error.has_value(); }
};

// Tracks source-global daily phases and the current selected-instrument trading states.
class SessionState {
public:
  [[nodiscard]] std::optional<SessionError> register_instrument(StockLocate stock_locate);
  [[nodiscard]] SessionApplyResult apply(MessageIndex message_index, const SystemEvent& event);
  [[nodiscard]] SessionApplyResult apply(const TradingAction& action);

  [[nodiscard]] std::optional<TradingState> state(StockLocate stock_locate) const noexcept;
  [[nodiscard]] bool is_tradable(StockLocate stock_locate) const noexcept;
  [[nodiscard]] const std::vector<GlobalSessionEvent>& global_events() const noexcept {
    return global_events_;
  }

private:
  std::optional<std::uint8_t> global_phase_rank_;
  std::unordered_map<StockLocate, TradingState> states_;
  std::vector<GlobalSessionEvent> global_events_;
};

} // namespace itchlab
