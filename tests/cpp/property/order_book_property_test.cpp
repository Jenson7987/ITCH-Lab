#include "itchlab/book/order_book.hpp"

#include "itchlab/core/sha256.hpp"
#include "itchlab/core/types.hpp"

#include <catch2/catch_test_macros.hpp>

#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <map>
#include <utility>
#include <vector>

namespace {

struct ModelLevel {
  itchlab::Shares total{};
  std::vector<itchlab::OrderReference> fifo;
};

using ModelKey = std::pair<itchlab::Side, itchlab::Price4>;

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
    const itchlab::BookAdd add{message_index, stock_locate, reference, side, shares, price4};
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
