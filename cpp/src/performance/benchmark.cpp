#include "itchlab/performance/benchmark.hpp"

#include "itchlab/book/order_book.hpp"
#include "itchlab/core/sha256.hpp"
#include "itchlab/input/framed_reader.hpp"
#include "itchlab/itch/decoder.hpp"
#include "itchlab/itch/messages.hpp"
#include "itchlab/output/manifest.hpp"
#include "itchlab/output/snapshot_writer.hpp"
#include "itchlab/replay/instrument_directory.hpp"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <limits>
#include <map>
#include <memory>
#include <optional>
#include <string>
#include <string_view>
#include <sys/resource.h>
#include <unordered_map>
#include <utility>
#include <variant>
#include <vector>

namespace itchlab {
namespace {

enum class PassKind : std::uint8_t {
  framing,
  decoding,
  filtering,
  book,
  gzip_pipeline,
  snapshot_writer,
};

struct PassResult {
  std::optional<BenchmarkSample> sample;
  std::optional<BenchmarkError> error;
  std::map<std::string, std::string> digests;
};

struct CountingOutputState {
  std::uint64_t position{};
  std::uint64_t bytes_written{};
  bool closed{};
};

class CountingSnapshotOutput final : public SnapshotWriterOutput {
public:
  explicit CountingSnapshotOutput(std::shared_ptr<CountingOutputState> state)
      : state_{std::move(state)} {}

  [[nodiscard]] bool write(const std::span<const std::byte> bytes) override {
    if (state_->closed ||
        bytes.size() > std::numeric_limits<std::uint64_t>::max() - state_->position ||
        bytes.size() > std::numeric_limits<std::uint64_t>::max() - state_->bytes_written) {
      return false;
    }
    state_->position += static_cast<std::uint64_t>(bytes.size());
    state_->bytes_written += static_cast<std::uint64_t>(bytes.size());
    return true;
  }

  [[nodiscard]] bool seek(const std::uint64_t offset) override {
    if (state_->closed) {
      return false;
    }
    state_->position = offset;
    return true;
  }

  [[nodiscard]] bool flush() override { return !state_->closed; }

  [[nodiscard]] bool close() override {
    state_->closed = true;
    return true;
  }

private:
  std::shared_ptr<CountingOutputState> state_;
};

[[nodiscard]] PassResult fail(const ErrorCode code, std::string message) {
  return PassResult{std::nullopt, BenchmarkError{code, std::move(message)}, {}};
}

[[nodiscard]] std::optional<BookMessage> to_book_message(const MessageIndex message_index,
                                                         const ItchMessage& decoded) {
  if (const auto* add = std::get_if<AddOrder>(&decoded)) {
    return BookAdd{message_index,        add->header.stock_locate,
                   add->order_reference, add->side,
                   add->shares,          add->price4,
                   std::nullopt};
  }
  if (const auto* add = std::get_if<AddOrderWithAttribution>(&decoded)) {
    return BookAdd{
        message_index, add->header.stock_locate, add->order_reference, add->side, add->shares,
        add->price4,   add->attribution};
  }
  if (const auto* execute = std::get_if<OrderExecuted>(&decoded)) {
    return BookExecute{message_index, execute->header.stock_locate, execute->order_reference,
                       execute->executed_shares};
  }
  if (const auto* execute = std::get_if<OrderExecutedWithPrice>(&decoded)) {
    return BookExecute{message_index, execute->header.stock_locate, execute->order_reference,
                       execute->executed_shares};
  }
  if (const auto* cancel = std::get_if<OrderCancel>(&decoded)) {
    return BookCancel{message_index, cancel->header.stock_locate, cancel->order_reference,
                      cancel->cancelled_shares};
  }
  if (const auto* deletion = std::get_if<OrderDelete>(&decoded)) {
    return BookDelete{message_index, deletion->header.stock_locate, deletion->order_reference};
  }
  if (const auto* replace = std::get_if<OrderReplace>(&decoded)) {
    return BookReplace{message_index,
                       replace->header.stock_locate,
                       replace->original_order_reference,
                       replace->new_order_reference,
                       replace->shares,
                       replace->price4};
  }
  return std::nullopt;
}

[[nodiscard]] const MessageHeader& message_header(const ItchMessage& message) {
  return std::visit([](const auto& typed) -> const MessageHeader& { return typed.header; },
                    message);
}

[[nodiscard]] double rate(const std::uint64_t count, const std::uint64_t elapsed_ns) noexcept {
  if (elapsed_ns == 0) {
    return 0.0;
  }
  return static_cast<double>(count) * 1'000'000'000.0 / static_cast<double>(elapsed_ns);
}

[[nodiscard]] PassResult run_snapshot_writer_pass(const BenchmarkOptions& options) {
  auto opened = open_input_source(options.fixture);
  if (!opened.valid()) {
    return fail(opened.error->code, opened.error->message);
  }
  FramedMessageReader reader{*opened.source};
  std::uint64_t records{};
  while (true) {
    const auto framed = reader.next();
    if (framed.error) {
      return fail(framed.error->code, framed.error->message);
    }
    if (framed.end_of_file()) {
      break;
    }
    if (records == std::numeric_limits<std::uint64_t>::max()) {
      return fail(ErrorCode::internal, "Benchmark snapshot record counter overflowed.");
    }
    ++records;
  }
  if (records == 0) {
    return fail(ErrorCode::empty_input, "Benchmark fixture contains no framed messages.");
  }

  constexpr std::uint16_t depth = 10;
  auto output_state = std::make_shared<CountingOutputState>();
  auto writer =
      make_snapshot_writer(std::make_unique<CountingSnapshotOutput>(output_state), 1, depth);
  if (!writer.valid()) {
    return fail(writer.error->code, writer.error->message);
  }
  const auto initial_bytes = output_state->bytes_written;
  DiagnosticSnapshot snapshot;
  snapshot.symbol_id = 1;
  snapshot.stock_locate = 1;
  snapshot.symbol = options.symbols.front();
  snapshot.event_kind = "add";
  snapshot.depth = depth;
  snapshot.trading_state = TradingState::trading;
  snapshot.top_levels.bids.resize(depth);
  snapshot.top_levels.asks.resize(depth);

  const auto started = std::chrono::steady_clock::now();
  for (std::uint64_t index = 0; index < records; ++index) {
    snapshot.message_index = index + 1;
    snapshot.timestamp_ns = index + 1;
    if (const auto write_error = writer.writer->write_snapshot(snapshot)) {
      return fail(write_error->code, write_error->message);
    }
  }
  const auto completed = std::chrono::steady_clock::now();
  const auto elapsed = std::chrono::duration_cast<std::chrono::nanoseconds>(completed - started);
  const auto elapsed_ns = static_cast<std::uint64_t>(std::max<std::int64_t>(elapsed.count(), 1));
  const auto written_bytes = output_state->bytes_written - initial_bytes;
  if (const auto close_error = writer.writer->close_partial()) {
    return fail(close_error->code, close_error->message);
  }
  BenchmarkSample sample{elapsed_ns,
                         records,
                         0,
                         0,
                         records,
                         records,
                         written_bytes,
                         rate(records, elapsed_ns),
                         rate(records, elapsed_ns),
                         0.0,
                         0.0};
  return PassResult{sample, std::nullopt, {}};
}

[[nodiscard]] PassResult run_pass(const BenchmarkOptions& options, const PassKind kind) {
  if (kind == PassKind::snapshot_writer) {
    return run_snapshot_writer_pass(options);
  }
  auto opened = open_input_source(options.fixture);
  if (!opened.valid()) {
    return fail(opened.error->code, opened.error->message);
  }
  if (kind == PassKind::gzip_pipeline && opened.compression != InputCompression::gzip) {
    return fail(ErrorCode::unsupported_compression, "PERF-005 requires a gzip-compressed fixture.");
  }

  FramedMessageReader reader{*opened.source};
  const ItchDecoder decoder;
  InstrumentDirectory directory{options.symbols};
  std::unordered_map<StockLocate, std::unique_ptr<OrderBook>> books;
  std::uint64_t messages{};
  std::uint64_t selected{};
  std::uint64_t operations{};
  const auto started = std::chrono::steady_clock::now();

  while (true) {
    const auto framed = reader.next();
    if (framed.error) {
      return fail(framed.error->code, framed.error->message);
    }
    if (framed.end_of_file()) {
      break;
    }
    if (messages == std::numeric_limits<std::uint64_t>::max()) {
      return fail(ErrorCode::internal, "Benchmark message counter overflowed.");
    }
    ++messages;
    if (kind == PassKind::framing) {
      continue;
    }

    const auto decoded = decoder.decode(framed.frame->payload);
    if (!decoded.valid()) {
      return fail(decoded.error->code, decoded.error->message);
    }
    if (kind == PassKind::decoding) {
      continue;
    }
    if (const auto* stock_directory = std::get_if<StockDirectory>(&*decoded.message)) {
      const auto applied = directory.apply(*stock_directory);
      if (applied.error) {
        return fail(applied.error->code, applied.error->message);
      }
      if (applied.resolved_instrument) {
        books.emplace(applied.resolved_instrument->stock_locate,
                      std::make_unique<OrderBook>(applied.resolved_instrument->stock_locate));
      }
      continue;
    }
    if (std::holds_alternative<SystemEvent>(*decoded.message)) {
      continue;
    }
    const auto& header = message_header(*decoded.message);
    if (directory.selected_by_locate(header.stock_locate) == nullptr) {
      continue;
    }
    ++selected;
    if (kind == PassKind::filtering) {
      continue;
    }
    const auto book_message = to_book_message(framed.frame->message_index, *decoded.message);
    if (!book_message) {
      continue;
    }
    const auto book = books.find(header.stock_locate);
    if (book == books.end()) {
      return fail(ErrorCode::invariant,
                  "Selected benchmark message appeared before its Stock Directory record.");
    }
    const auto applied = book->second->apply(*book_message);
    if (!applied.valid()) {
      return fail(applied.error->code, applied.error->message);
    }
    ++operations;
  }

  const auto completed = std::chrono::steady_clock::now();
  const auto elapsed = std::chrono::duration_cast<std::chrono::nanoseconds>(completed - started);
  if (!directory.all_requested_resolved() && kind != PassKind::framing &&
      kind != PassKind::decoding) {
    return fail(ErrorCode::unknown_symbol,
                "A requested benchmark symbol was not announced by Stock Directory.");
  }

  std::map<std::string, std::string> digests;
  if (kind == PassKind::book || kind == PassKind::gzip_pipeline) {
    for (const auto& instrument : directory.selected_instruments()) {
      const auto book = books.find(instrument.stock_locate);
      if (book == books.end() || !book->second->check_invariants().valid()) {
        return fail(ErrorCode::invariant, "Benchmark final book invariants failed.");
      }
      digests.emplace(instrument.symbol, content_hash_to_hex(book->second->digest()));
    }
  }
  const auto progress = opened.source->progress();
  const auto elapsed_ns = static_cast<std::uint64_t>(std::max<std::int64_t>(elapsed.count(), 1));
  BenchmarkSample sample{elapsed_ns,
                         messages,
                         progress.source_bytes_consumed,
                         progress.uncompressed_bytes_delivered,
                         selected,
                         operations,
                         0,
                         rate(messages, elapsed_ns),
                         rate(operations, elapsed_ns),
                         rate(progress.source_bytes_consumed, elapsed_ns),
                         rate(progress.uncompressed_bytes_delivered, elapsed_ns)};
  return PassResult{sample, std::nullopt, std::move(digests)};
}

[[nodiscard]] std::uint64_t peak_rss_bytes() noexcept {
  rusage usage{};
  if (getrusage(RUSAGE_SELF, &usage) != 0 || usage.ru_maxrss < 0) {
    return 0;
  }
#if defined(__APPLE__)
  return static_cast<std::uint64_t>(usage.ru_maxrss);
#else
  constexpr std::uint64_t bytes_per_kibibyte = 1024;
  return static_cast<std::uint64_t>(usage.ru_maxrss) * bytes_per_kibibyte;
#endif
}

[[nodiscard]] std::vector<PassKind> requested_passes(const BenchmarkStage stage,
                                                     const InputCompression compression) {
  if (compression == InputCompression::gzip) {
    return {PassKind::gzip_pipeline};
  }
  switch (stage) {
  case BenchmarkStage::parser:
    return {PassKind::framing, PassKind::decoding};
  case BenchmarkStage::filter:
    return {PassKind::filtering};
  case BenchmarkStage::book:
    return {PassKind::book};
  case BenchmarkStage::all:
    return {PassKind::framing, PassKind::decoding, PassKind::filtering, PassKind::book,
            PassKind::snapshot_writer};
  }
  return {};
}

[[nodiscard]] std::pair<std::string_view, std::string_view> pass_identity(const PassKind kind) {
  switch (kind) {
  case PassKind::framing:
    return {"PERF-001", "framing"};
  case PassKind::decoding:
    return {"PERF-002", "decoding"};
  case PassKind::filtering:
    return {"PERF-003", "directory-filter"};
  case PassKind::book:
    return {"PERF-004", "parser-book"};
  case PassKind::gzip_pipeline:
    return {"PERF-005", "gzip-parser-book"};
  case PassKind::snapshot_writer:
    return {"PERF-006", "snapshot-writer"};
  }
  return {"PERF-000", "unknown"};
}

} // namespace

double benchmark_median(std::vector<double> values) {
  if (values.empty()) {
    return 0.0;
  }
  std::sort(values.begin(), values.end());
  const auto middle = values.size() / 2;
  if (values.size() % 2 != 0) {
    return values[middle];
  }
  return (values[middle - 1] + values[middle]) / 2.0;
}

double benchmark_mad(const std::vector<double>& values, const double median) {
  std::vector<double> deviations;
  deviations.reserve(values.size());
  for (const auto value : values) {
    deviations.push_back(std::abs(value - median));
  }
  return benchmark_median(std::move(deviations));
}

BenchmarkResult run_benchmarks(const BenchmarkOptions& options) {
  if (options.repetitions < 3 || options.repetitions > 100) {
    return BenchmarkResult{std::nullopt,
                           BenchmarkError{ErrorCode::config_schema,
                                          "Benchmark repetitions must be between 3 and 100."}};
  }
  if (options.symbols.empty()) {
    return BenchmarkResult{std::nullopt,
                           BenchmarkError{ErrorCode::config_schema,
                                          "Benchmark requires at least one selected symbol."}};
  }
  const auto hashed = hash_file(options.fixture, ErrorCode::input_path);
  if (!hashed.valid()) {
    return BenchmarkResult{std::nullopt, BenchmarkError{hashed.error->code, hashed.error->message}};
  }
  auto probe = open_input_source(options.fixture);
  if (!probe.valid()) {
    return BenchmarkResult{std::nullopt, BenchmarkError{probe.error->code, probe.error->message}};
  }
  const auto compression = probe.compression;
  const auto passes = requested_passes(options.stage, compression);
  std::vector<BenchmarkMeasurement> measurements;
  measurements.reserve(passes.size());

  for (const auto kind : passes) {
    const auto warmup = run_pass(options, kind);
    if (warmup.error) {
      return BenchmarkResult{std::nullopt, warmup.error};
    }
    BenchmarkMeasurement measurement;
    const auto [id, name] = pass_identity(kind);
    measurement.id = id;
    measurement.name = name;
    measurement.input_mode = compression;
    measurement.final_book_digests = warmup.digests;
    measurement.samples.reserve(options.repetitions);
    std::vector<double> message_rates;
    std::vector<double> operation_rates;
    std::vector<double> output_bytes_per_operation;
    std::vector<double> source_rates;
    std::vector<double> uncompressed_rates;
    for (std::uint16_t repetition = 0; repetition < options.repetitions; ++repetition) {
      const auto result = run_pass(options, kind);
      if (result.error) {
        return BenchmarkResult{std::nullopt, result.error};
      }
      if (result.digests != measurement.final_book_digests) {
        return BenchmarkResult{
            std::nullopt,
            BenchmarkError{ErrorCode::invariant, "Benchmark state digest changed between runs."}};
      }
      measurement.samples.push_back(*result.sample);
      message_rates.push_back(result.sample->messages_per_second);
      operation_rates.push_back(result.sample->operations_per_second);
      output_bytes_per_operation.push_back(
          result.sample->operations == 0 ? 0.0
                                         : static_cast<double>(result.sample->output_bytes) /
                                               static_cast<double>(result.sample->operations));
      source_rates.push_back(result.sample->source_bytes_per_second);
      uncompressed_rates.push_back(result.sample->uncompressed_bytes_per_second);
    }
    measurement.median_messages_per_second = benchmark_median(message_rates);
    measurement.mad_messages_per_second =
        benchmark_mad(message_rates, measurement.median_messages_per_second);
    measurement.median_operations_per_second = benchmark_median(std::move(operation_rates));
    measurement.median_output_bytes_per_operation =
        benchmark_median(std::move(output_bytes_per_operation));
    measurement.median_source_bytes_per_second = benchmark_median(std::move(source_rates));
    measurement.median_uncompressed_bytes_per_second =
        benchmark_median(std::move(uncompressed_rates));
    measurements.push_back(std::move(measurement));
  }

  return BenchmarkResult{BenchmarkReport{options.fixture.filename(), hashed.file->sha256,
                                         hashed.file->size_bytes, compression, options.repetitions,
                                         peak_rss_bytes(), std::move(measurements)},
                         std::nullopt};
}

} // namespace itchlab
