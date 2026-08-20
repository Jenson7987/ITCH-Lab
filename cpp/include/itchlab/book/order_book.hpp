#pragma once

#include "itchlab/book/order.hpp"
#include "itchlab/book/price_level.hpp"
#include "itchlab/core/errors.hpp"
#include "itchlab/core/types.hpp"

#include <cstddef>
#include <cstdint>
#include <functional>
#include <list>
#include <map>
#include <memory>
#include <memory_resource>
#include <optional>
#include <string>
#include <unordered_map>
#include <vector>

namespace itchlab {

using BookDigest = ContentHash;

enum class BookMutationKind : std::uint8_t {
  add = 1,
  delete_order = 2,
  execute = 3,
  cancel = 4,
  replace = 5,
};

struct BookDelta {
  BookMutationKind kind{BookMutationKind::add};
  MessageIndex message_index{};
  StockLocate stock_locate{};
  OrderReference order_reference{};
  Side side{Side::not_applicable};
  Price4 price4{};
  Shares previous_remaining{};
  Shares remaining{};
  std::optional<OrderReference> new_order_reference;

  friend bool operator==(const BookDelta&, const BookDelta&) = default;
};

struct BookError {
  ErrorCode code{ErrorCode::invariant};
  std::optional<OrderReference> order_reference;
  std::string message;

  friend bool operator==(const BookError&, const BookError&) = default;
};

struct BookApplyResult {
  std::optional<BookDelta> delta;
  std::optional<BookError> error;

  [[nodiscard]] static BookApplyResult success(BookDelta book_delta) noexcept;
  [[nodiscard]] static BookApplyResult failure(BookError book_error);
  [[nodiscard]] bool valid() const noexcept { return delta.has_value() && !error.has_value(); }
};

struct InvariantViolation {
  ErrorCode code{ErrorCode::invariant};
  std::optional<OrderReference> order_reference;
  std::optional<Price4> price4;
  std::string message;

  friend bool operator==(const InvariantViolation&, const InvariantViolation&) = default;
};

struct InvariantReport {
  std::size_t order_count{};
  std::size_t bid_level_count{};
  std::size_t ask_level_count{};
  std::vector<InvariantViolation> violations;

  [[nodiscard]] bool valid() const noexcept { return violations.empty(); }
};

class OrderBook {
public:
  explicit OrderBook(StockLocate stock_locate);

  OrderBook(const OrderBook&) = delete;
  OrderBook& operator=(const OrderBook&) = delete;
  OrderBook(OrderBook&&) noexcept;
  OrderBook& operator=(OrderBook&&) noexcept;
  ~OrderBook() = default;

  // Expected domain errors are returned without changing the book.
  [[nodiscard]] BookApplyResult apply(const BookMessage& message);

  // Returns exactly depth explicitly valid/empty slots per side, best price first.
  [[nodiscard]] TopLevels top_levels(std::uint16_t depth) const;
  [[nodiscard]] std::optional<PriceLevelView> level(Side side, Price4 price4) const;

  // SHA-256 over the documented canonical logical state, independent of container layout.
  [[nodiscard]] BookDigest digest() const;
  [[nodiscard]] InvariantReport check_invariants() const;

  [[nodiscard]] StockLocate stock_locate() const noexcept { return stock_locate_; }
  [[nodiscard]] std::size_t order_count() const noexcept { return orders_.size(); }

private:
  using OrderQueue = std::pmr::list<OrderReference>;

  struct StoredPriceLevel {
    explicit StoredPriceLevel(std::pmr::memory_resource* resource) : fifo{resource} {}

    Shares total_quantity{};
    OrderQueue fifo;
  };

  struct StoredOrder {
    StockLocate stock_locate{};
    Side side{Side::not_applicable};
    Price4 price4{};
    std::uint32_t remaining{};
    MessageIndex priority_sequence{};
    std::optional<BookAttribution> attribution;
    OrderQueue::iterator level_iterator;
  };

  using BidLevels = std::pmr::map<Price4, StoredPriceLevel, std::greater<Price4>>;
  using AskLevels = std::pmr::map<Price4, StoredPriceLevel>;
  using Orders = std::pmr::unordered_map<OrderReference, StoredOrder>;

  [[nodiscard]] BookApplyResult apply_add(const BookAdd& add);
  [[nodiscard]] BookApplyResult apply_execute(const BookExecute& execute);
  [[nodiscard]] BookApplyResult apply_cancel(const BookCancel& cancel);
  [[nodiscard]] BookApplyResult apply_delete(const BookDelete& delete_order);
  [[nodiscard]] BookApplyResult apply_replace(const BookReplace& replace);
  [[nodiscard]] BookApplyResult apply_reduction(MessageIndex message_index,
                                                StockLocate stock_locate,
                                                OrderReference order_reference, Shares shares,
                                                BookMutationKind kind);

  StockLocate stock_locate_{};
  std::unique_ptr<std::pmr::unsynchronized_pool_resource> pool_;
  BidLevels bids_;
  AskLevels asks_;
  Orders orders_;
};

} // namespace itchlab
