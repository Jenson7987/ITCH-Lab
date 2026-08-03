#pragma once

#include <cstdint>
#include <string_view>

namespace itchlab {

// Stable public error identifiers. Numeric values are internal but explicit to prevent accidental
// renumbering in persisted diagnostics.
enum class ErrorCode : std::uint16_t {
  input_path = 1,
  unsupported_compression = 2,
  framing = 3,
  truncated_message = 4,
  empty_input = 5,
  message_length = 6,
  unknown_message = 7,
  timestamp = 8,
  unknown_symbol = 9,
  trading_date = 10,
  order_reference = 11,
  quantity = 12,
  price = 13,
  book_crossed = 14,
  invariant = 15,
  output_path = 16,
  disk_write = 17,
  hash_mismatch = 18,
  schema_version = 19,
  partial_artefact = 20,
  config_schema = 21,
  session_window = 22,
  timezone = 23,
  depth = 24,
  horizon = 25,
  partition = 26,
  row_stride = 27,
  seed = 28,
  empty_dataset = 29,
  leakage_guard = 30,
  model_training = 31,
  prediction_key = 32,
  latency = 33,
  cost = 34,
  queue_state = 35,
  inventory_limit = 36,
  simulation_anomaly = 37,
  broken_sim_fill = 38,
  run_exists = 39,
  cancelled = 40,
  internal = 41,
};

[[nodiscard]] std::string_view error_code_name(ErrorCode code) noexcept;

} // namespace itchlab
