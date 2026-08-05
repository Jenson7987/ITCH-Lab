#include "itchlab/replay/replay_coordinator.hpp"

#include "itchlab/book/order.hpp"
#include "itchlab/book/order_book.hpp"
#include "itchlab/input/framed_reader.hpp"
#include "itchlab/itch/decoder.hpp"
#include "itchlab/itch/messages.hpp"

#include <cstddef>
#include <cstdint>
#include <memory>
#include <optional>
#include <string>
#include <unordered_map>
#include <utility>
#include <variant>

namespace itchlab {
namespace {

[[nodiscard]] ReplayResult
failure(const ErrorCode code, std::string message,
        const std::optional<MessageIndex> message_index = std::nullopt,
        const std::optional<std::uint64_t> source_offset = std::nullopt,
        const std::optional<std::uint8_t> source_type = std::nullopt,
        const std::optional<OrderReference> order_reference = std::nullopt) {
  return ReplayResult{std::nullopt, ReplayError{code, std::move(message), message_index,
                                                source_offset, source_type, order_reference}};
}

[[nodiscard]] bool is_in_session(const TimestampNs timestamp_ns,
                                 const ReplaySelectionConfig& selection) noexcept {
  return timestamp_ns >= selection.session_start_ns && timestamp_ns < selection.session_end_ns;
}

} // namespace

ReplayResult ReplayCoordinator::run(ByteSource& source, const ReplayConfig& config,
                                    DiagnosticSink& diagnostics) const {
  if (config.selection.symbols.size() != 1) {
    return failure(ErrorCode::config_schema,
                   "TASK-007 diagnostic replay requires exactly one selected symbol.");
  }
  if (config.validation.mode != ValidationMode::strict) {
    return failure(ErrorCode::config_schema,
                   "TASK-007 diagnostic replay supports strict validation only.");
  }
  if (config.selection.require_trading_state) {
    return failure(ErrorCode::config_schema,
                   "Provisional diagnostic replay does not yet apply trading-state filtering.");
  }
  if (config.input.sha256) {
    return failure(ErrorCode::config_schema,
                   "TASK-007 diagnostic replay requires input.sha256 to be null.");
  }
  if (config.validation.invariant_interval == 0) {
    return failure(ErrorCode::config_schema, "Invariant interval must be positive.");
  }

  const auto& requested_symbol = config.selection.symbols.front();
  constexpr SymbolId symbol_id{1};
  ReplaySummary summary;
  FramedMessageReader reader{source};
  const ItchDecoder decoder;
  std::unordered_map<StockLocate, std::string> symbols_by_locate;
  std::unordered_map<std::string, StockLocate> locates_by_symbol;
  std::optional<StockLocate> selected_locate;
  std::unique_ptr<OrderBook> book;
  std::uint64_t selected_mutations{};

  while (true) {
    const auto framed = reader.next();
    if (framed.error) {
      return failure(framed.error->code, framed.error->message, framed.error->message_index,
                     framed.error->source_offset);
    }
    if (framed.end_of_file()) {
      break;
    }

    const auto& frame = *framed.frame;
    const auto source_type = std::to_integer<std::uint8_t>(frame.payload.front());
    const auto next_processed = checked_add(summary.messages_processed, std::uint64_t{1});
    if (!next_processed) {
      return failure(ErrorCode::internal, "Replay message counter overflowed.", frame.message_index,
                     frame.source_offset, source_type);
    }
    summary.messages_processed = *next_processed;

    const auto decoded = decoder.decode(frame.payload);
    if (decoded.error) {
      return failure(decoded.error->code, decoded.error->message, frame.message_index,
                     frame.source_offset, decoded.error->source_type);
    }
    const auto next_decoded = checked_add(summary.decoded_messages, std::uint64_t{1});
    if (!next_decoded) {
      return failure(ErrorCode::internal, "Decoded-message counter overflowed.",
                     frame.message_index, frame.source_offset, source_type);
    }
    summary.decoded_messages = *next_decoded;

    if (const auto* directory = std::get_if<StockDirectory>(&*decoded.message)) {
      const auto symbol = std::string{trimmed_alpha(directory->stock)};
      const auto locate = directory->header.stock_locate;
      const auto locate_entry = symbols_by_locate.find(locate);
      if (locate_entry != symbols_by_locate.end() && locate_entry->second != symbol) {
        return failure(ErrorCode::invariant,
                       "Stock locate maps to contradictory Stock Directory symbols.",
                       frame.message_index, frame.source_offset, source_type);
      }
      const auto symbol_entry = locates_by_symbol.find(symbol);
      if (symbol_entry != locates_by_symbol.end() && symbol_entry->second != locate) {
        return failure(ErrorCode::invariant,
                       "Stock Directory symbol maps to contradictory stock locates.",
                       frame.message_index, frame.source_offset, source_type);
      }
      symbols_by_locate[locate] = symbol;
      locates_by_symbol[symbol] = locate;
      if (symbol == requested_symbol) {
        if (selected_locate && *selected_locate != locate) {
          return failure(ErrorCode::invariant,
                         "Selected symbol was announced with more than one stock locate.",
                         frame.message_index, frame.source_offset, source_type);
        }
        selected_locate = locate;
        if (!book) {
          book = std::make_unique<OrderBook>(locate);
        }
      }
      continue;
    }

    std::optional<BookMessage> book_message;
    TimestampNs timestamp_ns{};
    Shares event_quantity{};
    if (const auto* add = std::get_if<AddOrder>(&*decoded.message)) {
      if (!selected_locate && std::string{trimmed_alpha(add->stock)} == requested_symbol) {
        return failure(ErrorCode::unknown_symbol,
                       "Selected Add Order appeared before its Stock Directory record.",
                       frame.message_index, frame.source_offset, source_type, add->order_reference);
      }
      if (selected_locate && add->header.stock_locate == *selected_locate) {
        if (std::string{trimmed_alpha(add->stock)} != requested_symbol) {
          return failure(
              ErrorCode::invariant, "Selected Add Order symbol disagrees with Stock Directory.",
              frame.message_index, frame.source_offset, source_type, add->order_reference);
        }
        timestamp_ns = add->header.timestamp_ns;
        event_quantity = add->shares;
        book_message = BookAdd{frame.message_index,  add->header.stock_locate,
                               add->order_reference, add->side,
                               add->shares,          add->price4,
                               std::nullopt};
      }
    } else if (const auto* delete_order = std::get_if<OrderDelete>(&*decoded.message)) {
      if (selected_locate && delete_order->header.stock_locate == *selected_locate) {
        timestamp_ns = delete_order->header.timestamp_ns;
        book_message = BookDelete{frame.message_index, delete_order->header.stock_locate,
                                  delete_order->order_reference};
      }
    }

    if (!book_message || timestamp_ns >= config.selection.session_end_ns) {
      continue;
    }
    if (!book) {
      return failure(ErrorCode::internal, "Selected book was not created after resolution.",
                     frame.message_index, frame.source_offset, source_type);
    }

    const auto before = book->top_levels(config.output.depth);
    const auto applied = book->apply(*book_message);
    if (applied.error) {
      return failure(applied.error->code, applied.error->message, frame.message_index,
                     frame.source_offset, source_type, applied.error->order_reference);
    }
    const auto& delta = *applied.delta;
    const auto next_mutations = checked_add(selected_mutations, std::uint64_t{1});
    if (!next_mutations) {
      return failure(ErrorCode::internal, "Selected-mutation counter overflowed.",
                     frame.message_index, frame.source_offset, source_type, delta.order_reference);
    }
    selected_mutations = *next_mutations;
    if (selected_mutations % config.validation.invariant_interval == 0) {
      const auto invariants = book->check_invariants();
      if (!invariants.valid()) {
        return failure(ErrorCode::invariant, invariants.violations.front().message,
                       frame.message_index, frame.source_offset, source_type,
                       invariants.violations.front().order_reference);
      }
    }

    const auto after = book->top_levels(config.output.depth);
    const auto digest = book->digest();
    const auto event_kind =
        delta.kind == BookMutationKind::add ? std::string{"add"} : std::string{"delete"};
    const auto quantity =
        delta.kind == BookMutationKind::add ? event_quantity : delta.previous_remaining;
    const DiagnosticEvent event{
        frame.message_index,
        frame.source_offset,
        timestamp_ns,
        symbol_id,
        *selected_locate,
        requested_symbol,
        event_kind,
        static_cast<char>(source_type),
        delta.order_reference,
        delta.side,
        delta.price4,
        quantity,
        delta.previous_remaining,
        delta.remaining,
        is_in_session(timestamp_ns, config.selection),
        digest,
    };
    if (const auto write_failure = diagnostics.write_event(event)) {
      return failure(write_failure->code, write_failure->message, frame.message_index,
                     frame.source_offset, source_type, delta.order_reference);
    }
    const auto next_selected = checked_add(summary.selected_events, std::uint64_t{1});
    if (!next_selected) {
      return failure(ErrorCode::internal, "Selected-event counter overflowed.", frame.message_index,
                     frame.source_offset, source_type, delta.order_reference);
    }
    summary.selected_events = *next_selected;

    if (event.in_session && before != after) {
      const DiagnosticSnapshot snapshot{frame.message_index,
                                        timestamp_ns,
                                        symbol_id,
                                        *selected_locate,
                                        requested_symbol,
                                        event_kind,
                                        config.output.depth,
                                        true,
                                        after,
                                        digest};
      if (const auto write_failure = diagnostics.write_snapshot(snapshot)) {
        return failure(write_failure->code, write_failure->message, frame.message_index,
                       frame.source_offset, source_type, delta.order_reference);
      }
      const auto next_snapshots = checked_add(summary.snapshots_written, std::uint64_t{1});
      if (!next_snapshots) {
        return failure(ErrorCode::internal, "Snapshot counter overflowed.", frame.message_index,
                       frame.source_offset, source_type, delta.order_reference);
      }
      summary.snapshots_written = *next_snapshots;
    }
  }

  if (!selected_locate || !book) {
    return failure(ErrorCode::unknown_symbol,
                   "Requested symbol was not announced by Stock Directory.");
  }
  const auto final_invariants = book->check_invariants();
  if (!final_invariants.valid()) {
    return failure(ErrorCode::invariant, final_invariants.violations.front().message, std::nullopt,
                   std::nullopt, std::nullopt, final_invariants.violations.front().order_reference);
  }

  summary.symbol_id = symbol_id;
  summary.stock_locate = *selected_locate;
  summary.symbol = requested_symbol;
  summary.final_order_count = book->order_count();
  summary.final_book_digest = book->digest();
  summary.source_progress = source.progress();
  return ReplayResult{std::move(summary), std::nullopt};
}

} // namespace itchlab
