#include "itchlab/replay/replay_coordinator.hpp"

#include "itchlab/book/order.hpp"
#include "itchlab/book/order_book.hpp"
#include "itchlab/input/framed_reader.hpp"
#include "itchlab/itch/decoder.hpp"
#include "itchlab/itch/messages.hpp"
#include "itchlab/replay/error_policy.hpp"

#include <cstddef>
#include <cstdint>
#include <limits>
#include <map>
#include <memory>
#include <optional>
#include <string>
#include <string_view>
#include <unordered_map>
#include <unordered_set>
#include <utility>
#include <variant>

namespace itchlab {
namespace {

[[nodiscard]] ReplayResult
failure(const ErrorCode code, std::string message,
        const std::optional<MessageIndex> message_index = std::nullopt,
        const std::optional<std::uint64_t> source_offset = std::nullopt,
        const std::optional<std::uint8_t> source_type = std::nullopt,
        const std::optional<OrderReference> order_reference = std::nullopt,
        const std::optional<ReplayRuntimeContext> runtime = std::nullopt) {
  return ReplayResult{std::nullopt,
                      ReplayError{code, std::move(message), message_index, source_offset,
                                  source_type, order_reference, runtime}};
}

[[nodiscard]] bool is_in_session(const TimestampNs timestamp_ns,
                                 const ReplaySelectionConfig& selection) noexcept {
  return timestamp_ns >= selection.session_start_ns && timestamp_ns < selection.session_end_ns;
}

[[nodiscard]] const MessageHeader& message_header(const ItchMessage& message) {
  return std::visit([](const auto& typed) -> const MessageHeader& { return typed.header; },
                    message);
}

[[nodiscard]] std::optional<std::string_view> embedded_symbol(const ItchMessage& message) {
  if (const auto* action = std::get_if<TradingAction>(&message)) {
    return trimmed_alpha(action->stock);
  }
  if (const auto* add = std::get_if<AddOrder>(&message)) {
    return trimmed_alpha(add->stock);
  }
  if (const auto* add = std::get_if<AddOrderWithAttribution>(&message)) {
    return trimmed_alpha(add->stock);
  }
  if (const auto* trade = std::get_if<Trade>(&message)) {
    return trimmed_alpha(trade->stock);
  }
  if (const auto* cross = std::get_if<CrossTrade>(&message)) {
    return trimmed_alpha(cross->stock);
  }
  return std::nullopt;
}

[[nodiscard]] bool increment(std::uint64_t& value) noexcept {
  if (value == std::numeric_limits<std::uint64_t>::max()) {
    return false;
  }
  ++value;
  return true;
}

[[nodiscard]] bool increment(std::map<std::string, std::uint64_t>& counts, const std::string& key) {
  return increment(counts[key]);
}

[[nodiscard]] std::optional<std::uint64_t>
count_total(const std::map<std::string, std::uint64_t>& counts) noexcept {
  std::uint64_t total{};
  for (const auto& [type, count] : counts) {
    static_cast<void>(type);
    const auto next = checked_add(total, count);
    if (!next) {
      return std::nullopt;
    }
    total = *next;
  }
  return total;
}

[[nodiscard]] std::string unresolved_message(const std::vector<std::string>& symbols) {
  std::string message{"Requested symbol was not announced by Stock Directory: "};
  for (std::size_t index = 0; index < symbols.size(); ++index) {
    if (index != 0) {
      message += ", ";
    }
    message += symbols[index];
  }
  message += '.';
  return message;
}

[[nodiscard]] std::map<std::string, std::uint64_t> error_counts(const ErrorPolicy& policy) {
  std::map<std::string, std::uint64_t> result;
  for (const auto& [code, count] : policy.counts_by_code()) {
    result.emplace(error_code_name(code), count);
  }
  return result;
}

} // namespace

ReplayResult ReplayCoordinator::run(ByteSource& source, const ReplayConfig& config,
                                    EventSink& events, SnapshotSink& snapshots,
                                    const CancellationToken cancellation,
                                    ProgressReporter* const progress) const {
  if (config.selection.symbols.empty() ||
      config.selection.symbols.size() > std::numeric_limits<SymbolId>::max()) {
    return failure(ErrorCode::config_schema,
                   "Replay requires between one and 65535 selected symbols.");
  }
  if (config.input.sha256) {
    return failure(ErrorCode::config_schema,
                   "Provisional replay requires input.sha256 to be null.");
  }
  if (config.validation.invariant_interval == 0) {
    return failure(ErrorCode::config_schema, "Invariant interval must be positive.");
  }

  ReplaySummary summary;
  ErrorPolicy error_policy{config.validation};
  std::uint64_t skipped_decode_messages{};
  FramedMessageReader reader{source};
  const ItchDecoder decoder;
  InstrumentDirectory directory{config.selection.symbols};
  SessionState session;
  std::unordered_map<StockLocate, std::unique_ptr<OrderBook>> books;
  std::unordered_map<StockLocate, std::uint64_t> mutations_by_locate;
  std::unordered_set<StockLocate> unresolved_locates_observed;

  const auto runtime_context = [&]() {
    return ReplayRuntimeContext{summary.messages_processed, summary.selected_events,
                                error_policy.error_count(), source.progress()};
  };
  const auto report_progress = [&]() {
    if (progress != nullptr) {
      progress->observe(summary.messages_processed, source.progress(), summary.selected_events,
                        error_policy.error_count());
    }
  };
  const auto cancellation_failure = [&]() {
    return failure(ErrorCode::cancelled, "Replay cancellation was requested.", std::nullopt,
                   std::nullopt, std::nullopt, std::nullopt, runtime_context());
  };

  while (true) {
    report_progress();
    if (cancellation.is_cancellation_requested()) {
      return cancellation_failure();
    }
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
    const auto source_type_text = std::string(1, static_cast<char>(source_type));
    if (!increment(summary.messages_processed)) {
      return failure(ErrorCode::internal, "Replay message counter overflowed.", frame.message_index,
                     frame.source_offset, source_type);
    }

    const auto decoded = decoder.decode(frame.payload);
    if (decoded.error) {
      const auto decision = error_policy.observe(ReplayErrorOrigin::decoder, decoded.error->code);
      if (decision.counter_overflow) {
        return failure(ErrorCode::internal, "Replay error counter overflowed.", frame.message_index,
                       frame.source_offset, decoded.error->source_type);
      }
      if (decision.disposition == ErrorDisposition::skip) {
        if (!increment(skipped_decode_messages)) {
          return failure(ErrorCode::internal, "Skipped decoder-message counter overflowed.",
                         frame.message_index, frame.source_offset, decoded.error->source_type);
        }
        continue;
      }
      auto message = decoded.error->message;
      if (decision.disposition == ErrorDisposition::budget_exceeded) {
        message += " Permissive skipped-message budget of " +
                   std::to_string(config.validation.max_skipped_messages) + " was exceeded.";
      }
      return failure(decoded.error->code, std::move(message), frame.message_index,
                     frame.source_offset, decoded.error->source_type);
    }
    if (!increment(summary.decoded_messages) ||
        !increment(summary.all_counts_by_type, source_type_text)) {
      return failure(ErrorCode::internal, "Decoded-message counter overflowed.",
                     frame.message_index, frame.source_offset, source_type);
    }

    if (const auto* system_event = std::get_if<SystemEvent>(&*decoded.message)) {
      const auto applied = session.apply(frame.message_index, *system_event);
      if (applied.error) {
        return failure(applied.error->code, applied.error->message, frame.message_index,
                       frame.source_offset, source_type);
      }
      if (!increment(summary.global_system_messages)) {
        return failure(ErrorCode::internal, "Global-session counter overflowed.",
                       frame.message_index, frame.source_offset, source_type);
      }
      continue;
    }

    if (const auto* stock_directory = std::get_if<StockDirectory>(&*decoded.message)) {
      const auto applied = directory.apply(*stock_directory);
      if (applied.error) {
        return failure(applied.error->code, applied.error->message, frame.message_index,
                       frame.source_offset, source_type);
      }
      if (!increment(summary.directory_messages)) {
        return failure(ErrorCode::internal, "Stock Directory counter overflowed.",
                       frame.message_index, frame.source_offset, source_type);
      }
      if (applied.resolved_instrument) {
        const auto locate = applied.resolved_instrument->stock_locate;
        if (unresolved_locates_observed.contains(locate)) {
          return failure(ErrorCode::unknown_symbol,
                         "Selected instrument messages appeared before its Stock Directory record.",
                         frame.message_index, frame.source_offset, source_type);
        }
        if (const auto session_error = session.register_instrument(locate)) {
          return failure(session_error->code, session_error->message, frame.message_index,
                         frame.source_offset, source_type);
        }
        books.emplace(locate, std::make_unique<OrderBook>(locate));
        mutations_by_locate.emplace(locate, 0);
      }
      continue;
    }

    const auto& header = message_header(*decoded.message);
    const auto* instrument = directory.selected_by_locate(header.stock_locate);
    if (instrument == nullptr) {
      const auto symbol = embedded_symbol(*decoded.message);
      if (symbol && directory.requests(*symbol)) {
        const auto code = directory.knows_locate(header.stock_locate) ? ErrorCode::invariant
                                                                      : ErrorCode::unknown_symbol;
        return failure(
            code,
            directory.knows_locate(header.stock_locate)
                ? "Selected message symbol disagrees with its Stock Directory locate."
                : "Selected instrument message appeared before its Stock Directory record.",
            frame.message_index, frame.source_offset, source_type);
      }
      if (!directory.knows_locate(header.stock_locate)) {
        unresolved_locates_observed.insert(header.stock_locate);
      }
      if (!increment(summary.filtered_instrument_messages)) {
        return failure(ErrorCode::internal, "Filtered-message counter overflowed.",
                       frame.message_index, frame.source_offset, source_type);
      }
      continue;
    }

    if (!increment(summary.selected_instrument_messages) ||
        !increment(summary.selected_counts_by_type, source_type_text)) {
      return failure(ErrorCode::internal, "Selected-message counter overflowed.",
                     frame.message_index, frame.source_offset, source_type);
    }
    if (const auto symbol = embedded_symbol(*decoded.message);
        symbol && *symbol != instrument->symbol) {
      return failure(ErrorCode::invariant,
                     "Selected message symbol disagrees with Stock Directory.", frame.message_index,
                     frame.source_offset, source_type);
    }
    if (header.timestamp_ns >= config.selection.session_end_ns) {
      continue;
    }

    const auto book_entry = books.find(instrument->stock_locate);
    if (book_entry == books.end()) {
      return failure(ErrorCode::internal, "Selected book was not created after resolution.",
                     frame.message_index, frame.source_offset, source_type);
    }
    auto& book = *book_entry->second;
    const auto in_session = is_in_session(header.timestamp_ns, config.selection);

    const auto write_event = [&](const DiagnosticEvent& event,
                                 const std::optional<OrderReference> order_reference =
                                     std::nullopt) -> std::optional<ReplayResult> {
      const auto next_selected = checked_add(summary.selected_events, std::uint64_t{1});
      if (!next_selected) {
        return failure(ErrorCode::internal, "Selected-event counter overflowed.",
                       frame.message_index, frame.source_offset, source_type, order_reference);
      }
      if (const auto write_failure = events.write_event(event)) {
        return failure(write_failure->code, write_failure->message, frame.message_index,
                       frame.source_offset, source_type, order_reference);
      }
      summary.selected_events = *next_selected;
      return std::nullopt;
    };
    const auto write_snapshot = [&](const DiagnosticSnapshot& snapshot,
                                    const std::optional<OrderReference> order_reference =
                                        std::nullopt) -> std::optional<ReplayResult> {
      const auto next_snapshots = checked_add(summary.snapshots_written, std::uint64_t{1});
      if (!next_snapshots) {
        return failure(ErrorCode::internal, "Snapshot counter overflowed.", frame.message_index,
                       frame.source_offset, source_type, order_reference);
      }
      if (const auto write_failure = snapshots.write_snapshot(snapshot)) {
        return failure(write_failure->code, write_failure->message, frame.message_index,
                       frame.source_offset, source_type, order_reference);
      }
      summary.snapshots_written = *next_snapshots;
      return std::nullopt;
    };

    if (const auto* action = std::get_if<TradingAction>(&*decoded.message)) {
      const auto applied = session.apply(*action);
      if (applied.error) {
        return failure(applied.error->code, applied.error->message, frame.message_index,
                       frame.source_offset, source_type);
      }
      const auto reason = std::string{trimmed_alpha(action->reason)};
      const auto digest = book.digest();
      const DiagnosticEvent event{frame.message_index,
                                  frame.source_offset,
                                  header.timestamp_ns,
                                  instrument->symbol_id,
                                  instrument->stock_locate,
                                  instrument->symbol,
                                  "trading_state",
                                  static_cast<char>(source_type),
                                  std::nullopt,
                                  std::nullopt,
                                  std::nullopt,
                                  std::nullopt,
                                  std::nullopt,
                                  std::nullopt,
                                  std::nullopt,
                                  std::nullopt,
                                  reason.empty() ? std::nullopt
                                                 : std::optional<std::string>{reason},
                                  action->trading_state,
                                  in_session,
                                  digest};
      if (auto error = write_event(event)) {
        return std::move(*error);
      }
      if (in_session && applied.state_change->changed) {
        const auto current_state = session.state(instrument->stock_locate);
        if (!current_state) {
          return failure(ErrorCode::internal, "Selected trading state disappeared.",
                         frame.message_index, frame.source_offset, source_type);
        }
        const DiagnosticSnapshot snapshot{frame.message_index,
                                          header.timestamp_ns,
                                          instrument->symbol_id,
                                          instrument->stock_locate,
                                          instrument->symbol,
                                          "trading_state",
                                          config.output.depth,
                                          false,
                                          std::nullopt,
                                          std::nullopt,
                                          *current_state,
                                          book.top_levels(config.output.depth),
                                          digest};
        if (auto error = write_snapshot(snapshot)) {
          return std::move(*error);
        }
      }
      continue;
    }

    std::optional<BookMessage> book_message;
    std::string event_kind;
    std::optional<OrderReference> primary_reference;
    std::optional<OrderReference> secondary_reference;
    std::optional<Price4> event_price4;
    std::optional<Price4> execution_price4;
    std::optional<Shares> event_quantity;
    std::optional<std::string> aux_code;

    if (const auto* add = std::get_if<AddOrder>(&*decoded.message)) {
      book_message = BookAdd{frame.message_index,  add->header.stock_locate,
                             add->order_reference, add->side,
                             add->shares,          add->price4,
                             std::nullopt};
      event_kind = "add";
      primary_reference = add->order_reference;
      event_price4 = add->price4;
      event_quantity = add->shares;
    } else if (const auto* add = std::get_if<AddOrderWithAttribution>(&*decoded.message)) {
      book_message = BookAdd{frame.message_index,  add->header.stock_locate,
                             add->order_reference, add->side,
                             add->shares,          add->price4,
                             add->attribution};
      event_kind = "add";
      primary_reference = add->order_reference;
      event_price4 = add->price4;
      event_quantity = add->shares;
      aux_code = std::string{trimmed_alpha(add->attribution)};
    } else if (const auto* execute = std::get_if<OrderExecuted>(&*decoded.message)) {
      book_message = BookExecute{frame.message_index, execute->header.stock_locate,
                                 execute->order_reference, execute->executed_shares};
      event_kind = "execute";
      primary_reference = execute->order_reference;
      secondary_reference = execute->match_number;
      event_quantity = execute->executed_shares;
    } else if (const auto* execute = std::get_if<OrderExecutedWithPrice>(&*decoded.message)) {
      book_message = BookExecute{frame.message_index, execute->header.stock_locate,
                                 execute->order_reference, execute->executed_shares};
      event_kind = "execute_price";
      primary_reference = execute->order_reference;
      secondary_reference = execute->match_number;
      execution_price4 = execute->execution_price4;
      event_quantity = execute->executed_shares;
    } else if (const auto* cancel = std::get_if<OrderCancel>(&*decoded.message)) {
      book_message = BookCancel{frame.message_index, cancel->header.stock_locate,
                                cancel->order_reference, cancel->cancelled_shares};
      event_kind = "cancel";
      primary_reference = cancel->order_reference;
      event_quantity = cancel->cancelled_shares;
    } else if (const auto* delete_order = std::get_if<OrderDelete>(&*decoded.message)) {
      book_message = BookDelete{frame.message_index, delete_order->header.stock_locate,
                                delete_order->order_reference};
      event_kind = "delete";
      primary_reference = delete_order->order_reference;
    } else if (const auto* replace = std::get_if<OrderReplace>(&*decoded.message)) {
      book_message = BookReplace{frame.message_index,
                                 replace->header.stock_locate,
                                 replace->original_order_reference,
                                 replace->new_order_reference,
                                 replace->shares,
                                 replace->price4};
      event_kind = "replace";
      primary_reference = replace->original_order_reference;
      secondary_reference = replace->new_order_reference;
      event_price4 = replace->price4;
      event_quantity = replace->shares;
    }

    if (book_message) {
      const auto before = book.top_levels(config.output.depth);
      const auto applied = book.apply(*book_message);
      if (applied.error) {
        const auto decision =
            error_policy.observe(ReplayErrorOrigin::book_apply, applied.error->code);
        if (decision.counter_overflow) {
          return failure(ErrorCode::internal, "Replay error counter overflowed.",
                         frame.message_index, frame.source_offset, source_type,
                         applied.error->order_reference);
        }
        if (decision.disposition == ErrorDisposition::skip) {
          continue;
        }
        auto message = applied.error->message;
        if (decision.disposition == ErrorDisposition::budget_exceeded) {
          message += " Permissive skipped-message budget of " +
                     std::to_string(config.validation.max_skipped_messages) + " was exceeded.";
        }
        return failure(applied.error->code, std::move(message), frame.message_index,
                       frame.source_offset, source_type, applied.error->order_reference);
      }
      const auto& delta = *applied.delta;
      auto& mutation_count = mutations_by_locate.at(instrument->stock_locate);
      if (!increment(mutation_count)) {
        return failure(ErrorCode::internal, "Selected-mutation counter overflowed.",
                       frame.message_index, frame.source_offset, source_type,
                       delta.order_reference);
      }
      if (mutation_count % config.validation.invariant_interval == 0) {
        const auto invariants = book.check_invariants();
        if (!invariants.valid()) {
          return failure(ErrorCode::invariant, invariants.violations.front().message,
                         frame.message_index, frame.source_offset, source_type,
                         invariants.violations.front().order_reference);
        }
      }

      const auto after = book.top_levels(config.output.depth);
      const auto digest = book.digest();
      if (!event_quantity && delta.kind == BookMutationKind::delete_order) {
        event_quantity = delta.previous_remaining;
      }
      const DiagnosticEvent event{frame.message_index,
                                  frame.source_offset,
                                  header.timestamp_ns,
                                  instrument->symbol_id,
                                  instrument->stock_locate,
                                  instrument->symbol,
                                  event_kind,
                                  static_cast<char>(source_type),
                                  primary_reference,
                                  secondary_reference,
                                  delta.side,
                                  delta.price4,
                                  execution_price4,
                                  event_quantity,
                                  delta.previous_remaining,
                                  delta.remaining,
                                  aux_code,
                                  std::nullopt,
                                  in_session,
                                  digest};
      if (auto error = write_event(event, delta.order_reference)) {
        return std::move(*error);
      }

      const auto snapshot_allowed = in_session && before != after &&
                                    (!config.selection.require_trading_state ||
                                     session.is_tradable(instrument->stock_locate));
      if (snapshot_allowed) {
        const auto current_state = session.state(instrument->stock_locate);
        if (!current_state) {
          return failure(ErrorCode::internal, "Selected trading state disappeared.",
                         frame.message_index, frame.source_offset, source_type,
                         delta.order_reference);
        }
        const DiagnosticSnapshot snapshot{frame.message_index,
                                          header.timestamp_ns,
                                          instrument->symbol_id,
                                          instrument->stock_locate,
                                          instrument->symbol,
                                          event_kind,
                                          config.output.depth,
                                          true,
                                          event_price4 ? event_price4
                                                       : std::optional<Price4>{delta.price4},
                                          event_quantity,
                                          *current_state,
                                          after,
                                          digest};
        if (auto error = write_snapshot(snapshot, delta.order_reference)) {
          return std::move(*error);
        }
      }
      continue;
    }

    std::optional<char> event_subtype;
    if (const auto* trade = std::get_if<Trade>(&*decoded.message)) {
      event_kind = "trade";
      primary_reference = trade->order_reference;
      secondary_reference = trade->match_number;
      event_price4 = trade->price4;
      event_quantity = trade->shares;
      const auto current_state = session.state(instrument->stock_locate);
      if (!current_state) {
        return failure(ErrorCode::internal, "Selected trading state disappeared.",
                       frame.message_index, frame.source_offset, source_type);
      }
      const auto digest = book.digest();
      const DiagnosticEvent event{frame.message_index,
                                  frame.source_offset,
                                  header.timestamp_ns,
                                  instrument->symbol_id,
                                  instrument->stock_locate,
                                  instrument->symbol,
                                  event_kind,
                                  static_cast<char>(source_type),
                                  primary_reference,
                                  secondary_reference,
                                  trade->buy_sell_indicator,
                                  event_price4,
                                  std::nullopt,
                                  event_quantity,
                                  std::nullopt,
                                  std::nullopt,
                                  std::nullopt,
                                  std::nullopt,
                                  in_session,
                                  digest};
      if (auto error = write_event(event)) {
        return std::move(*error);
      }
      if (in_session && config.output.emit_unchanged_trade_snapshots &&
          (!config.selection.require_trading_state ||
           session.is_tradable(instrument->stock_locate))) {
        const DiagnosticSnapshot snapshot{frame.message_index,
                                          header.timestamp_ns,
                                          instrument->symbol_id,
                                          instrument->stock_locate,
                                          instrument->symbol,
                                          event_kind,
                                          config.output.depth,
                                          false,
                                          event_price4,
                                          event_quantity,
                                          *current_state,
                                          book.top_levels(config.output.depth),
                                          digest};
        if (auto error = write_snapshot(snapshot)) {
          return std::move(*error);
        }
      }
      continue;
    }

    if (const auto* cross = std::get_if<CrossTrade>(&*decoded.message)) {
      event_kind = "cross";
      secondary_reference = cross->match_number;
      event_price4 = cross->cross_price4;
      event_quantity = cross->shares;
      event_subtype = cross->cross_type;
    } else if (const auto* broken = std::get_if<BrokenTrade>(&*decoded.message)) {
      event_kind = "broken_trade";
      primary_reference = broken->match_number;
    } else {
      return failure(ErrorCode::internal, "Selected decoded message has no replay route.",
                     frame.message_index, frame.source_offset, source_type);
    }

    const auto current_state = session.state(instrument->stock_locate);
    if (!current_state) {
      return failure(ErrorCode::internal, "Selected trading state disappeared.",
                     frame.message_index, frame.source_offset, source_type);
    }
    const auto digest = book.digest();
    const DiagnosticEvent event{frame.message_index,
                                frame.source_offset,
                                header.timestamp_ns,
                                instrument->symbol_id,
                                instrument->stock_locate,
                                instrument->symbol,
                                event_kind,
                                static_cast<char>(source_type),
                                primary_reference,
                                secondary_reference,
                                std::nullopt,
                                event_price4,
                                std::nullopt,
                                event_quantity,
                                std::nullopt,
                                std::nullopt,
                                std::nullopt,
                                event_subtype,
                                in_session,
                                digest};
    if (auto error = write_event(event)) {
      return std::move(*error);
    }
    if (event_kind == "cross" && in_session && config.output.emit_unchanged_trade_snapshots &&
        (!config.selection.require_trading_state ||
         session.is_tradable(instrument->stock_locate))) {
      const DiagnosticSnapshot snapshot{frame.message_index,
                                        header.timestamp_ns,
                                        instrument->symbol_id,
                                        instrument->stock_locate,
                                        instrument->symbol,
                                        event_kind,
                                        config.output.depth,
                                        false,
                                        event_price4,
                                        event_quantity,
                                        *current_state,
                                        book.top_levels(config.output.depth),
                                        digest};
      if (auto error = write_snapshot(snapshot)) {
        return std::move(*error);
      }
    }
  }

  report_progress();
  if (cancellation.is_cancellation_requested()) {
    return cancellation_failure();
  }

  if (!directory.all_requested_resolved()) {
    return failure(ErrorCode::unknown_symbol, unresolved_message(directory.unresolved_symbols()));
  }
  const auto all_count_total = count_total(summary.all_counts_by_type);
  const auto selected_count_total = count_total(summary.selected_counts_by_type);
  const auto categorised_instrument_total =
      checked_add(summary.selected_instrument_messages, summary.filtered_instrument_messages);
  const auto categorised_directory_total =
      categorised_instrument_total
          ? checked_add(summary.directory_messages, *categorised_instrument_total)
          : std::nullopt;
  const auto categorised_total =
      categorised_directory_total
          ? checked_add(summary.global_system_messages, *categorised_directory_total)
          : std::nullopt;
  const auto processed_total = checked_add(summary.decoded_messages, skipped_decode_messages);
  if (!all_count_total || !selected_count_total || !categorised_total || !processed_total ||
      *all_count_total != summary.decoded_messages ||
      *selected_count_total != summary.selected_instrument_messages ||
      *categorised_total != summary.decoded_messages ||
      *processed_total != summary.messages_processed) {
    return failure(ErrorCode::internal, "Replay message-count reconciliation failed.");
  }

  for (const auto& instrument : directory.selected_instruments()) {
    const auto book = books.find(instrument.stock_locate);
    if (book == books.end()) {
      return failure(ErrorCode::internal, "Resolved selected instrument has no book.");
    }
    const auto invariants = book->second->check_invariants();
    if (!invariants.valid()) {
      return failure(ErrorCode::invariant, invariants.violations.front().message, std::nullopt,
                     std::nullopt, std::nullopt, invariants.violations.front().order_reference);
    }
    const auto final_state = session.state(instrument.stock_locate);
    if (!final_state) {
      return failure(ErrorCode::internal, "Resolved selected instrument has no session state.");
    }
    summary.instruments.push_back(ReplayInstrumentSummary{instrument, book->second->order_count(),
                                                          book->second->digest(), *final_state});
  }
  summary.global_session_events = session.global_events();
  summary.source_progress = source.progress();
  summary.errors_observed = error_policy.error_count();
  summary.skipped_messages = error_policy.skipped_messages();
  summary.degraded = error_policy.degraded();
  summary.error_counts_by_code = error_counts(error_policy);
  return ReplayResult{std::move(summary), std::nullopt};
}

} // namespace itchlab
