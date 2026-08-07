#pragma once

#include "itchlab/core/errors.hpp"
#include "itchlab/core/types.hpp"

#include <cstdint>
#include <filesystem>
#include <optional>
#include <string>
#include <vector>

namespace itchlab {

enum class ValidationCheckStatus : std::uint8_t {
  passed,
  failed,
  not_run,
};

[[nodiscard]] const char* validation_check_status_name(ValidationCheckStatus status) noexcept;

struct ValidationCheck {
  std::string name;
  ValidationCheckStatus status{ValidationCheckStatus::not_run};
  std::string message;
  std::optional<std::string> expected;
  std::optional<std::string> actual;
  std::uint64_t records_examined{};
};

struct ValidatedArtefact {
  std::string kind;
  std::string filename;
  ContentHash sha256{};
  std::uint64_t size_bytes{};
  std::uint64_t declared_records{};
  std::uint64_t records_examined{};
};

struct ArtefactValidationReport {
  std::string target_kind;
  std::string target_name;
  bool deep{};
  std::vector<ValidationCheck> checks;
  std::vector<ValidatedArtefact> artefacts;

  [[nodiscard]] bool passed() const noexcept;
};

struct ArtefactValidationError {
  ErrorCode code{ErrorCode::invariant};
  std::string message;
  std::optional<std::string> check;
  std::optional<std::uint64_t> record_index;
  std::optional<std::string> expected;
  std::optional<std::string> actual;
};

struct ArtefactValidationResult {
  ArtefactValidationReport report;
  std::optional<ArtefactValidationError> error;

  [[nodiscard]] bool valid() const noexcept { return !error.has_value() && report.passed(); }
};

// Validates an immutable replay directory. Deep mode streams both child files and reconstructs the
// visible books from event-v1 before comparing the final order counts and state digests.
[[nodiscard]] ArtefactValidationResult
validate_replay_run(const std::filesystem::path& run_directory, bool deep,
                    const std::optional<std::filesystem::path>& source_path = std::nullopt);

// Validates one standalone event-v1 or snapshot-v1 file. Without a manifest, its child hash is
// reported rather than authenticated against an expected value and no final digest is claimed.
[[nodiscard]] ArtefactValidationResult
validate_interchange_file(const std::filesystem::path& file_path, bool deep,
                          const std::optional<std::filesystem::path>& source_path = std::nullopt);

} // namespace itchlab
