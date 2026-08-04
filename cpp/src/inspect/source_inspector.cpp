#include "itchlab/inspect/source_inspector.hpp"

#include "itchlab/input/framed_reader.hpp"
#include "itchlab/itch/decoder.hpp"
#include "itchlab/itch/messages.hpp"

#include <algorithm>
#include <cctype>
#include <cstddef>
#include <cstdint>
#include <limits>
#include <optional>
#include <set>
#include <string>
#include <unordered_map>
#include <utility>
#include <variant>

namespace itchlab {
namespace {

[[nodiscard]] std::string source_type_name(const std::uint8_t source_type) {
  if (std::isprint(static_cast<unsigned char>(source_type)) != 0) {
    return std::string(1, static_cast<char>(source_type));
  }
  constexpr char digits[] = "0123456789abcdef";
  std::string result{"0x00"};
  result[2] = digits[source_type >> 4U];
  result[3] = digits[source_type & 0x0fU];
  return result;
}

[[nodiscard]] const MessageHeader& message_header(const ItchMessage& message) {
  return std::visit([](const auto& typed) -> const MessageHeader& { return typed.header; },
                    message);
}

void increment(std::map<std::string, std::uint64_t>& counts, const std::string& key) {
  auto& count = counts[key];
  if (count != std::numeric_limits<std::uint64_t>::max()) {
    ++count;
  }
}

[[nodiscard]] InspectionResult failure(const ErrorCode code, std::string message,
                                       const std::optional<MessageIndex> message_index,
                                       const std::optional<std::uint64_t> source_offset,
                                       const std::optional<std::uint8_t> source_type) {
  return InspectionResult{std::nullopt, InspectionError{code, std::move(message), message_index,
                                                        source_offset, source_type}};
}

} // namespace

InspectionResult inspect_source(ByteSource& source, const InspectionOptions& options) {
  if (options.message_limit && *options.message_limit == 0) {
    return failure(ErrorCode::config_schema, "Inspection limit must be positive.", std::nullopt,
                   std::nullopt, std::nullopt);
  }

  InspectionSummary summary;
  std::set<std::string> requested{options.requested_symbols.begin(),
                                  options.requested_symbols.end()};
  std::set<std::string> found;
  std::unordered_map<StockLocate, std::string> symbols_by_locate;
  FramedMessageReader reader{source};
  const ItchDecoder decoder;

  while (!options.message_limit || summary.messages_examined < *options.message_limit) {
    const auto framed = reader.next();
    if (framed.error) {
      return failure(framed.error->code, framed.error->message, framed.error->message_index,
                     framed.error->source_offset, std::nullopt);
    }
    if (framed.end_of_file()) {
      summary.input_complete = true;
      break;
    }

    const auto& frame = *framed.frame;
    const auto source_type = std::to_integer<std::uint8_t>(frame.payload.front());
    const auto type_name = source_type_name(source_type);
    increment(summary.counts_by_type, type_name);
    const auto next_examined = checked_add(summary.messages_examined, std::uint64_t{1});
    if (!next_examined) {
      return failure(ErrorCode::internal, "Inspection message counter overflowed.",
                     frame.message_index, frame.source_offset, source_type);
    }
    summary.messages_examined = *next_examined;

    const auto decoded = decoder.decode(frame.payload);
    if (decoded.error) {
      increment(summary.parse_errors_by_code, std::string{error_code_name(decoded.error->code)});
      if (options.mode == ValidationMode::strict) {
        return failure(decoded.error->code, decoded.error->message, frame.message_index,
                       frame.source_offset, decoded.error->source_type);
      }
      continue;
    }

    const auto timestamp_ns = message_header(*decoded.message).timestamp_ns;
    if (!summary.first_timestamp_ns) {
      summary.first_timestamp_ns = timestamp_ns;
    }
    summary.last_timestamp_ns = timestamp_ns;

    if (const auto* directory = std::get_if<StockDirectory>(&*decoded.message)) {
      const auto symbol = std::string{trimmed_alpha(directory->stock)};
      const auto existing = symbols_by_locate.find(directory->header.stock_locate);
      if (existing != symbols_by_locate.end() && existing->second != symbol) {
        return failure(ErrorCode::invariant,
                       "Stock locate maps to contradictory Stock Directory symbols.",
                       frame.message_index, frame.source_offset, source_type);
      }
      symbols_by_locate[directory->header.stock_locate] = symbol;
      const auto next_count = checked_add(summary.stock_directory_count, std::uint64_t{1});
      if (!next_count) {
        return failure(ErrorCode::internal, "Stock Directory counter overflowed.",
                       frame.message_index, frame.source_offset, source_type);
      }
      summary.stock_directory_count = *next_count;
      if (requested.contains(symbol)) {
        found.insert(symbol);
      }
    }

    const auto locate = message_header(*decoded.message).stock_locate;
    const auto known_symbol = symbols_by_locate.find(locate);
    if (known_symbol != symbols_by_locate.end() && requested.contains(known_symbol->second)) {
      increment(summary.selected_counts_by_type, type_name);
    }
  }

  summary.source_progress = source.progress();
  for (const auto& requested_symbol : options.requested_symbols) {
    if (found.contains(requested_symbol)) {
      summary.requested_symbols_found.push_back(requested_symbol);
    }
  }

  if (summary.messages_examined == 0 && summary.input_complete) {
    return failure(ErrorCode::empty_input, "Input contains no framed ITCH messages.", std::nullopt,
                   std::nullopt, std::nullopt);
  }
  if (summary.input_complete && found.size() != requested.size()) {
    return failure(ErrorCode::unknown_symbol,
                   "At least one requested symbol was not announced by Stock Directory.",
                   std::nullopt, std::nullopt, std::nullopt);
  }
  return InspectionResult{std::move(summary), std::nullopt};
}

} // namespace itchlab
