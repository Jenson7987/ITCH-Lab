#pragma once

#include "itchlab/core/errors.hpp"
#include "itchlab/core/types.hpp"
#include "itchlab/input/source_factory.hpp"

#include <cstdint>
#include <filesystem>
#include <map>
#include <optional>
#include <string>
#include <vector>

namespace itchlab {

enum class BenchmarkStage : std::uint8_t {
  parser,
  filter,
  book,
  all,
};

struct BenchmarkOptions {
  std::filesystem::path fixture;
  BenchmarkStage stage{BenchmarkStage::all};
  std::vector<std::string> symbols{"AAPL"};
  std::uint16_t repetitions{10};
};

struct BenchmarkSample {
  std::uint64_t elapsed_ns{};
  std::uint64_t messages{};
  std::uint64_t source_bytes{};
  std::uint64_t uncompressed_bytes{};
  std::uint64_t selected_messages{};
  std::uint64_t operations{};
  std::uint64_t output_bytes{};
  double messages_per_second{};
  double operations_per_second{};
  double source_bytes_per_second{};
  double uncompressed_bytes_per_second{};
};

struct BenchmarkMeasurement {
  std::string id;
  std::string name;
  InputCompression input_mode{InputCompression::none};
  std::vector<BenchmarkSample> samples;
  double median_messages_per_second{};
  double mad_messages_per_second{};
  double median_operations_per_second{};
  double median_output_bytes_per_operation{};
  double median_source_bytes_per_second{};
  double median_uncompressed_bytes_per_second{};
  std::map<std::string, std::string> final_book_digests;
};

struct BenchmarkReport {
  std::filesystem::path fixture_name;
  ContentHash fixture_sha256{};
  std::uint64_t fixture_size_bytes{};
  InputCompression input_mode{InputCompression::none};
  std::uint16_t repetitions{};
  std::uint64_t peak_rss_bytes{};
  std::vector<BenchmarkMeasurement> measurements;
};

struct BenchmarkError {
  ErrorCode code{ErrorCode::internal};
  std::string message;
};

struct BenchmarkResult {
  std::optional<BenchmarkReport> report;
  std::optional<BenchmarkError> error;

  [[nodiscard]] bool valid() const noexcept { return report.has_value() && !error.has_value(); }
};

[[nodiscard]] BenchmarkResult run_benchmarks(const BenchmarkOptions& options);
[[nodiscard]] double benchmark_median(std::vector<double> values);
[[nodiscard]] double benchmark_mad(const std::vector<double>& values, double median);

} // namespace itchlab
