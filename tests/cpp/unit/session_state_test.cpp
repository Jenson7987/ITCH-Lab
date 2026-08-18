#include "itchlab/replay/session_state.hpp"

#include <catch2/catch_test_macros.hpp>

#include <algorithm>
#include <string>
#include <string_view>

namespace {

itchlab::StockField stock_field(const std::string_view symbol) {
  itchlab::StockField field{};
  field.fill(' ');
  std::copy(symbol.begin(), symbol.end(), field.begin());
  return field;
}

itchlab::SystemEvent system_event(const char code, const itchlab::TimestampNs timestamp_ns) {
  return itchlab::SystemEvent{itchlab::MessageHeader{0, 1, timestamp_ns}, code};
}

itchlab::TradingAction trading_action(const itchlab::StockLocate locate, const char state) {
  return itchlab::TradingAction{itchlab::MessageHeader{locate, 1, 10'000},
                                stock_field("AAPL"),
                                state,
                                ' ',
                                {' ', ' ', ' ', ' '}};
}

} // namespace

TEST_CASE("TASK-011 session state applies the official global and per-stock state model",
          "[TASK-011][session][unit]") {
  itchlab::SessionState session;
  REQUIRE(session.apply(0, system_event('O', 1'000)).valid());
  REQUIRE_FALSE(session.register_instrument(1).has_value());
  REQUIRE_FALSE(session.register_instrument(2).has_value());
  REQUIRE(session.state(1) == itchlab::TradingState::preopen);

  const auto trading = session.apply(trading_action(1, 'T'));
  REQUIRE(trading.valid());
  REQUIRE(trading.state_change == itchlab::TradingStateChange{itchlab::TradingState::preopen,
                                                              itchlab::TradingState::trading,
                                                              true});
  REQUIRE(session.is_tradable(1));

  REQUIRE(session.apply(1, system_event('S', 28'800'000'000'000)).valid());
  REQUIRE(session.state(1) == itchlab::TradingState::trading);
  REQUIRE(session.state(2) == itchlab::TradingState::halted);
  REQUIRE_FALSE(session.is_tradable(2));
  REQUIRE(session.apply(2, system_event('Q', 34'200'000'000'000)).valid());

  for (const auto& [code, expected] : {
           std::pair{'H', itchlab::TradingState::halted},
           std::pair{'P', itchlab::TradingState::paused},
           std::pair{'Q', itchlab::TradingState::quotation_only},
           std::pair{'T', itchlab::TradingState::trading},
       }) {
    const auto change = session.apply(trading_action(1, code));
    REQUIRE(change.valid());
    REQUIRE(change.state_change->current == expected);
  }

  const auto repeated = session.apply(trading_action(1, 'T'));
  REQUIRE(repeated.valid());
  REQUIRE_FALSE(repeated.state_change->changed);

  REQUIRE(session.apply(3, system_event('M', 57'600'000'000'000)).valid());
  REQUIRE(session.state(1) == itchlab::TradingState::closed);
  REQUIRE(session.state(2) == itchlab::TradingState::closed);
  REQUIRE(session.apply(4, system_event('E', 72'000'000'000'000)).valid());
  REQUIRE(session.apply(5, system_event('C', 72'000'000'001'000)).valid());
  REQUIRE(session.global_events().size() == 6);
  REQUIRE(session.global_events().front() == itchlab::GlobalSessionEvent{0, 1'000, 'O'});
  REQUIRE(session.global_events().back() ==
          itchlab::GlobalSessionEvent{5, 72'000'000'001'000, 'C'});
}

TEST_CASE("TASK-011 session state rejects invalid transitions without partial mutation",
          "[TASK-011][session][unit][error]") {
  itchlab::SessionState session;
  REQUIRE_FALSE(session.register_instrument(1).has_value());

  auto invalid_locate = system_event('O', 1'000);
  invalid_locate.header.stock_locate = 1;
  REQUIRE_FALSE(session.apply(0, invalid_locate).valid());
  REQUIRE(session.global_events().empty());

  REQUIRE(session.apply(0, system_event('O', 1'000)).valid());
  REQUIRE_FALSE(session.apply(1, system_event('O', 2'000)).valid());
  REQUIRE(session.global_events().size() == 1);

  REQUIRE_FALSE(session.apply(trading_action(1, 'X')).valid());
  REQUIRE(session.state(1) == itchlab::TradingState::preopen);

  REQUIRE(session.apply(2, system_event('M', 57'600'000'000'000)).valid());
  REQUIRE_FALSE(session.apply(trading_action(1, 'T')).valid());
  REQUIRE(session.state(1) == itchlab::TradingState::closed);

  REQUIRE(session.register_instrument(0)->code == itchlab::ErrorCode::invariant);
  REQUIRE_FALSE(session.apply(trading_action(99, 'T')).valid());
}

TEST_CASE("TASK-011 trading-state names match the persisted vocabulary",
          "[TASK-011][session][unit][contract]") {
  REQUIRE(std::string{itchlab::trading_state_name(itchlab::TradingState::unknown)} == "unknown");
  REQUIRE(std::string{itchlab::trading_state_name(itchlab::TradingState::preopen)} == "preopen");
  REQUIRE(std::string{itchlab::trading_state_name(itchlab::TradingState::trading)} == "trading");
  REQUIRE(std::string{itchlab::trading_state_name(itchlab::TradingState::halted)} == "halted");
  REQUIRE(std::string{itchlab::trading_state_name(itchlab::TradingState::paused)} == "paused");
  REQUIRE(std::string{itchlab::trading_state_name(itchlab::TradingState::quotation_only)} ==
          "quotation_only");
  REQUIRE(std::string{itchlab::trading_state_name(itchlab::TradingState::closed)} == "closed");
}
