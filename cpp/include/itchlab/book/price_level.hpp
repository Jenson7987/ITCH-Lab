#pragma once

#include "itchlab/book/order.hpp"
#include "itchlab/core/types.hpp"

#include <optional>
#include <vector>

namespace itchlab {

struct AggregatedLevel {
  Price4 price4{};
  Shares total_quantity{};

  friend bool operator==(const AggregatedLevel&, const AggregatedLevel&) = default;
};

struct PriceLevelView {
  Price4 price4{};
  Shares total_quantity{};
  std::vector<OrderView> fifo_orders;

  friend bool operator==(const PriceLevelView&, const PriceLevelView&) = default;
};

struct TopLevels {
  std::vector<std::optional<AggregatedLevel>> bids;
  std::vector<std::optional<AggregatedLevel>> asks;

  friend bool operator==(const TopLevels&, const TopLevels&) = default;
};

} // namespace itchlab
