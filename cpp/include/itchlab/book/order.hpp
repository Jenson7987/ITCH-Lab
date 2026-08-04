#pragma once

#include "itchlab/core/types.hpp"

#include <variant>

namespace itchlab {

// Replay supplies the source message index separately from the decoded ITCH payload so FIFO
// priority remains tied to source order.
struct BookAdd {
  MessageIndex message_index{};
  StockLocate stock_locate{};
  OrderReference order_reference{};
  Side side{Side::not_applicable};
  Shares shares{};
  Price4 price4{};

  friend bool operator==(const BookAdd&, const BookAdd&) = default;
};

struct BookDelete {
  MessageIndex message_index{};
  StockLocate stock_locate{};
  OrderReference order_reference{};

  friend bool operator==(const BookDelete&, const BookDelete&) = default;
};

using BookMessage = std::variant<BookAdd, BookDelete>;

struct OrderView {
  OrderReference order_reference{};
  Shares remaining{};
  MessageIndex priority_sequence{};

  friend bool operator==(const OrderView&, const OrderView&) = default;
};

} // namespace itchlab
