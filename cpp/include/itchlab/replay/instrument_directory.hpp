#pragma once

#include "itchlab/core/errors.hpp"
#include "itchlab/core/types.hpp"
#include "itchlab/itch/messages.hpp"

#include <cstddef>
#include <cstdint>
#include <optional>
#include <string>
#include <string_view>
#include <unordered_map>
#include <vector>

namespace itchlab {

struct Instrument {
  SymbolId symbol_id{};
  StockLocate stock_locate{};
  std::string symbol;
  char market_category{};
  char financial_status{};
  std::uint32_t round_lot_size{};
  bool round_lots_only{};

  friend bool operator==(const Instrument&, const Instrument&) = default;
};

struct DirectoryError {
  ErrorCode code{ErrorCode::invariant};
  std::string message;
};

struct DirectoryApplyResult {
  // Present only when this record resolves a requested symbol for the first time.
  std::optional<Instrument> resolved_instrument;
  std::optional<DirectoryError> error;

  [[nodiscard]] bool valid() const noexcept { return !error.has_value(); }
};

// One source-day directory. Requested SymbolIds are fixed by configuration order, while daily
// StockLocate values are learned from Stock Directory messages.
class InstrumentDirectory {
public:
  explicit InstrumentDirectory(const std::vector<std::string>& requested_symbols);

  [[nodiscard]] DirectoryApplyResult apply(const StockDirectory& directory);
  [[nodiscard]] const Instrument* selected_by_locate(StockLocate stock_locate) const noexcept;
  [[nodiscard]] bool knows_locate(StockLocate stock_locate) const noexcept;
  [[nodiscard]] bool requests(std::string_view symbol) const;
  [[nodiscard]] bool all_requested_resolved() const noexcept;
  [[nodiscard]] std::vector<std::string> unresolved_symbols() const;
  [[nodiscard]] std::vector<Instrument> selected_instruments() const;
  [[nodiscard]] std::size_t requested_count() const noexcept { return selected_by_id_.size(); }

private:
  struct DirectoryIdentity {
    std::string symbol;
    char market_category{};
    char financial_status{};
    std::uint32_t round_lot_size{};
    bool round_lots_only{};

    friend bool operator==(const DirectoryIdentity&, const DirectoryIdentity&) = default;
  };

  std::vector<std::string> requested_symbols_;
  std::unordered_map<std::string, SymbolId> requested_ids_;
  std::vector<std::optional<Instrument>> selected_by_id_;
  std::unordered_map<StockLocate, DirectoryIdentity> identities_by_locate_;
  std::unordered_map<std::string, StockLocate> locates_by_symbol_;
  std::unordered_map<StockLocate, Instrument> selected_by_locate_;
};

} // namespace itchlab
