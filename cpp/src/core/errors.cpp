#include "itchlab/core/errors.hpp"

namespace itchlab {

std::string_view error_code_name(const ErrorCode code) noexcept {
  switch (code) {
  case ErrorCode::input_path:
    return "ERR_INPUT_PATH";
  case ErrorCode::unsupported_compression:
    return "ERR_UNSUPPORTED_COMPRESSION";
  case ErrorCode::framing:
    return "ERR_FRAMING";
  case ErrorCode::truncated_message:
    return "ERR_TRUNCATED_MESSAGE";
  case ErrorCode::empty_input:
    return "ERR_EMPTY_INPUT";
  case ErrorCode::message_length:
    return "ERR_MESSAGE_LENGTH";
  case ErrorCode::unknown_message:
    return "ERR_UNKNOWN_MESSAGE";
  case ErrorCode::timestamp:
    return "ERR_TIMESTAMP";
  case ErrorCode::unknown_symbol:
    return "ERR_UNKNOWN_SYMBOL";
  case ErrorCode::trading_date:
    return "ERR_TRADING_DATE";
  case ErrorCode::order_reference:
    return "ERR_ORDER_REFERENCE";
  case ErrorCode::quantity:
    return "ERR_QUANTITY";
  case ErrorCode::price:
    return "ERR_PRICE";
  case ErrorCode::book_crossed:
    return "ERR_BOOK_CROSSED";
  case ErrorCode::invariant:
    return "ERR_INVARIANT";
  case ErrorCode::output_path:
    return "ERR_OUTPUT_PATH";
  case ErrorCode::disk_write:
    return "ERR_DISK_WRITE";
  case ErrorCode::hash_mismatch:
    return "ERR_HASH_MISMATCH";
  case ErrorCode::schema_version:
    return "ERR_SCHEMA_VERSION";
  case ErrorCode::partial_artefact:
    return "ERR_PARTIAL_ARTEFACT";
  case ErrorCode::config_schema:
    return "ERR_CONFIG_SCHEMA";
  case ErrorCode::session_window:
    return "ERR_SESSION_WINDOW";
  case ErrorCode::timezone:
    return "ERR_TIMEZONE";
  case ErrorCode::depth:
    return "ERR_DEPTH";
  case ErrorCode::horizon:
    return "ERR_HORIZON";
  case ErrorCode::partition:
    return "ERR_PARTITION";
  case ErrorCode::row_stride:
    return "ERR_ROW_STRIDE";
  case ErrorCode::seed:
    return "ERR_SEED";
  case ErrorCode::empty_dataset:
    return "ERR_EMPTY_DATASET";
  case ErrorCode::leakage_guard:
    return "ERR_LEAKAGE_GUARD";
  case ErrorCode::model_training:
    return "ERR_MODEL_TRAINING";
  case ErrorCode::prediction_key:
    return "ERR_PREDICTION_KEY";
  case ErrorCode::latency:
    return "ERR_LATENCY";
  case ErrorCode::cost:
    return "ERR_COST";
  case ErrorCode::queue_state:
    return "ERR_QUEUE_STATE";
  case ErrorCode::inventory_limit:
    return "ERR_INVENTORY_LIMIT";
  case ErrorCode::simulation_anomaly:
    return "ERR_SIMULATION_ANOMALY";
  case ErrorCode::broken_sim_fill:
    return "ERR_BROKEN_SIM_FILL";
  case ErrorCode::run_exists:
    return "ERR_RUN_EXISTS";
  case ErrorCode::cancelled:
    return "ERR_CANCELLED";
  case ErrorCode::internal:
    return "ERR_INTERNAL";
  }
  return "ERR_INTERNAL";
}

} // namespace itchlab
