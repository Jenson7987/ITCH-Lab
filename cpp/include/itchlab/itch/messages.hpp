#pragma once

#include "itchlab/core/types.hpp"

#include <array>
#include <cstddef>
#include <cstdint>
#include <string_view>
#include <variant>

namespace itchlab {

template <std::size_t Width> using AlphaField = std::array<char, Width>;

using StockField = AlphaField<8>;
using IssueSubTypeField = AlphaField<2>;
using AttributionField = AlphaField<4>;
using TradingReasonField = AlphaField<4>;

// ITCH alpha fields are right-padded with spaces. The returned view aliases the field.
template <std::size_t Width>
[[nodiscard]] constexpr std::string_view trimmed_alpha(const AlphaField<Width>& field) noexcept {
  std::size_t length = Width;
  while (length > 0 && field[length - 1] == ' ') {
    --length;
  }
  return {field.data(), length};
}

struct MessageHeader {
  StockLocate stock_locate{};
  std::uint16_t tracking_number{};
  TimestampNs timestamp_ns{};

  friend bool operator==(const MessageHeader&, const MessageHeader&) = default;
};

struct SystemEvent {
  MessageHeader header;
  char event_code{};

  friend bool operator==(const SystemEvent&, const SystemEvent&) = default;
};

struct StockDirectory {
  MessageHeader header;
  StockField stock{};
  char market_category{};
  char financial_status{};
  std::uint32_t round_lot_size{};
  char round_lots_only{};
  char issue_classification{};
  IssueSubTypeField issue_sub_type{};
  char authenticity{};
  char short_sale_threshold_indicator{};
  char ipo_flag{};
  char luld_reference_price_tier{};
  char etp_flag{};
  std::uint32_t etp_leverage_factor{};
  char inverse_indicator{};

  friend bool operator==(const StockDirectory&, const StockDirectory&) = default;
};

struct TradingAction {
  MessageHeader header;
  StockField stock{};
  char trading_state{};
  char reserved{};
  TradingReasonField reason{};

  friend bool operator==(const TradingAction&, const TradingAction&) = default;
};

struct AddOrder {
  MessageHeader header;
  OrderReference order_reference{};
  Side side{Side::not_applicable};
  Shares shares{};
  StockField stock{};
  Price4 price4{};

  friend bool operator==(const AddOrder&, const AddOrder&) = default;
};

struct AddOrderWithAttribution {
  MessageHeader header;
  OrderReference order_reference{};
  Side side{Side::not_applicable};
  Shares shares{};
  StockField stock{};
  Price4 price4{};
  AttributionField attribution{};

  friend bool operator==(const AddOrderWithAttribution&, const AddOrderWithAttribution&) = default;
};

struct OrderExecuted {
  MessageHeader header;
  OrderReference order_reference{};
  Shares executed_shares{};
  MatchNumber match_number{};

  friend bool operator==(const OrderExecuted&, const OrderExecuted&) = default;
};

struct OrderExecutedWithPrice {
  MessageHeader header;
  OrderReference order_reference{};
  Shares executed_shares{};
  MatchNumber match_number{};
  char printable{};
  Price4 execution_price4{};

  friend bool operator==(const OrderExecutedWithPrice&, const OrderExecutedWithPrice&) = default;
};

struct OrderCancel {
  MessageHeader header;
  OrderReference order_reference{};
  Shares cancelled_shares{};

  friend bool operator==(const OrderCancel&, const OrderCancel&) = default;
};

struct OrderDelete {
  MessageHeader header;
  OrderReference order_reference{};

  friend bool operator==(const OrderDelete&, const OrderDelete&) = default;
};

struct OrderReplace {
  MessageHeader header;
  OrderReference original_order_reference{};
  OrderReference new_order_reference{};
  Shares shares{};
  Price4 price4{};

  friend bool operator==(const OrderReplace&, const OrderReplace&) = default;
};

struct Trade {
  MessageHeader header;
  OrderReference order_reference{};
  // This is the raw ITCH buy/sell indicator, not an inferred aggressor side.
  Side buy_sell_indicator{Side::not_applicable};
  Shares shares{};
  StockField stock{};
  Price4 price4{};
  MatchNumber match_number{};

  friend bool operator==(const Trade&, const Trade&) = default;
};

struct CrossTrade {
  MessageHeader header;
  Shares shares{};
  StockField stock{};
  Price4 cross_price4{};
  MatchNumber match_number{};
  char cross_type{};

  friend bool operator==(const CrossTrade&, const CrossTrade&) = default;
};

struct BrokenTrade {
  MessageHeader header;
  MatchNumber match_number{};

  friend bool operator==(const BrokenTrade&, const BrokenTrade&) = default;
};

using ItchMessage =
    std::variant<SystemEvent, StockDirectory, TradingAction, AddOrder, AddOrderWithAttribution,
                 OrderExecuted, OrderExecutedWithPrice, OrderCancel, OrderDelete, OrderReplace,
                 Trade, CrossTrade, BrokenTrade>;

} // namespace itchlab
