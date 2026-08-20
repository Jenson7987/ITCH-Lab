#include "itchlab/performance/benchmark.hpp"

#include <benchmark/benchmark.h>

#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <filesystem>
#include <new>
#include <string>

namespace allocation_tracking {

thread_local bool enabled{};
thread_local std::uint64_t allocations{};

void* allocate(const std::size_t requested) {
  if (enabled) {
    ++allocations;
  }
  if (void* memory = std::malloc(requested == 0 ? 1 : requested); memory != nullptr) {
    return memory;
  }
  throw std::bad_alloc{};
}

} // namespace allocation_tracking

void* operator new(const std::size_t size) { return allocation_tracking::allocate(size); }
void* operator new[](const std::size_t size) { return allocation_tracking::allocate(size); }
void operator delete(void* memory) noexcept { std::free(memory); }
void operator delete[](void* memory) noexcept { std::free(memory); }
void operator delete(void* memory, std::size_t) noexcept { std::free(memory); }
void operator delete[](void* memory, std::size_t) noexcept { std::free(memory); }

namespace {

[[nodiscard]] std::filesystem::path fixture_path() {
  if (const auto* supplied = std::getenv("ITCHLAB_BENCHMARK_FIXTURE"); supplied != nullptr) {
    return supplied;
  }
  const auto performance_fixture =
      std::filesystem::path{ITCHLAB_SOURCE_DIR} / "data/fixtures/performance.itch";
  if (std::filesystem::is_regular_file(performance_fixture)) {
    return performance_fixture;
  }
  return std::filesystem::path{ITCHLAB_SOURCE_DIR} / "tests/fixtures/synthetic_mixed.itch";
}

void parser_book(benchmark::State& state) {
  for ([[maybe_unused]] const auto iteration : state) {
    allocation_tracking::allocations = 0;
    allocation_tracking::enabled = true;
    const auto result = itchlab::run_benchmarks(
        itchlab::BenchmarkOptions{fixture_path(), itchlab::BenchmarkStage::book, {"AAPL"}, 3});
    allocation_tracking::enabled = false;
    if (!result.valid()) {
      state.SkipWithError(result.error->message);
      return;
    }
    const auto& measurement = result.report->measurements.front();
    auto digest_count = measurement.final_book_digests.size();
    benchmark::DoNotOptimize(digest_count);
    state.counters["messages_per_second"] = measurement.median_messages_per_second;
    state.counters["peak_rss_bytes"] = static_cast<double>(result.report->peak_rss_bytes);
    const auto measured_passes = static_cast<double>(measurement.samples.size() + 1);
    const auto total_messages =
        static_cast<double>(measurement.samples.front().messages) * measured_passes;
    state.counters["allocations_per_message"] =
        static_cast<double>(allocation_tracking::allocations) / total_messages;
  }
}

BENCHMARK(parser_book)->Name("PERF-004/parser-book")->Unit(benchmark::kMillisecond);

} // namespace
