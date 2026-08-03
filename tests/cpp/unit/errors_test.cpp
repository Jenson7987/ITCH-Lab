#include "itchlab/core/errors.hpp"

#include <catch2/catch_test_macros.hpp>

#include <array>
#include <string>
#include <string_view>
#include <unordered_set>

TEST_CASE("TASK-002 stable error catalogue matches public strings", "[TASK-002][errors]") {
  using itchlab::ErrorCode;
  constexpr std::array codes{
      ErrorCode::input_path,
      ErrorCode::unsupported_compression,
      ErrorCode::framing,
      ErrorCode::truncated_message,
      ErrorCode::empty_input,
      ErrorCode::message_length,
      ErrorCode::unknown_message,
      ErrorCode::timestamp,
      ErrorCode::unknown_symbol,
      ErrorCode::trading_date,
      ErrorCode::order_reference,
      ErrorCode::quantity,
      ErrorCode::price,
      ErrorCode::book_crossed,
      ErrorCode::invariant,
      ErrorCode::output_path,
      ErrorCode::disk_write,
      ErrorCode::hash_mismatch,
      ErrorCode::schema_version,
      ErrorCode::partial_artefact,
      ErrorCode::config_schema,
      ErrorCode::session_window,
      ErrorCode::timezone,
      ErrorCode::depth,
      ErrorCode::horizon,
      ErrorCode::partition,
      ErrorCode::row_stride,
      ErrorCode::seed,
      ErrorCode::empty_dataset,
      ErrorCode::leakage_guard,
      ErrorCode::model_training,
      ErrorCode::prediction_key,
      ErrorCode::latency,
      ErrorCode::cost,
      ErrorCode::queue_state,
      ErrorCode::inventory_limit,
      ErrorCode::simulation_anomaly,
      ErrorCode::broken_sim_fill,
      ErrorCode::run_exists,
      ErrorCode::cancelled,
      ErrorCode::internal,
  };

  std::unordered_set<std::string_view> names;
  for (const auto code : codes) {
    const auto name = itchlab::error_code_name(code);
    REQUIRE(name.starts_with("ERR_"));
    REQUIRE(names.insert(name).second);
  }
  REQUIRE(names.size() == codes.size());
  REQUIRE(std::string{itchlab::error_code_name(ErrorCode::config_schema)} == "ERR_CONFIG_SCHEMA");
  REQUIRE(std::string{itchlab::error_code_name(ErrorCode::broken_sim_fill)} ==
          "ERR_BROKEN_SIM_FILL");
}
