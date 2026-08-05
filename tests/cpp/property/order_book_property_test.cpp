#include "itchlab/book/order_book.hpp"

#include "itchlab/core/sha256.hpp"
#include "itchlab/core/types.hpp"

#include <catch2/catch_test_macros.hpp>

#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <map>
#include <optional>
#include <utility>
#include <vector>

namespace {

struct ModelLevel {
  itchlab::Shares total{};
  std::vector<itchlab::OrderReference> fifo;
};

using ModelKey = std::pair<itchlab::Side, itchlab::Price4>;

struct ModelOrder {
  itchlab::Side side{itchlab::Side::not_applicable};
  itchlab::Price4 price4{};
  itchlab::Shares remaining{};
  itchlab::MessageIndex priority{};
};

} // namespace

TEST_CASE("TASK-006 generated add/delete lifecycles preserve book invariants",
          "[TASK-006][property][book][FR-004][FR-005]") {
  constexpr itchlab::StockLocate stock_locate = 7;
  constexpr std::size_t order_count = 48;
  itchlab::OrderBook book{stock_locate};
  const auto empty_digest = itchlab::content_hash_to_hex(book.digest());
  std::vector<itchlab::BookAdd> additions;
  additions.reserve(order_count);
  std::map<ModelKey, ModelLevel> model;

  for (std::size_t index = 0; index < order_count; ++index) {
    const auto side = (index % 2 == 0) ? itchlab::Side::buy : itchlab::Side::sell;
    const auto base_price = side == itchlab::Side::buy ? 1'000U : 2'000U;
    const auto price4 = static_cast<itchlab::Price4>(base_price + (index % 4));
    const auto shares = static_cast<itchlab::Shares>((index % 7) + 1);
    const auto reference = static_cast<itchlab::OrderReference>(10'000 + index);
    const auto message_index = static_cast<itchlab::MessageIndex>(index + 1);
    const itchlab::BookAdd add{message_index, stock_locate, reference,   side,
                               shares,        price4,       std::nullopt};
    additions.push_back(add);

    const auto result = book.apply(add);
    REQUIRE(result.valid());
    auto& expected = model[{side, price4}];
    expected.total += shares;
    expected.fifo.push_back(reference);
    REQUIRE(book.check_invariants().valid());

    if ((index % 8) == 0) {
      const auto digest_before_duplicate = itchlab::content_hash_to_hex(book.digest());
      const auto duplicate = book.apply(add);
      REQUIRE_FALSE(duplicate.valid());
      REQUIRE(itchlab::content_hash_to_hex(book.digest()) == digest_before_duplicate);
      REQUIRE(book.check_invariants().valid());
    }
  }

  for (const auto& [key, expected] : model) {
    const auto actual = book.level(key.first, key.second);
    REQUIRE(actual.has_value());
    REQUIRE(actual->total_quantity == expected.total);
    REQUIRE(actual->fifo_orders.size() == expected.fifo.size());
    for (std::size_t index = 0; index < expected.fifo.size(); ++index) {
      REQUIRE(actual->fifo_orders[index].order_reference == expected.fifo[index]);
    }
  }

  for (std::size_t step = 0; step < order_count; ++step) {
    const auto addition_index = (step * 17) % order_count;
    const auto& add = additions[addition_index];
    const auto message_index = static_cast<itchlab::MessageIndex>(order_count + step + 1);
    const auto result =
        book.apply(itchlab::BookDelete{message_index, stock_locate, add.order_reference});
    REQUIRE(result.valid());

    auto expected = model.find({add.side, add.price4});
    REQUIRE(expected != model.end());
    expected->second.total -= add.shares;
    const auto order =
        std::find(expected->second.fifo.begin(), expected->second.fifo.end(), add.order_reference);
    REQUIRE(order != expected->second.fifo.end());
    expected->second.fifo.erase(order);
    if (expected->second.fifo.empty()) {
      model.erase(expected);
      REQUIRE_FALSE(book.level(add.side, add.price4).has_value());
    } else {
      const auto actual = book.level(add.side, add.price4);
      REQUIRE(actual.has_value());
      REQUIRE(actual->total_quantity == expected->second.total);
    }
    REQUIRE(book.check_invariants().valid());
  }

  REQUIRE(model.empty());
  REQUIRE(book.order_count() == 0);
  REQUIRE(itchlab::content_hash_to_hex(book.digest()) == empty_digest);
  const auto top = book.top_levels(4);
  REQUIRE(std::ranges::all_of(top.bids, [](const auto& level) { return !level.has_value(); }));
  REQUIRE(std::ranges::all_of(top.asks, [](const auto& level) { return !level.has_value(); }));
}

TEST_CASE("TASK-010 generated full lifecycles match an independent FIFO aggregate model",
          "[TASK-010][property][book][FR-004][FR-005][atomic]") {
  constexpr itchlab::StockLocate stock_locate = 7;
  constexpr std::size_t initial_order_count = 36;
  itchlab::OrderBook book{stock_locate};
  const auto empty_digest = book.digest();
  std::map<ModelKey, ModelLevel> levels;
  std::map<itchlab::OrderReference, ModelOrder> orders;
  itchlab::MessageIndex message_index = 1;

  const auto assert_model = [&]() {
    REQUIRE(book.order_count() == orders.size());
    REQUIRE(book.check_invariants().valid());
    for (const auto& [key, expected_level] : levels) {
      const auto actual = book.level(key.first, key.second);
      REQUIRE(actual.has_value());
      REQUIRE(actual->total_quantity == expected_level.total);
      REQUIRE(actual->fifo_orders.size() == expected_level.fifo.size());
      for (std::size_t position = 0; position < expected_level.fifo.size(); ++position) {
        const auto reference = expected_level.fifo[position];
        const auto expected_order = orders.find(reference);
        REQUIRE(expected_order != orders.end());
        REQUIRE(actual->fifo_orders[position] ==
                itchlab::OrderView{reference, expected_order->second.remaining,
                                   expected_order->second.priority, std::nullopt});
      }
    }
  };

  const auto remove_from_model = [&](const itchlab::OrderReference reference) {
    const auto order = orders.find(reference);
    REQUIRE(order != orders.end());
    auto level = levels.find({order->second.side, order->second.price4});
    REQUIRE(level != levels.end());
    REQUIRE(level->second.total >= order->second.remaining);
    level->second.total -= order->second.remaining;
    const auto queue_position =
        std::find(level->second.fifo.begin(), level->second.fifo.end(), reference);
    REQUIRE(queue_position != level->second.fifo.end());
    level->second.fifo.erase(queue_position);
    if (level->second.fifo.empty()) {
      REQUIRE(level->second.total == 0);
      levels.erase(level);
    }
    orders.erase(order);
  };

  for (std::size_t index = 0; index < initial_order_count; ++index) {
    const auto side = index % 2 == 0 ? itchlab::Side::buy : itchlab::Side::sell;
    const auto price4 =
        static_cast<itchlab::Price4>((side == itchlab::Side::buy ? 1'000U : 2'000U) + (index % 3));
    const auto shares = static_cast<itchlab::Shares>(5 + (index % 7));
    const auto reference = static_cast<itchlab::OrderReference>(20'000 + index);
    const auto applied = book.apply(itchlab::BookAdd{message_index, stock_locate, reference, side,
                                                     shares, price4, std::nullopt});
    REQUIRE(applied.valid());
    levels[{side, price4}].total += shares;
    levels[{side, price4}].fifo.push_back(reference);
    orders.emplace(reference, ModelOrder{side, price4, shares, message_index});
    ++message_index;
    assert_model();
  }

  for (std::size_t index = 0; index < initial_order_count; ++index) {
    const auto reference = static_cast<itchlab::OrderReference>(20'000 + index);
    auto order = orders.find(reference);
    REQUIRE(order != orders.end());

    if (index % 4 == 0 || index % 4 == 1) {
      const auto digest_before_rejection = book.digest();
      const auto rejected = book.apply(itchlab::BookExecute{message_index, stock_locate, reference,
                                                            order->second.remaining + 1});
      REQUIRE_FALSE(rejected.valid());
      REQUIRE(rejected.error->code == itchlab::ErrorCode::quantity);
      REQUIRE(itchlab::content_hash_to_hex(book.digest()) ==
              itchlab::content_hash_to_hex(digest_before_rejection));

      constexpr itchlab::Shares decrement = 1;
      const auto reduced =
          index % 4 == 0
              ? book.apply(itchlab::BookExecute{message_index, stock_locate, reference, decrement})
              : book.apply(itchlab::BookCancel{message_index, stock_locate, reference, decrement});
      REQUIRE(reduced.valid());
      auto& level = levels.at({order->second.side, order->second.price4});
      level.total -= decrement;
      order->second.remaining -= decrement;
    } else if (index % 4 == 2) {
      const auto old_order = order->second;
      const auto new_reference = static_cast<itchlab::OrderReference>(30'000 + index);
      const auto new_price4 =
          static_cast<itchlab::Price4>(index % 8 == 2 ? old_order.price4 : old_order.price4 + 5);
      const auto new_shares = old_order.remaining + 2;
      const auto replaced = book.apply(itchlab::BookReplace{message_index, stock_locate, reference,
                                                            new_reference, new_shares, new_price4});
      REQUIRE(replaced.valid());

      remove_from_model(reference);
      levels[{old_order.side, new_price4}].total += new_shares;
      levels[{old_order.side, new_price4}].fifo.push_back(new_reference);
      orders.emplace(new_reference,
                     ModelOrder{old_order.side, new_price4, new_shares, message_index});
    }
    ++message_index;
    assert_model();
  }

  std::vector<itchlab::OrderReference> live_references;
  live_references.reserve(orders.size());
  for (const auto& [reference, order] : orders) {
    static_cast<void>(order);
    live_references.push_back(reference);
  }

  for (std::size_t index = 0; index < live_references.size(); ++index) {
    const auto reference = live_references[index];
    const auto order = orders.find(reference);
    REQUIRE(order != orders.end());
    itchlab::BookApplyResult removed;
    if (index % 3 == 0) {
      removed = book.apply(
          itchlab::BookExecute{message_index, stock_locate, reference, order->second.remaining});
    } else if (index % 3 == 1) {
      removed = book.apply(
          itchlab::BookCancel{message_index, stock_locate, reference, order->second.remaining});
    } else {
      removed = book.apply(itchlab::BookDelete{message_index, stock_locate, reference});
    }
    REQUIRE(removed.valid());
    remove_from_model(reference);
    ++message_index;
    assert_model();
  }

  REQUIRE(levels.empty());
  REQUIRE(orders.empty());
  REQUIRE(book.order_count() == 0);
  REQUIRE(itchlab::content_hash_to_hex(book.digest()) ==
          itchlab::content_hash_to_hex(empty_digest));
}
