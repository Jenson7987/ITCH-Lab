#include "itchlab/book/order_book.hpp"

#include "itchlab/core/errors.hpp"
#include "itchlab/core/sha256.hpp"
#include "itchlab/core/types.hpp"

#include <catch2/catch_test_macros.hpp>

#include <cstdint>
#include <limits>
#include <utility>

namespace {

constexpr itchlab::StockLocate kStockLocate = 1;

itchlab::BookAdd add(const itchlab::MessageIndex message_index,
                     const itchlab::OrderReference reference, const itchlab::Side side,
                     const itchlab::Shares shares, const itchlab::Price4 price4) {
  return itchlab::BookAdd{message_index, kStockLocate, reference, side, shares, price4};
}

itchlab::BookDelete delete_order(const itchlab::MessageIndex message_index,
                                 const itchlab::OrderReference reference) {
  return itchlab::BookDelete{message_index, kStockLocate, reference};
}

void require_valid(const itchlab::BookApplyResult& result) {
  REQUIRE(result.valid());
  REQUIRE(result.delta.has_value());
  REQUIRE_FALSE(result.error.has_value());
}

} // namespace

TEST_CASE("TASK-006 add and delete maintain FIFO, totals, best prices and top-N",
          "[TASK-006][book][FR-004][FR-005]") {
  itchlab::OrderBook book{kStockLocate};

  require_valid(book.apply(add(10, 10, itchlab::Side::buy, 5, 100)));
  require_valid(book.apply(add(11, 11, itchlab::Side::buy, 7, 100)));
  require_valid(book.apply(add(12, 12, itchlab::Side::buy, 3, 101)));
  require_valid(book.apply(add(13, 13, itchlab::Side::buy, 9, 99)));
  require_valid(book.apply(add(14, 14, itchlab::Side::sell, 6, 103)));
  require_valid(book.apply(add(15, 15, itchlab::Side::sell, 4, 102)));

  const auto top = book.top_levels(3);
  REQUIRE(top.bids.size() == 3);
  REQUIRE(top.asks.size() == 3);
  REQUIRE(top.bids[0] == itchlab::AggregatedLevel{101, 3});
  REQUIRE(top.bids[1] == itchlab::AggregatedLevel{100, 12});
  REQUIRE(top.bids[2] == itchlab::AggregatedLevel{99, 9});
  REQUIRE(top.asks[0] == itchlab::AggregatedLevel{102, 4});
  REQUIRE(top.asks[1] == itchlab::AggregatedLevel{103, 6});
  REQUIRE_FALSE(top.asks[2].has_value());

  const auto bid_level = book.level(itchlab::Side::buy, 100);
  REQUIRE(bid_level.has_value());
  REQUIRE(bid_level->total_quantity == 12);
  REQUIRE(bid_level->fifo_orders == std::vector<itchlab::OrderView>{{10, 5, 10}, {11, 7, 11}});

  const auto delete_head = book.apply(delete_order(20, 10));
  require_valid(delete_head);
  REQUIRE(delete_head.delta == itchlab::BookDelta{itchlab::BookMutationKind::delete_order, 20,
                                                  kStockLocate, 10, itchlab::Side::buy, 100, 5, 0});
  REQUIRE(book.level(itchlab::Side::buy, 100)->fifo_orders ==
          std::vector<itchlab::OrderView>{{11, 7, 11}});

  require_valid(book.apply(delete_order(21, 11)));
  REQUIRE_FALSE(book.level(itchlab::Side::buy, 100).has_value());
  REQUIRE(book.check_invariants().valid());
}

TEST_CASE("UT-BOOK-003 rejected reference mutations are atomic",
          "[TASK-006][UT-BOOK-003][book][atomic]") {
  itchlab::OrderBook book{kStockLocate};
  require_valid(book.apply(add(3, 9'001, itchlab::Side::buy, 100, 1'000'000)));

  const auto original_digest = itchlab::content_hash_to_hex(book.digest());
  const auto original_top = book.top_levels(2);
  const auto original_level = book.level(itchlab::Side::buy, 1'000'000);

  SECTION("duplicate live reference") {
    const auto result = book.apply(add(4, 9'001, itchlab::Side::sell, 50, 1'001'000));
    REQUIRE_FALSE(result.valid());
    REQUIRE(result.error->code == itchlab::ErrorCode::order_reference);
    REQUIRE(result.error->order_reference == 9'001);
  }

  SECTION("missing delete reference") {
    const auto result = book.apply(delete_order(4, 99'001));
    REQUIRE_FALSE(result.valid());
    REQUIRE(result.error->code == itchlab::ErrorCode::order_reference);
    REQUIRE(result.error->order_reference == 99'001);
  }

  REQUIRE(itchlab::content_hash_to_hex(book.digest()) == original_digest);
  REQUIRE(book.top_levels(2) == original_top);
  REQUIRE(book.level(itchlab::Side::buy, 1'000'000) == original_level);
  REQUIRE(book.check_invariants().valid());
}

TEST_CASE("TASK-006 invalid add domains fail without mutation",
          "[TASK-006][book][atomic][boundary][security]") {
  itchlab::OrderBook book{kStockLocate};
  const auto empty_digest = itchlab::content_hash_to_hex(book.digest());

  SECTION("zero quantity") {
    const auto result = book.apply(add(1, 1, itchlab::Side::buy, 0, 100));
    REQUIRE(result.error->code == itchlab::ErrorCode::quantity);
  }

  SECTION("quantity exceeds the uint32 ITCH source domain") {
    constexpr auto oversized =
        static_cast<itchlab::Shares>(std::numeric_limits<std::uint32_t>::max()) + 1;
    const auto result = book.apply(add(1, 1, itchlab::Side::buy, oversized, 100));
    REQUIRE(result.error->code == itchlab::ErrorCode::quantity);
  }

  SECTION("side is not applicable") {
    const auto result = book.apply(add(1, 1, itchlab::Side::not_applicable, 1, 100));
    REQUIRE(result.error->code == itchlab::ErrorCode::invariant);
  }

  SECTION("stock locate differs from the owning book") {
    const auto result = book.apply(itchlab::BookAdd{1, 2, 1, itchlab::Side::buy, 1, 100});
    REQUIRE(result.error->code == itchlab::ErrorCode::invariant);
  }

  REQUIRE(book.order_count() == 0);
  REQUIRE(itchlab::content_hash_to_hex(book.digest()) == empty_digest);
  REQUIRE(book.check_invariants().valid());
}

TEST_CASE("TASK-006 owner and source-priority errors are atomic",
          "[TASK-006][book][atomic][priority]") {
  itchlab::OrderBook book{kStockLocate};
  require_valid(book.apply(add(10, 1, itchlab::Side::buy, 5, 100)));
  const auto original_digest = itchlab::content_hash_to_hex(book.digest());

  SECTION("same-level add priority does not follow the FIFO tail") {
    const auto result = book.apply(add(10, 2, itchlab::Side::buy, 7, 100));
    REQUIRE(result.error->code == itchlab::ErrorCode::invariant);
  }

  SECTION("delete source position does not follow its add") {
    const auto result = book.apply(delete_order(10, 1));
    REQUIRE(result.error->code == itchlab::ErrorCode::invariant);
  }

  SECTION("delete stock locate differs from the owning book") {
    const auto result = book.apply(itchlab::BookDelete{11, 2, 1});
    REQUIRE(result.error->code == itchlab::ErrorCode::invariant);
  }

  REQUIRE_FALSE(book.level(itchlab::Side::not_applicable, 100).has_value());
  REQUIRE(book.top_levels(0) == itchlab::TopLevels{});
  REQUIRE(itchlab::content_hash_to_hex(book.digest()) == original_digest);
  REQUIRE(book.check_invariants().valid());
}

TEST_CASE("TASK-006 accepts maximum source quantity and explicit empty depth slots",
          "[TASK-006][book][boundary]") {
  itchlab::OrderBook book{kStockLocate};
  constexpr auto maximum = std::numeric_limits<std::uint32_t>::max();
  require_valid(book.apply(
      add(1, 1, itchlab::Side::sell, maximum, std::numeric_limits<itchlab::Price4>::max())));

  const auto top = book.top_levels(2);
  REQUIRE_FALSE(top.bids[0].has_value());
  REQUIRE_FALSE(top.bids[1].has_value());
  REQUIRE(top.asks[0] ==
          itchlab::AggregatedLevel{std::numeric_limits<itchlab::Price4>::max(), maximum});
  REQUIRE_FALSE(top.asks[1].has_value());
  REQUIRE(book.top_levels(0) == itchlab::TopLevels{});
  REQUIRE(book.check_invariants().valid());
}

TEST_CASE("TASK-006 digest ignores insertion and unordered-map bucket history",
          "[TASK-006][book][digest][NFR-001]") {
  itchlab::OrderBook first{kStockLocate};
  itchlab::OrderBook second{kStockLocate};

  require_valid(first.apply(add(10, 1, itchlab::Side::buy, 10, 100)));
  require_valid(first.apply(add(20, 2, itchlab::Side::buy, 20, 100)));
  require_valid(first.apply(add(15, 3, itchlab::Side::sell, 30, 102)));

  require_valid(second.apply(add(15, 3, itchlab::Side::sell, 30, 102)));
  require_valid(second.apply(add(10, 1, itchlab::Side::buy, 10, 100)));
  require_valid(second.apply(add(20, 2, itchlab::Side::buy, 20, 100)));

  REQUIRE(first.top_levels(3) == second.top_levels(3));
  REQUIRE(first.level(itchlab::Side::buy, 100) == second.level(itchlab::Side::buy, 100));
  REQUIRE(itchlab::content_hash_to_hex(first.digest()) ==
          itchlab::content_hash_to_hex(second.digest()));
  REQUIRE(itchlab::content_hash_to_hex(first.digest()) ==
          "42146618e842955375e772183aa3431b3c5ef796f552657f46105379a07ccb9d");

  const auto digest_before_move = itchlab::content_hash_to_hex(first.digest());
  itchlab::OrderBook moved{std::move(first)};
  REQUIRE(itchlab::content_hash_to_hex(moved.digest()) == digest_before_move);
  REQUIRE(moved.check_invariants().valid());

  itchlab::OrderBook assigned{kStockLocate};
  assigned = std::move(moved);
  REQUIRE(itchlab::content_hash_to_hex(assigned.digest()) == digest_before_move);
  REQUIRE(assigned.check_invariants().valid());
}

TEST_CASE("TASK-006 deleting every order restores the canonical empty state",
          "[TASK-006][book][digest]") {
  itchlab::OrderBook book{kStockLocate};
  const auto empty_digest = itchlab::content_hash_to_hex(book.digest());
  REQUIRE(empty_digest == "47213ce72b18bbb9fb839f064fb00c71d810d21c19e1fe74a9ed61162c0d2a6c");

  require_valid(book.apply(add(1, 1, itchlab::Side::buy, 10, 100)));
  require_valid(book.apply(add(2, 2, itchlab::Side::sell, 20, 101)));
  require_valid(book.apply(delete_order(3, 1)));
  require_valid(book.apply(delete_order(4, 2)));

  REQUIRE(book.order_count() == 0);
  REQUIRE(itchlab::content_hash_to_hex(book.digest()) == empty_digest);
  REQUIRE(book.check_invariants().valid());
}
