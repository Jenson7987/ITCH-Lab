#include "itchlab/book/order_book.hpp"

#include "itchlab/core/errors.hpp"
#include "itchlab/core/sha256.hpp"
#include "itchlab/core/types.hpp"

#include <catch2/catch_test_macros.hpp>

#include <cstdint>
#include <limits>
#include <optional>
#include <utility>

namespace {

constexpr itchlab::StockLocate kStockLocate = 1;

itchlab::BookAdd add(const itchlab::MessageIndex message_index,
                     const itchlab::OrderReference reference, const itchlab::Side side,
                     const itchlab::Shares shares, const itchlab::Price4 price4) {
  return itchlab::BookAdd{message_index, kStockLocate, reference,   side,
                          shares,        price4,       std::nullopt};
}

itchlab::BookDelete delete_order(const itchlab::MessageIndex message_index,
                                 const itchlab::OrderReference reference) {
  return itchlab::BookDelete{message_index, kStockLocate, reference};
}

itchlab::BookExecute execute(const itchlab::MessageIndex message_index,
                             const itchlab::OrderReference reference,
                             const itchlab::Shares shares) {
  return itchlab::BookExecute{message_index, kStockLocate, reference, shares};
}

itchlab::BookCancel cancel(const itchlab::MessageIndex message_index,
                           const itchlab::OrderReference reference, const itchlab::Shares shares) {
  return itchlab::BookCancel{message_index, kStockLocate, reference, shares};
}

itchlab::BookReplace replace(const itchlab::MessageIndex message_index,
                             const itchlab::OrderReference original_reference,
                             const itchlab::OrderReference new_reference,
                             const itchlab::Shares shares, const itchlab::Price4 price4) {
  return itchlab::BookReplace{message_index, kStockLocate, original_reference,
                              new_reference, shares,       price4};
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
  REQUIRE(bid_level->fifo_orders ==
          std::vector<itchlab::OrderView>{{10, 5, 10, std::nullopt}, {11, 7, 11, std::nullopt}});

  const auto delete_head = book.apply(delete_order(20, 10));
  require_valid(delete_head);
  REQUIRE(delete_head.delta == itchlab::BookDelta{itchlab::BookMutationKind::delete_order, 20,
                                                  kStockLocate, 10, itchlab::Side::buy, 100, 5, 0,
                                                  std::nullopt});
  REQUIRE(book.level(itchlab::Side::buy, 100)->fifo_orders ==
          std::vector<itchlab::OrderView>{{11, 7, 11, std::nullopt}});

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
    const auto result =
        book.apply(itchlab::BookAdd{1, 2, 1, itchlab::Side::buy, 1, 100, std::nullopt});
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

TEST_CASE("UT-BOOK-001 execute cancel and delete maintain exact lifecycle quantities",
          "[TASK-010][UT-BOOK-001][book][FR-004][FR-005]") {
  itchlab::OrderBook book{kStockLocate};
  require_valid(book.apply(add(10, 100, itchlab::Side::buy, 100, 1'000'000)));
  require_valid(book.apply(add(11, 101, itchlab::Side::buy, 40, 1'000'000)));
  require_valid(book.apply(add(12, 200, itchlab::Side::sell, 25, 1'001'000)));

  const auto partial_execute = book.apply(execute(13, 100, 30));
  require_valid(partial_execute);
  REQUIRE(partial_execute.delta == itchlab::BookDelta{itchlab::BookMutationKind::execute, 13,
                                                      kStockLocate, 100, itchlab::Side::buy,
                                                      1'000'000, 100, 70, std::nullopt});
  REQUIRE(book.level(itchlab::Side::buy, 1'000'000)->total_quantity == 110);
  REQUIRE(book.check_invariants().valid());

  const auto partial_cancel = book.apply(cancel(14, 100, 20));
  require_valid(partial_cancel);
  REQUIRE(partial_cancel.delta == itchlab::BookDelta{itchlab::BookMutationKind::cancel, 14,
                                                     kStockLocate, 100, itchlab::Side::buy,
                                                     1'000'000, 70, 50, std::nullopt});
  REQUIRE(
      book.level(itchlab::Side::buy, 1'000'000)->fifo_orders ==
      std::vector<itchlab::OrderView>{{100, 50, 10, std::nullopt}, {101, 40, 11, std::nullopt}});
  REQUIRE(book.check_invariants().valid());

  const auto explicit_delete = book.apply(delete_order(15, 100));
  require_valid(explicit_delete);
  REQUIRE(explicit_delete.delta->previous_remaining == 50);
  REQUIRE(book.level(itchlab::Side::buy, 1'000'000)->total_quantity == 40);
  REQUIRE(book.check_invariants().valid());

  const auto full_execute = book.apply(execute(16, 101, 40));
  require_valid(full_execute);
  REQUIRE(full_execute.delta->remaining == 0);
  REQUIRE_FALSE(book.level(itchlab::Side::buy, 1'000'000).has_value());
  REQUIRE(book.check_invariants().valid());

  const auto full_cancel = book.apply(cancel(17, 200, 25));
  require_valid(full_cancel);
  REQUIRE(full_cancel.delta->remaining == 0);
  REQUIRE(book.order_count() == 0);
  REQUIRE_FALSE(book.level(itchlab::Side::sell, 1'001'000).has_value());
  REQUIRE(book.check_invariants().valid());
}

TEST_CASE("UT-BOOK-002 replacement resets FIFO priority and retains immutable fields",
          "[TASK-010][UT-BOOK-002][book][FR-004][FR-005][priority]") {
  constexpr itchlab::BookAttribution attribution{'T', 'E', 'S', 'T'};
  itchlab::OrderBook book{kStockLocate};
  require_valid(book.apply(
      itchlab::BookAdd{10, kStockLocate, 100, itchlab::Side::sell, 100, 1'001'000, attribution}));
  require_valid(book.apply(add(11, 101, itchlab::Side::sell, 50, 1'001'000)));

  const auto same_price = book.apply(replace(12, 100, 102, 80, 1'001'000));
  require_valid(same_price);
  REQUIRE(same_price.delta == itchlab::BookDelta{itchlab::BookMutationKind::replace, 12,
                                                 kStockLocate, 100, itchlab::Side::sell, 1'001'000,
                                                 100, 80, 102});
  REQUIRE(book.order_count() == 2);
  REQUIRE(book.level(itchlab::Side::sell, 1'001'000)->fifo_orders ==
          std::vector<itchlab::OrderView>{{101, 50, 11, std::nullopt}, {102, 80, 12, attribution}});
  REQUIRE(book.level(itchlab::Side::sell, 1'001'000)->total_quantity == 130);
  REQUIRE(book.check_invariants().valid());

  const auto different_price = book.apply(replace(13, 101, 103, 70, 1'002'000));
  require_valid(different_price);
  REQUIRE(book.level(itchlab::Side::sell, 1'001'000)->fifo_orders ==
          std::vector<itchlab::OrderView>{{102, 80, 12, attribution}});
  REQUIRE(book.level(itchlab::Side::sell, 1'002'000)->fifo_orders ==
          std::vector<itchlab::OrderView>{{103, 70, 13, std::nullopt}});
  const auto top = book.top_levels(2);
  REQUIRE(top.asks[0] == itchlab::AggregatedLevel{1'001'000, 80});
  REQUIRE(top.asks[1] == itchlab::AggregatedLevel{1'002'000, 70});
  REQUIRE(book.check_invariants().valid());
}

TEST_CASE("UT-BOOK-004 rejected quantity mutations are atomic",
          "[TASK-010][UT-BOOK-004][book][atomic][security]") {
  itchlab::OrderBook book{kStockLocate};
  require_valid(book.apply(add(10, 100, itchlab::Side::buy, 100, 1'000'000)));
  require_valid(book.apply(add(11, 101, itchlab::Side::buy, 50, 1'001'000)));
  const auto original_digest = book.digest();
  const auto original_top = book.top_levels(3);
  const auto original_level = book.level(itchlab::Side::buy, 1'000'000);

  SECTION("zero execute") {
    REQUIRE(book.apply(execute(12, 100, 0)).error->code == itchlab::ErrorCode::quantity);
  }
  SECTION("over execute") {
    REQUIRE(book.apply(execute(12, 100, 101)).error->code == itchlab::ErrorCode::quantity);
  }
  SECTION("oversized execute") {
    constexpr auto oversized =
        static_cast<itchlab::Shares>(std::numeric_limits<std::uint32_t>::max()) + 1;
    REQUIRE(book.apply(execute(12, 100, oversized)).error->code == itchlab::ErrorCode::quantity);
  }
  SECTION("zero cancel") {
    REQUIRE(book.apply(cancel(12, 100, 0)).error->code == itchlab::ErrorCode::quantity);
  }
  SECTION("over cancel") {
    REQUIRE(book.apply(cancel(12, 100, 101)).error->code == itchlab::ErrorCode::quantity);
  }
  SECTION("zero replacement") {
    REQUIRE(book.apply(replace(12, 100, 102, 0, 1'000'000)).error->code ==
            itchlab::ErrorCode::quantity);
  }
  SECTION("oversized replacement") {
    constexpr auto oversized =
        static_cast<itchlab::Shares>(std::numeric_limits<std::uint32_t>::max()) + 1;
    REQUIRE(book.apply(replace(12, 100, 102, oversized, 1'000'000)).error->code ==
            itchlab::ErrorCode::quantity);
  }

  REQUIRE(itchlab::content_hash_to_hex(book.digest()) ==
          itchlab::content_hash_to_hex(original_digest));
  REQUIRE(book.top_levels(3) == original_top);
  REQUIRE(book.level(itchlab::Side::buy, 1'000'000) == original_level);
  REQUIRE(book.check_invariants().valid());
}

TEST_CASE("TASK-010 rejected lifecycle references owners and priorities are atomic",
          "[TASK-010][book][atomic][priority][boundary]") {
  itchlab::OrderBook book{kStockLocate};
  require_valid(book.apply(add(10, 100, itchlab::Side::buy, 100, 1'000'000)));
  require_valid(book.apply(add(11, 101, itchlab::Side::buy, 50, 1'001'000)));
  const auto original_digest = book.digest();

  SECTION("missing execute") {
    REQUIRE(book.apply(execute(12, 999, 1)).error->code == itchlab::ErrorCode::order_reference);
  }
  SECTION("missing cancel") {
    REQUIRE(book.apply(cancel(12, 999, 1)).error->code == itchlab::ErrorCode::order_reference);
  }
  SECTION("missing replace original") {
    REQUIRE(book.apply(replace(12, 999, 102, 1, 1'000'000)).error->code ==
            itchlab::ErrorCode::order_reference);
  }
  SECTION("replace destination is live") {
    REQUIRE(book.apply(replace(12, 100, 101, 100, 1'000'000)).error->code ==
            itchlab::ErrorCode::order_reference);
  }
  SECTION("replacement references are identical") {
    REQUIRE(book.apply(replace(12, 100, 100, 100, 1'000'000)).error->code ==
            itchlab::ErrorCode::order_reference);
  }
  SECTION("execute does not follow add priority") {
    REQUIRE(book.apply(execute(10, 100, 1)).error->code == itchlab::ErrorCode::invariant);
  }
  SECTION("replacement does not follow target FIFO priority") {
    REQUIRE(book.apply(replace(11, 100, 102, 100, 1'001'000)).error->code ==
            itchlab::ErrorCode::invariant);
  }
  SECTION("cancel owner differs") {
    REQUIRE(book.apply(itchlab::BookCancel{12, 2, 100, 1}).error->code ==
            itchlab::ErrorCode::invariant);
  }
  SECTION("replace owner differs") {
    REQUIRE(book.apply(itchlab::BookReplace{12, 2, 100, 102, 100, 1'000'000}).error->code ==
            itchlab::ErrorCode::invariant);
  }

  REQUIRE(itchlab::content_hash_to_hex(book.digest()) ==
          itchlab::content_hash_to_hex(original_digest));
  REQUIRE(book.check_invariants().valid());
}

TEST_CASE("TASK-010 maximum source quantities survive replace and full reduction",
          "[TASK-010][book][boundary][SEC-002]") {
  constexpr auto maximum = std::numeric_limits<std::uint32_t>::max();
  itchlab::OrderBook book{kStockLocate};
  require_valid(book.apply(add(1, 1, itchlab::Side::buy, maximum, 100)));
  require_valid(book.apply(add(2, 2, itchlab::Side::buy, maximum, 100)));
  REQUIRE(book.level(itchlab::Side::buy, 100)->total_quantity ==
          static_cast<itchlab::Shares>(maximum) * 2);

  require_valid(book.apply(replace(3, 1, 3, maximum, 101)));
  require_valid(book.apply(execute(4, 2, maximum)));
  require_valid(book.apply(cancel(5, 3, maximum)));
  REQUIRE(book.order_count() == 0);
  REQUIRE(book.check_invariants().valid());
}
