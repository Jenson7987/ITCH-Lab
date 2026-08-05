#pragma once

#include "itchlab/core/types.hpp"

#include <array>
#include <optional>
#include <variant>

namespace itchlab {

using BookAttribution = std::array<char, 4>;

// Replay supplies the source message index separately from the decoded ITCH payload so FIFO
// priority remains tied to source order.
struct BookAdd {
  MessageIndex message_index{};
  StockLocate stock_locate{};
  OrderReference order_reference{};
  Side side{Side::not_applicable};
  Shares shares{};
  Price4 price4{};
  std::optional<BookAttribution> attribution;

  friend bool operator==(const BookAdd&, const BookAdd&) = default;
};

// E and C have the same visible-book effect. Execution price and print metadata remain on the
// decoded C message and must not replace the order's display price.
struct BookExecute {
  MessageIndex message_index{};
  StockLocate stock_locate{};
  OrderReference order_reference{};
  Shares executed_shares{};

  friend bool operator==(const BookExecute&, const BookExecute&) = default;
};

struct BookCancel {
  MessageIndex message_index{};
  StockLocate stock_locate{};
  OrderReference order_reference{};
  Shares cancelled_shares{};

  friend bool operator==(const BookCancel&, const BookCancel&) = default;
};

struct BookDelete {
  MessageIndex message_index{};
  StockLocate stock_locate{};
  OrderReference order_reference{};

  friend bool operator==(const BookDelete&, const BookDelete&) = default;
};

struct BookReplace {
  MessageIndex message_index{};
  StockLocate stock_locate{};
  OrderReference original_order_reference{};
  OrderReference new_order_reference{};
  Shares shares{};
  Price4 price4{};

  friend bool operator==(const BookReplace&, const BookReplace&) = default;
};

using BookMessage = std::variant<BookAdd, BookExecute, BookCancel, BookDelete, BookReplace>;

struct OrderView {
  OrderReference order_reference{};
  Shares remaining{};
  MessageIndex priority_sequence{};
  std::optional<BookAttribution> attribution;

  friend bool operator==(const OrderView&, const OrderView&) = default;
};

} // namespace itchlab
