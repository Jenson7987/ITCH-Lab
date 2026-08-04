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

struct AddOrder {
  MessageHeader header;
  OrderReference order_reference{};
  Side side{Side::not_applicable};
  Shares shares{};
  StockField stock{};
  Price4 price4{};

  friend bool operator==(const AddOrder&, const AddOrder&) = default;
};

struct OrderDelete {
  MessageHeader header;
  OrderReference order_reference{};

  friend bool operator==(const OrderDelete&, const OrderDelete&) = default;
};

// TASK-009 expands this closed variant with the remaining MVP message types.
using ItchMessage = std::variant<SystemEvent, StockDirectory, AddOrder, OrderDelete>;

} // namespace itchlab
