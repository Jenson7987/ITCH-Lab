#include "itchlab/replay/session_state.hpp"

#include <optional>
#include <string>
#include <utility>

namespace itchlab {
namespace {

[[nodiscard]] std::optional<std::uint8_t> phase_rank(const char event_code) noexcept {
  switch (event_code) {
  case 'O':
    return 0;
  case 'S':
    return 1;
  case 'Q':
    return 2;
  case 'M':
    return 3;
  case 'E':
    return 4;
  case 'C':
    return 5;
  default:
    return std::nullopt;
  }
}

[[nodiscard]] std::optional<TradingState> decode_trading_state(const char state) noexcept {
  switch (state) {
  case 'H':
    return TradingState::halted;
  case 'P':
    return TradingState::paused;
  case 'Q':
    return TradingState::quotation_only;
  case 'T':
    return TradingState::trading;
  default:
    return std::nullopt;
  }
}

[[nodiscard]] SessionApplyResult failure(std::string message) {
  return SessionApplyResult{std::nullopt, SessionError{ErrorCode::invariant, std::move(message)}};
}

} // namespace

std::string_view trading_state_name(const TradingState state) noexcept {
  switch (state) {
  case TradingState::unknown:
    return "unknown";
  case TradingState::preopen:
    return "preopen";
  case TradingState::trading:
    return "trading";
  case TradingState::halted:
    return "halted";
  case TradingState::paused:
    return "paused";
  case TradingState::quotation_only:
    return "quotation_only";
  case TradingState::closed:
    return "closed";
  }
  return "unknown";
}

std::optional<SessionError> SessionState::register_instrument(const StockLocate stock_locate) {
  if (stock_locate == 0) {
    return SessionError{ErrorCode::invariant,
                        "Selected instrument cannot use the global stock locate zero."};
  }
  if (states_.contains(stock_locate)) {
    return std::nullopt;
  }

  TradingState initial = TradingState::preopen;
  if (global_phase_rank_ && *global_phase_rank_ >= 3) {
    initial = TradingState::closed;
  } else if (global_phase_rank_ && *global_phase_rank_ >= 1) {
    // Nasdaq specifies that a security absent from the pre-opening T spin is halted at system open.
    initial = TradingState::halted;
  }
  states_.emplace(stock_locate, initial);
  return std::nullopt;
}

SessionApplyResult SessionState::apply(const MessageIndex message_index, const SystemEvent& event) {
  if (event.header.stock_locate != 0) {
    return failure("System Event must use the global stock locate zero.");
  }
  const auto next_rank = phase_rank(event.event_code);
  if (!next_rank) {
    return failure("System Event contains an unsupported daily event code.");
  }
  if (global_phase_rank_ && *next_rank <= *global_phase_rank_) {
    return failure("System Event codes do not follow the daily session sequence.");
  }

  if (*next_rank == 1) {
    for (auto& [locate, state] : states_) {
      static_cast<void>(locate);
      if (state == TradingState::preopen) {
        state = TradingState::halted;
      }
    }
  } else if (*next_rank >= 3) {
    for (auto& [locate, state] : states_) {
      static_cast<void>(locate);
      state = TradingState::closed;
    }
  }

  global_phase_rank_ = *next_rank;
  global_events_.push_back(
      GlobalSessionEvent{message_index, event.header.timestamp_ns, event.event_code});
  return SessionApplyResult{};
}

SessionApplyResult SessionState::apply(const TradingAction& action) {
  const auto instrument = states_.find(action.header.stock_locate);
  if (instrument == states_.end()) {
    return failure("Trading Action does not belong to a registered selected instrument.");
  }
  if (global_phase_rank_ && *global_phase_rank_ >= 3) {
    return failure("Trading Action cannot reopen an instrument after end of market hours.");
  }
  const auto next_state = decode_trading_state(action.trading_state);
  if (!next_state) {
    return failure("Trading Action contains an unsupported trading-state code.");
  }

  const auto previous = instrument->second;
  instrument->second = *next_state;
  return SessionApplyResult{TradingStateChange{previous, *next_state, previous != *next_state},
                            std::nullopt};
}

std::optional<TradingState> SessionState::state(const StockLocate stock_locate) const noexcept {
  const auto instrument = states_.find(stock_locate);
  if (instrument == states_.end()) {
    return std::nullopt;
  }
  return instrument->second;
}

bool SessionState::is_tradable(const StockLocate stock_locate) const noexcept {
  return state(stock_locate) == TradingState::trading;
}

} // namespace itchlab
