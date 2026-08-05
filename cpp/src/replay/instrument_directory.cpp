#include "itchlab/replay/instrument_directory.hpp"

#include <algorithm>
#include <cstddef>
#include <string>
#include <utility>

namespace itchlab {
namespace {

[[nodiscard]] DirectoryApplyResult failure(std::string message) {
  return DirectoryApplyResult{std::nullopt,
                              DirectoryError{ErrorCode::invariant, std::move(message)}};
}

[[nodiscard]] bool valid_source_symbol(const std::string_view symbol) noexcept {
  if (symbol.empty() || symbol.size() > 8 || symbol.front() == ' ') {
    return false;
  }
  return std::all_of(symbol.begin(), symbol.end(), [](const char character) {
    const auto byte = static_cast<unsigned char>(character);
    return byte >= 0x20U && byte <= 0x7eU;
  });
}

} // namespace

InstrumentDirectory::InstrumentDirectory(const std::vector<std::string>& requested_symbols)
    : requested_symbols_{requested_symbols}, selected_by_id_(requested_symbols.size()) {
  requested_ids_.reserve(requested_symbols.size());
  for (std::size_t index = 0; index < requested_symbols.size(); ++index) {
    const auto symbol_id = static_cast<SymbolId>(index + 1);
    requested_ids_.emplace(requested_symbols[index], symbol_id);
  }
}

DirectoryApplyResult InstrumentDirectory::apply(const StockDirectory& directory) {
  const auto symbol = std::string{trimmed_alpha(directory.stock)};
  if (directory.header.stock_locate == 0) {
    return failure("Stock Directory uses the reserved global stock locate zero.");
  }
  if (!valid_source_symbol(symbol)) {
    return failure("Stock Directory contains an invalid source symbol.");
  }
  if (directory.round_lots_only != 'Y' && directory.round_lots_only != 'N') {
    return failure("Stock Directory round-lots-only value must be Y or N.");
  }

  const DirectoryIdentity identity{symbol, directory.market_category, directory.financial_status,
                                   directory.round_lot_size, directory.round_lots_only == 'Y'};
  const auto existing_locate = identities_by_locate_.find(directory.header.stock_locate);
  if (existing_locate != identities_by_locate_.end()) {
    if (existing_locate->second != identity) {
      return failure("Stock locate maps to contradictory Stock Directory metadata.");
    }
    return DirectoryApplyResult{};
  }
  const auto existing_symbol = locates_by_symbol_.find(symbol);
  if (existing_symbol != locates_by_symbol_.end() &&
      existing_symbol->second != directory.header.stock_locate) {
    return failure("Stock Directory symbol maps to contradictory daily stock locates.");
  }

  identities_by_locate_.emplace(directory.header.stock_locate, identity);
  locates_by_symbol_.emplace(symbol, directory.header.stock_locate);

  const auto requested = requested_ids_.find(symbol);
  if (requested == requested_ids_.end()) {
    return DirectoryApplyResult{};
  }

  const Instrument instrument{requested->second,
                              directory.header.stock_locate,
                              symbol,
                              directory.market_category,
                              directory.financial_status,
                              directory.round_lot_size,
                              directory.round_lots_only == 'Y'};
  const auto selected_index = static_cast<std::size_t>(requested->second - 1);
  if (selected_by_id_[selected_index]) {
    if (*selected_by_id_[selected_index] != instrument) {
      return failure("Requested symbol resolved to contradictory instrument metadata.");
    }
    return DirectoryApplyResult{};
  }

  selected_by_id_[selected_index] = instrument;
  selected_by_locate_.emplace(instrument.stock_locate, instrument);
  return DirectoryApplyResult{instrument, std::nullopt};
}

const Instrument*
InstrumentDirectory::selected_by_locate(const StockLocate stock_locate) const noexcept {
  const auto selected = selected_by_locate_.find(stock_locate);
  return selected == selected_by_locate_.end() ? nullptr : &selected->second;
}

bool InstrumentDirectory::knows_locate(const StockLocate stock_locate) const noexcept {
  return identities_by_locate_.contains(stock_locate);
}

bool InstrumentDirectory::requests(const std::string_view symbol) const {
  return requested_ids_.contains(std::string{symbol});
}

bool InstrumentDirectory::all_requested_resolved() const noexcept {
  return std::all_of(selected_by_id_.begin(), selected_by_id_.end(),
                     [](const auto& instrument) { return instrument.has_value(); });
}

std::vector<std::string> InstrumentDirectory::unresolved_symbols() const {
  std::vector<std::string> unresolved;
  for (std::size_t index = 0; index < selected_by_id_.size(); ++index) {
    if (!selected_by_id_[index]) {
      unresolved.push_back(requested_symbols_[index]);
    }
  }
  return unresolved;
}

std::vector<Instrument> InstrumentDirectory::selected_instruments() const {
  std::vector<Instrument> instruments;
  instruments.reserve(selected_by_id_.size());
  for (const auto& selected : selected_by_id_) {
    if (selected) {
      instruments.push_back(*selected);
    }
  }
  return instruments;
}

} // namespace itchlab
