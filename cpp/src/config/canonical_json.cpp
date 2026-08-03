#include "itchlab/config/canonical_json.hpp"

#include "itchlab/core/sha256.hpp"

#include <nlohmann/json.hpp>

#include <string>

namespace itchlab {
namespace {

using Json = nlohmann::json;

Json replay_config_json(const ReplayConfig& config, const bool identity_projection) {
  Json input{{"exchange_timezone", config.input.exchange_timezone},
             {"trading_date", config.input.trading_date}};
  if (!identity_projection) {
    input["path"] = config.input.path;
    if (config.input.sha256) {
      input["sha256"] = content_hash_to_hex(*config.input.sha256);
    } else {
      input["sha256"] = nullptr;
    }
  }

  return Json{
      {"input", std::move(input)},
      {"output",
       {{"depth", config.output.depth},
        {"emit_unchanged_trade_snapshots", config.output.emit_unchanged_trade_snapshots}}},
      {"schema_version", config.schema_version},
      {"selection",
       {{"require_trading_state", config.selection.require_trading_state},
        {"session_end_ns", config.selection.session_end_ns},
        {"session_start_ns", config.selection.session_start_ns},
        {"symbols", config.selection.symbols}}},
      {"validation",
       {{"invariant_interval", config.validation.invariant_interval},
        {"max_skipped_messages", config.validation.max_skipped_messages},
        {"mode", config.validation.mode == ValidationMode::strict ? "strict" : "permissive"}}},
  };
}

} // namespace

std::string canonical_replay_config(const ReplayConfig& config) {
  return replay_config_json(config, false).dump(-1, ' ', false, Json::error_handler_t::strict);
}

std::string canonical_replay_identity_config(const ReplayConfig& config) {
  return replay_config_json(config, true).dump(-1, ' ', false, Json::error_handler_t::strict);
}

ConfigHashes replay_config_hashes(const ReplayConfig& config) {
  return ConfigHashes{sha256(canonical_replay_config(config)),
                      sha256(canonical_replay_identity_config(config))};
}

} // namespace itchlab
