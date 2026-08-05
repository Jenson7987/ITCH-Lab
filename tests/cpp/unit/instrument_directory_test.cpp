#include "itchlab/replay/instrument_directory.hpp"

#include <catch2/catch_test_macros.hpp>

#include <algorithm>
#include <string>
#include <string_view>
#include <vector>

namespace {

itchlab::StockField stock_field(const std::string_view symbol) {
  itchlab::StockField field{};
  field.fill(' ');
  std::copy(symbol.begin(), symbol.end(), field.begin());
  return field;
}

itchlab::StockDirectory directory(const itchlab::StockLocate locate, const std::string_view symbol,
                                  const std::uint32_t round_lot_size = 100,
                                  const char market_category = 'Q') {
  return itchlab::StockDirectory{itchlab::MessageHeader{locate, 1, 1'000},
                                 stock_field(symbol),
                                 market_category,
                                 'N',
                                 round_lot_size,
                                 'N',
                                 'C',
                                 {' ', ' '},
                                 'P',
                                 'N',
                                 'N',
                                 '1',
                                 'N',
                                 1,
                                 'N'};
}

} // namespace

TEST_CASE("TASK-011 directory assigns requested-order SymbolIds independently of source order",
          "[TASK-011][directory][unit]") {
  const std::vector<std::string> requested{"MSFT", "AAPL"};
  itchlab::InstrumentDirectory instruments{requested};

  const auto unselected = instruments.apply(directory(3, "AMZN"));
  REQUIRE(unselected.valid());
  REQUIRE_FALSE(unselected.resolved_instrument.has_value());
  REQUIRE(instruments.knows_locate(3));

  const auto aapl = instruments.apply(directory(1, "AAPL"));
  REQUIRE(aapl.valid());
  REQUIRE(aapl.resolved_instrument->symbol_id == 2);
  REQUIRE(aapl.resolved_instrument->stock_locate == 1);
  REQUIRE_FALSE(instruments.all_requested_resolved());
  REQUIRE(instruments.unresolved_symbols() == std::vector<std::string>{"MSFT"});

  const auto msft = instruments.apply(directory(2, "MSFT"));
  REQUIRE(msft.valid());
  REQUIRE(msft.resolved_instrument->symbol_id == 1);
  REQUIRE(instruments.all_requested_resolved());
  REQUIRE(instruments.unresolved_symbols().empty());
  REQUIRE(instruments.requests("AAPL"));
  REQUIRE_FALSE(instruments.requests("AMZN"));

  const auto selected = instruments.selected_instruments();
  REQUIRE(selected.size() == 2);
  REQUIRE(selected[0].symbol == "MSFT");
  REQUIRE(selected[0].stock_locate == 2);
  REQUIRE(selected[1].symbol == "AAPL");
  REQUIRE(selected[1].stock_locate == 1);
  REQUIRE(instruments.selected_by_locate(3) == nullptr);
  REQUIRE(instruments.selected_by_locate(1)->symbol == "AAPL");
}

TEST_CASE("TASK-011 directory accepts exact repeats and rejects contradictory records atomically",
          "[TASK-011][directory][unit][error]") {
  const std::vector<std::string> requested{"AAPL"};

  SECTION("exact repeat") {
    itchlab::InstrumentDirectory instruments{requested};
    REQUIRE(instruments.apply(directory(1, "AAPL")).valid());
    const auto repeated = instruments.apply(directory(1, "AAPL"));
    REQUIRE(repeated.valid());
    REQUIRE_FALSE(repeated.resolved_instrument.has_value());
    REQUIRE(instruments.selected_instruments().size() == 1);
  }

  SECTION("one locate with two symbols") {
    itchlab::InstrumentDirectory instruments{requested};
    REQUIRE(instruments.apply(directory(1, "AAPL")).valid());
    const auto rejected = instruments.apply(directory(1, "MSFT"));
    REQUIRE_FALSE(rejected.valid());
    REQUIRE(rejected.error->code == itchlab::ErrorCode::invariant);
    REQUIRE(instruments.selected_by_locate(1)->symbol == "AAPL");
  }

  SECTION("one symbol with two locates") {
    itchlab::InstrumentDirectory instruments{requested};
    REQUIRE(instruments.apply(directory(1, "AAPL")).valid());
    const auto rejected = instruments.apply(directory(2, "AAPL"));
    REQUIRE_FALSE(rejected.valid());
    REQUIRE(instruments.selected_by_locate(2) == nullptr);
  }

  SECTION("metadata contradiction") {
    itchlab::InstrumentDirectory instruments{requested};
    REQUIRE(instruments.apply(directory(1, "AAPL")).valid());
    const auto rejected = instruments.apply(directory(1, "AAPL", 200));
    REQUIRE_FALSE(rejected.valid());
    REQUIRE(instruments.selected_by_locate(1)->round_lot_size == 100);
  }

  SECTION("reserved locate and invalid round-lot flag") {
    itchlab::InstrumentDirectory instruments{requested};
    REQUIRE_FALSE(instruments.apply(directory(0, "AAPL")).valid());
    auto invalid_flag = directory(1, "AAPL");
    invalid_flag.round_lots_only = ' ';
    REQUIRE_FALSE(instruments.apply(invalid_flag).valid());
    REQUIRE(instruments.unresolved_symbols() == requested);
  }
}
