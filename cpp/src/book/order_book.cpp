#include "itchlab/book/order_book.hpp"

#include "itchlab/core/sha256.hpp"
#include "itchlab/core/types.hpp"

#include <cstddef>
#include <cstdint>
#include <iterator>
#include <memory>
#include <span>
#include <string_view>
#include <unordered_set>
#include <utility>
#include <variant>
#include <vector>

namespace itchlab {
namespace {

template <typename... Functions> struct Overloaded : Functions... {
  using Functions::operator()...;
};

[[nodiscard]] BookApplyResult fail(const ErrorCode code,
                                   const std::optional<OrderReference> order_reference,
                                   std::string message) {
  return BookApplyResult::failure(BookError{code, order_reference, std::move(message)});
}

void append_u8(std::vector<std::byte>& bytes, const std::uint8_t value) {
  bytes.push_back(static_cast<std::byte>(value));
}

void append_u16(std::vector<std::byte>& bytes, const std::uint16_t value) {
  append_u8(bytes, static_cast<std::uint8_t>(value >> 8U));
  append_u8(bytes, static_cast<std::uint8_t>(value));
}

void append_u32(std::vector<std::byte>& bytes, const std::uint32_t value) {
  for (int shift = 24; shift >= 0; shift -= 8) {
    append_u8(bytes, static_cast<std::uint8_t>(value >> static_cast<unsigned>(shift)));
  }
}

void append_u64(std::vector<std::byte>& bytes, const std::uint64_t value) {
  for (int shift = 56; shift >= 0; shift -= 8) {
    append_u8(bytes, static_cast<std::uint8_t>(value >> static_cast<unsigned>(shift)));
  }
}

void add_violation(InvariantReport& report, const std::optional<OrderReference> order_reference,
                   const std::optional<Price4> price4, std::string message) {
  report.violations.push_back(
      InvariantViolation{ErrorCode::invariant, order_reference, price4, std::move(message)});
}

} // namespace

BookApplyResult BookApplyResult::success(BookDelta book_delta) noexcept {
  return BookApplyResult{std::move(book_delta), std::nullopt};
}

BookApplyResult BookApplyResult::failure(BookError book_error) {
  return BookApplyResult{std::nullopt, std::move(book_error)};
}

OrderBook::OrderBook(const StockLocate stock_locate)
    : stock_locate_{stock_locate},
      pool_{std::make_unique<std::pmr::unsynchronized_pool_resource>()}, bids_{pool_.get()},
      asks_{pool_.get()}, orders_{pool_.get()} {}

OrderBook::OrderBook(OrderBook&&) noexcept = default;

OrderBook& OrderBook::operator=(OrderBook&& other) noexcept {
  if (this != &other) {
    std::destroy_at(this);
    std::construct_at(this, std::move(other));
  }
  return *this;
}

BookApplyResult OrderBook::apply(const BookMessage& message) {
  return std::visit(
      Overloaded{[this](const BookAdd& add) { return apply_add(add); },
                 [this](const BookExecute& execute) { return apply_execute(execute); },
                 [this](const BookCancel& cancel) { return apply_cancel(cancel); },
                 [this](const BookDelete& delete_order) { return apply_delete(delete_order); },
                 [this](const BookReplace& replace) { return apply_replace(replace); }},
      message);
}

BookApplyResult OrderBook::apply_add(const BookAdd& add) {
  if (add.stock_locate != stock_locate_) {
    return fail(ErrorCode::invariant, add.order_reference,
                "Add Order stock locate does not match the owning book.");
  }
  if (add.side != Side::buy && add.side != Side::sell) {
    return fail(ErrorCode::invariant, add.order_reference, "Add Order side must be buy or sell.");
  }
  const auto remaining = checked_integral_cast<std::uint32_t>(add.shares);
  if (!remaining || *remaining == 0) {
    return fail(ErrorCode::quantity, add.order_reference,
                "Add Order quantity must fit the positive ITCH source quantity domain.");
  }
  if (orders_.contains(add.order_reference)) {
    return fail(ErrorCode::order_reference, add.order_reference,
                "Add Order reference is already live.");
  }

  const auto add_to_side = [this, &add, remaining](auto& levels) -> BookApplyResult {
    auto level = levels.find(add.price4);
    bool inserted_level = false;
    Shares updated_total = static_cast<Shares>(*remaining);

    if (level != levels.end()) {
      const auto total = checked_add(level->second.total_quantity, static_cast<Shares>(*remaining));
      if (!total) {
        return fail(ErrorCode::quantity, add.order_reference,
                    "Add Order would overflow the price-level aggregate quantity.");
      }
      updated_total = *total;

      if (!level->second.fifo.empty()) {
        const auto previous = orders_.find(level->second.fifo.back());
        if (previous == orders_.end() || previous->second.priority_sequence >= add.message_index) {
          return fail(ErrorCode::invariant, add.order_reference,
                      "Add Order source priority is not later than the FIFO tail.");
        }
      }

      level->second.fifo.push_back(add.order_reference);
    } else {
      StoredPriceLevel staged_level{pool_.get()};
      staged_level.total_quantity = updated_total;
      staged_level.fifo.push_back(add.order_reference);
      auto insertion = levels.emplace(add.price4, std::move(staged_level));
      if (!insertion.second) {
        return fail(ErrorCode::invariant, add.order_reference,
                    "Price level could not be created for Add Order.");
      }
      level = insertion.first;
      inserted_level = true;
    }

    const auto queue_position = std::prev(level->second.fifo.end());
    try {
      const auto insertion = orders_.emplace(
          add.order_reference, StoredOrder{add.stock_locate, add.side, add.price4, *remaining,
                                           add.message_index, add.attribution, queue_position});
      if (!insertion.second) {
        if (inserted_level) {
          levels.erase(level);
        } else {
          level->second.fifo.pop_back();
        }
        return fail(ErrorCode::invariant, add.order_reference,
                    "Add Order reference insertion conflicted after validation.");
      }
    } catch (...) {
      if (inserted_level) {
        levels.erase(level);
      } else {
        level->second.fifo.pop_back();
      }
      throw;
    }

    if (!inserted_level) {
      level->second.total_quantity = updated_total;
    }
    return BookApplyResult::success(BookDelta{BookMutationKind::add, add.message_index,
                                              add.stock_locate, add.order_reference, add.side,
                                              add.price4, 0, add.shares, std::nullopt});
  };

  if (add.side == Side::buy) {
    return add_to_side(bids_);
  }
  return add_to_side(asks_);
}

BookApplyResult OrderBook::apply_execute(const BookExecute& execute) {
  return apply_reduction(execute.message_index, execute.stock_locate, execute.order_reference,
                         execute.executed_shares, BookMutationKind::execute);
}

BookApplyResult OrderBook::apply_cancel(const BookCancel& cancel) {
  return apply_reduction(cancel.message_index, cancel.stock_locate, cancel.order_reference,
                         cancel.cancelled_shares, BookMutationKind::cancel);
}

BookApplyResult OrderBook::apply_reduction(const MessageIndex message_index,
                                           const StockLocate stock_locate,
                                           const OrderReference order_reference,
                                           const Shares shares, const BookMutationKind kind) {
  const auto* mutation_name =
      kind == BookMutationKind::execute ? "Order execution" : "Order cancel";
  if (stock_locate != stock_locate_) {
    return fail(ErrorCode::invariant, order_reference,
                std::string{mutation_name} + " stock locate does not match the owning book.");
  }

  const auto order = orders_.find(order_reference);
  if (order == orders_.end()) {
    return fail(ErrorCode::order_reference, order_reference,
                std::string{mutation_name} + " reference is not live.");
  }
  if (message_index <= order->second.priority_sequence) {
    return fail(ErrorCode::invariant, order_reference,
                std::string{mutation_name} + " source position does not follow the live order.");
  }

  const auto decrement = checked_integral_cast<std::uint32_t>(shares);
  if (!decrement || *decrement == 0) {
    return fail(ErrorCode::quantity, order_reference,
                std::string{mutation_name} +
                    " quantity must fit the positive ITCH source quantity domain.");
  }
  if (*decrement > order->second.remaining) {
    return fail(ErrorCode::quantity, order_reference,
                std::string{mutation_name} + " quantity exceeds the live order remainder.");
  }

  const auto reduce_on_side = [this, message_index, stock_locate, order_reference, decrement, kind,
                               &order, mutation_name](auto& levels) -> BookApplyResult {
    auto level = levels.find(order->second.price4);
    if (level == levels.end() || level->second.fifo.empty() ||
        order->second.level_iterator == level->second.fifo.end() ||
        *order->second.level_iterator != order_reference) {
      return fail(ErrorCode::invariant, order_reference,
                  std::string{mutation_name} + " found inconsistent level-3 ownership.");
    }

    const auto previous_remaining = static_cast<Shares>(order->second.remaining);
    const auto next_remaining = static_cast<Shares>(order->second.remaining - *decrement);
    const auto updated_total =
        checked_subtract(level->second.total_quantity, static_cast<Shares>(*decrement));
    if (!updated_total) {
      return fail(ErrorCode::invariant, order_reference,
                  std::string{mutation_name} +
                      " found an aggregate quantity below the requested decrement.");
    }
    const bool removes_order = next_remaining == 0;
    if (removes_order && ((level->second.fifo.size() == 1) != (*updated_total == 0))) {
      return fail(ErrorCode::invariant, order_reference,
                  std::string{mutation_name} + " would leave an inconsistent empty price level.");
    }
    if (!removes_order && *updated_total == 0) {
      return fail(ErrorCode::invariant, order_reference,
                  std::string{mutation_name} +
                      " would leave a live order at a zero-quantity price level.");
    }

    const BookDelta delta{kind,
                          message_index,
                          stock_locate,
                          order_reference,
                          order->second.side,
                          order->second.price4,
                          previous_remaining,
                          next_remaining,
                          std::nullopt};

    if (!removes_order) {
      order->second.remaining = static_cast<std::uint32_t>(next_remaining);
      level->second.total_quantity = *updated_total;
      return BookApplyResult::success(delta);
    }

    level->second.fifo.erase(order->second.level_iterator);
    orders_.erase(order);
    if (level->second.fifo.empty()) {
      levels.erase(level);
    } else {
      level->second.total_quantity = *updated_total;
    }
    return BookApplyResult::success(delta);
  };

  if (order->second.side == Side::buy) {
    return reduce_on_side(bids_);
  }
  if (order->second.side == Side::sell) {
    return reduce_on_side(asks_);
  }
  return fail(ErrorCode::invariant, order_reference,
              std::string{mutation_name} + " found an invalid live-order side.");
}

BookApplyResult OrderBook::apply_delete(const BookDelete& delete_order) {
  if (delete_order.stock_locate != stock_locate_) {
    return fail(ErrorCode::invariant, delete_order.order_reference,
                "Order Delete stock locate does not match the owning book.");
  }

  const auto order = orders_.find(delete_order.order_reference);
  if (order == orders_.end()) {
    return fail(ErrorCode::order_reference, delete_order.order_reference,
                "Order Delete reference is not live.");
  }
  if (delete_order.message_index <= order->second.priority_sequence) {
    return fail(ErrorCode::invariant, delete_order.order_reference,
                "Order Delete source position does not follow the Add Order.");
  }

  const auto erase_from_side = [this, &delete_order, &order](auto& levels) -> BookApplyResult {
    auto level = levels.find(order->second.price4);
    if (level == levels.end() || level->second.fifo.empty() ||
        order->second.level_iterator == level->second.fifo.end() ||
        *order->second.level_iterator != delete_order.order_reference) {
      return fail(ErrorCode::invariant, delete_order.order_reference,
                  "Order Delete found inconsistent level-3 ownership.");
    }

    const auto previous_remaining = static_cast<Shares>(order->second.remaining);
    const auto updated_total = checked_subtract(level->second.total_quantity, previous_remaining);
    if (!updated_total) {
      return fail(ErrorCode::invariant, delete_order.order_reference,
                  "Order Delete found an aggregate quantity below the live order remainder.");
    }
    if ((level->second.fifo.size() == 1) != (*updated_total == 0)) {
      return fail(ErrorCode::invariant, delete_order.order_reference,
                  "Order Delete would leave an inconsistent empty price level.");
    }

    const BookDelta delta{BookMutationKind::delete_order,
                          delete_order.message_index,
                          delete_order.stock_locate,
                          delete_order.order_reference,
                          order->second.side,
                          order->second.price4,
                          previous_remaining,
                          0,
                          std::nullopt};

    level->second.fifo.erase(order->second.level_iterator);
    orders_.erase(order);
    if (level->second.fifo.empty()) {
      levels.erase(level);
    } else {
      level->second.total_quantity = *updated_total;
    }
    return BookApplyResult::success(delta);
  };

  if (order->second.side == Side::buy) {
    return erase_from_side(bids_);
  }
  if (order->second.side == Side::sell) {
    return erase_from_side(asks_);
  }
  return fail(ErrorCode::invariant, delete_order.order_reference,
              "Order Delete found an invalid live-order side.");
}

BookApplyResult OrderBook::apply_replace(const BookReplace& replace) {
  if (replace.stock_locate != stock_locate_) {
    return fail(ErrorCode::invariant, replace.original_order_reference,
                "Order Replace stock locate does not match the owning book.");
  }
  if (replace.original_order_reference == replace.new_order_reference) {
    return fail(ErrorCode::order_reference, replace.new_order_reference,
                "Order Replace requires a distinct new reference.");
  }

  const auto original = orders_.find(replace.original_order_reference);
  if (original == orders_.end()) {
    return fail(ErrorCode::order_reference, replace.original_order_reference,
                "Order Replace original reference is not live.");
  }
  if (orders_.contains(replace.new_order_reference)) {
    return fail(ErrorCode::order_reference, replace.new_order_reference,
                "Order Replace new reference is already live.");
  }
  if (replace.message_index <= original->second.priority_sequence) {
    return fail(ErrorCode::invariant, replace.original_order_reference,
                "Order Replace source position does not follow the live order.");
  }

  const auto replacement_remaining = checked_integral_cast<std::uint32_t>(replace.shares);
  if (!replacement_remaining || *replacement_remaining == 0) {
    return fail(ErrorCode::quantity, replace.new_order_reference,
                "Order Replace quantity must fit the positive ITCH source quantity domain.");
  }

  const auto replace_on_side = [this, &replace, replacement_remaining,
                                &original](auto& levels) -> BookApplyResult {
    const auto original_side = original->second.side;
    const auto original_price4 = original->second.price4;
    const auto original_remaining = original->second.remaining;
    const auto original_attribution = original->second.attribution;
    const auto original_queue_position = original->second.level_iterator;

    auto source_level = levels.find(original_price4);
    if (source_level == levels.end() || source_level->second.fifo.empty() ||
        original_queue_position == source_level->second.fifo.end() ||
        *original_queue_position != replace.original_order_reference) {
      return fail(ErrorCode::invariant, replace.original_order_reference,
                  "Order Replace found inconsistent level-3 ownership.");
    }

    const auto source_total_without_original = checked_subtract(
        source_level->second.total_quantity, static_cast<Shares>(original_remaining));
    if (!source_total_without_original) {
      return fail(ErrorCode::invariant, replace.original_order_reference,
                  "Order Replace found an aggregate below the original order remainder.");
    }
    if ((source_level->second.fifo.size() == 1) != (*source_total_without_original == 0)) {
      return fail(ErrorCode::invariant, replace.original_order_reference,
                  "Order Replace found inconsistent source-level quantity state.");
    }

    auto target_level = levels.find(replace.price4);
    const bool same_level = target_level == source_level;
    Shares updated_target_total{};
    if (same_level) {
      const auto total =
          checked_add(*source_total_without_original, static_cast<Shares>(*replacement_remaining));
      if (!total) {
        return fail(ErrorCode::quantity, replace.new_order_reference,
                    "Order Replace would overflow the price-level aggregate quantity.");
      }
      updated_target_total = *total;
    } else if (target_level == levels.end()) {
      updated_target_total = static_cast<Shares>(*replacement_remaining);
    } else {
      const auto total = checked_add(target_level->second.total_quantity,
                                     static_cast<Shares>(*replacement_remaining));
      if (!total) {
        return fail(ErrorCode::quantity, replace.new_order_reference,
                    "Order Replace would overflow the price-level aggregate quantity.");
      }
      updated_target_total = *total;
    }

    if (target_level != levels.end() && !target_level->second.fifo.empty()) {
      const auto tail_reference = target_level->second.fifo.back();
      if (tail_reference != replace.original_order_reference) {
        const auto tail = orders_.find(tail_reference);
        if (tail == orders_.end() || tail->second.priority_sequence >= replace.message_index) {
          return fail(ErrorCode::invariant, replace.new_order_reference,
                      "Order Replace source priority is not later than the target FIFO tail.");
        }
      }
    }

    bool inserted_target_level = false;
    if (target_level == levels.end()) {
      auto insertion = levels.emplace(replace.price4, StoredPriceLevel{pool_.get()});
      if (!insertion.second) {
        return fail(ErrorCode::invariant, replace.new_order_reference,
                    "Target price level could not be created for Order Replace.");
      }
      target_level = insertion.first;
      inserted_target_level = true;
    }

    try {
      target_level->second.fifo.push_back(replace.new_order_reference);
    } catch (...) {
      if (inserted_target_level) {
        levels.erase(target_level);
      }
      throw;
    }
    const auto replacement_queue_position = std::prev(target_level->second.fifo.end());
    try {
      const auto insertion = orders_.emplace(
          replace.new_order_reference,
          StoredOrder{replace.stock_locate, original_side, replace.price4, *replacement_remaining,
                      replace.message_index, original_attribution, replacement_queue_position});
      if (!insertion.second) {
        target_level->second.fifo.pop_back();
        if (inserted_target_level) {
          levels.erase(target_level);
        }
        return fail(ErrorCode::invariant, replace.new_order_reference,
                    "Order Replace new-reference insertion conflicted after validation.");
      }
    } catch (...) {
      target_level->second.fifo.pop_back();
      if (inserted_target_level) {
        levels.erase(target_level);
      }
      throw;
    }

    source_level->second.fifo.erase(original_queue_position);
    orders_.erase(replace.original_order_reference);
    if (same_level) {
      source_level->second.total_quantity = updated_target_total;
    } else {
      target_level->second.total_quantity = updated_target_total;
      if (source_level->second.fifo.empty()) {
        levels.erase(source_level);
      } else {
        source_level->second.total_quantity = *source_total_without_original;
      }
    }

    return BookApplyResult::success(
        BookDelta{BookMutationKind::replace, replace.message_index, replace.stock_locate,
                  replace.original_order_reference, original_side, replace.price4,
                  static_cast<Shares>(original_remaining),
                  static_cast<Shares>(*replacement_remaining), replace.new_order_reference});
  };

  if (original->second.side == Side::buy) {
    return replace_on_side(bids_);
  }
  if (original->second.side == Side::sell) {
    return replace_on_side(asks_);
  }
  return fail(ErrorCode::invariant, replace.original_order_reference,
              "Order Replace found an invalid live-order side.");
}

TopLevels OrderBook::top_levels(const std::uint16_t depth) const {
  TopLevels result;
  const auto requested = static_cast<std::size_t>(depth);
  result.bids.resize(requested);
  result.asks.resize(requested);

  std::size_t index = 0;
  for (const auto& [price4, level] : bids_) {
    if (index == requested) {
      break;
    }
    result.bids[index] = AggregatedLevel{price4, level.total_quantity};
    ++index;
  }

  index = 0;
  for (const auto& [price4, level] : asks_) {
    if (index == requested) {
      break;
    }
    result.asks[index] = AggregatedLevel{price4, level.total_quantity};
    ++index;
  }
  return result;
}

std::optional<PriceLevelView> OrderBook::level(const Side side, const Price4 price4) const {
  const auto make_view = [this, price4](const auto& levels) -> std::optional<PriceLevelView> {
    const auto level = levels.find(price4);
    if (level == levels.end()) {
      return std::nullopt;
    }

    PriceLevelView view{price4, level->second.total_quantity, {}};
    view.fifo_orders.reserve(level->second.fifo.size());
    for (const auto reference : level->second.fifo) {
      const auto order = orders_.find(reference);
      if (order == orders_.end()) {
        return std::nullopt;
      }
      view.fifo_orders.push_back(OrderView{reference, static_cast<Shares>(order->second.remaining),
                                           order->second.priority_sequence,
                                           order->second.attribution});
    }
    return view;
  };

  if (side == Side::buy) {
    return make_view(bids_);
  }
  if (side == Side::sell) {
    return make_view(asks_);
  }
  return std::nullopt;
}

BookDigest OrderBook::digest() const {
  constexpr std::string_view domain{"itchlab-book-state-v1"};
  std::vector<std::byte> canonical;
  const auto domain_bytes = std::as_bytes(std::span{domain.data(), domain.size()});
  canonical.insert(canonical.end(), domain_bytes.begin(), domain_bytes.end());
  canonical.push_back(std::byte{0});
  append_u16(canonical, stock_locate_);

  const auto append_side = [this, &canonical](const auto& levels, const char side) {
    append_u8(canonical, static_cast<std::uint8_t>(side));
    append_u64(canonical, static_cast<std::uint64_t>(levels.size()));
    for (const auto& [price4, level] : levels) {
      append_u32(canonical, price4);
      append_u64(canonical, level.total_quantity);
      append_u64(canonical, static_cast<std::uint64_t>(level.fifo.size()));
      for (const auto reference : level.fifo) {
        const auto& order = orders_.at(reference);
        append_u64(canonical, reference);
        append_u64(canonical, static_cast<Shares>(order.remaining));
        append_u64(canonical, order.priority_sequence);
      }
    }
  };

  append_side(bids_, 'B');
  append_side(asks_, 'S');
  return sha256(canonical);
}

InvariantReport OrderBook::check_invariants() const {
  InvariantReport report{orders_.size(), bids_.size(), asks_.size(), {}};
  std::unordered_set<OrderReference> seen;
  seen.reserve(orders_.size());

  const auto check_side = [this, &report, &seen](const auto& levels, const Side expected_side) {
    for (const auto& [price4, level] : levels) {
      if (level.fifo.empty()) {
        add_violation(report, std::nullopt, price4, "Stored price level has an empty FIFO.");
      }

      Shares calculated_total = 0;
      std::optional<MessageIndex> previous_priority;
      for (auto queue_position = level.fifo.begin(); queue_position != level.fifo.end();
           ++queue_position) {
        const auto reference = *queue_position;
        if (!seen.insert(reference).second) {
          add_violation(report, reference, price4,
                        "Live order occurs more than once in price-level FIFOs.");
        }

        const auto order = orders_.find(reference);
        if (order == orders_.end()) {
          add_violation(report, reference, price4,
                        "Price-level FIFO references an unknown live order.");
          continue;
        }
        if (order->second.stock_locate != stock_locate_ || order->second.side != expected_side ||
            order->second.price4 != price4) {
          add_violation(report, reference, price4,
                        "Live order fields do not match the owning book and price level.");
        }
        if (order->second.remaining == 0) {
          add_violation(report, reference, price4, "Live order has zero remaining quantity.");
        }
        if (order->second.level_iterator != queue_position) {
          add_violation(report, reference, price4,
                        "Live order iterator does not point to its FIFO position.");
        }
        if (previous_priority && *previous_priority >= order->second.priority_sequence) {
          add_violation(report, reference, price4,
                        "Price-level FIFO priority is not strictly increasing.");
        }
        previous_priority = order->second.priority_sequence;

        const auto total =
            checked_add(calculated_total, static_cast<Shares>(order->second.remaining));
        if (!total) {
          add_violation(report, reference, price4,
                        "Price-level live-order quantity sum overflowed.");
        } else {
          calculated_total = *total;
        }
      }
      if (calculated_total != level.total_quantity) {
        add_violation(report, std::nullopt, price4,
                      "Stored price-level total differs from live-order quantities.");
      }
    }
  };

  check_side(bids_, Side::buy);
  check_side(asks_, Side::sell);
  if (seen.size() != orders_.size()) {
    add_violation(report, std::nullopt, std::nullopt,
                  "At least one live order is absent from all price-level FIFOs.");
  }
  return report;
}

} // namespace itchlab
