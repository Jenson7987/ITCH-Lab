#pragma once

#include "itchlab/config/replay_config.hpp"
#include "itchlab/core/types.hpp"

#include <string>

namespace itchlab {

struct ConfigHashes {
  ContentHash config_sha256;
  ContentHash identity_config_sha256;
};

// RFC 8785 canonical bytes for the validated replay-config v1 subset.
[[nodiscard]] std::string canonical_replay_config(const ReplayConfig& config);
[[nodiscard]] std::string canonical_replay_identity_config(const ReplayConfig& config);
[[nodiscard]] ConfigHashes replay_config_hashes(const ReplayConfig& config);

} // namespace itchlab
