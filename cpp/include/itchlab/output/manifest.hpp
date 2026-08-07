#pragma once

#include "itchlab/config/canonical_json.hpp"
#include "itchlab/core/cancellation.hpp"
#include "itchlab/input/source_factory.hpp"
#include "itchlab/output/event_writer.hpp"
#include "itchlab/output/snapshot_writer.hpp"
#include "itchlab/replay/replay_coordinator.hpp"

#include <cstdint>
#include <filesystem>
#include <optional>
#include <string>
#include <string_view>

namespace itchlab {

inline constexpr std::uint16_t kReplayManifestSchemaVersion = 1;

struct HashedFile {
  ContentHash sha256{};
  std::uint64_t size_bytes{};

  friend bool operator==(const HashedFile&, const HashedFile&) = default;
};

struct FileHashResult {
  std::optional<HashedFile> file;
  std::optional<DiagnosticWriteError> error;

  [[nodiscard]] bool valid() const noexcept { return file.has_value() && !error.has_value(); }
};

// Streams a regular file through SHA-256. Cancellation is checked between bounded chunks.
[[nodiscard]] FileHashResult hash_file(const std::filesystem::path& path, ErrorCode read_error_code,
                                       CancellationToken cancellation = {});

[[nodiscard]] ContentHash replay_identity_hash(const ContentHash& source_sha256,
                                               const ContentHash& identity_config_sha256,
                                               const ContentHash& executable_sha256) noexcept;

struct BuildMetadata {
  std::string application_version;
  std::string git_revision;
  bool git_dirty{};
  std::string compiler;
  std::string compiler_version;
  std::string target;
  std::string build_type;
};

struct ReplayRunPaths {
  std::filesystem::path output_root;
  std::filesystem::path replay_root;
  std::filesystem::path lock_path;
  std::filesystem::path staging_directory;
  std::filesystem::path final_directory;
  std::filesystem::path event_path;
  std::filesystem::path snapshot_path;
  std::filesystem::path manifest_path;
};

struct ExistingReplay {
  std::string replay_id;
  std::string status;
  ReplayRunPaths paths;
  std::string manifest_document;
};

struct ReplayRunPreparation {
  std::optional<ReplayRunPaths> paths;
  std::optional<ExistingReplay> existing;
  std::optional<DiagnosticWriteError> error;

  [[nodiscard]] bool ready() const noexcept { return paths.has_value() && !error.has_value(); }
  [[nodiscard]] bool reused() const noexcept { return existing.has_value() && !error.has_value(); }
};

// Resolves a safe explicit root, enforces identity ownership and creates a fresh partial run.
[[nodiscard]] ReplayRunPreparation prepare_replay_run(const std::filesystem::path& output_root,
                                                      const std::filesystem::path& source_path,
                                                      const ContentHash& identity_sha256,
                                                      std::string replay_id, bool force_new_run,
                                                      CancellationToken cancellation = {});

struct ReplayManifestInput {
  std::string replay_id;
  ContentHash identity_sha256{};
  ReplayConfig effective_config;
  ConfigHashes config_hashes{};
  HashedFile source;
  std::string source_name;
  InputCompression compression{InputCompression::none};
  HashedFile executable;
  BuildMetadata build;
  std::string started_at;
  std::string completed_at;
  ReplaySummary summary;
  HashedFile events;
  HashedFile snapshots;
  std::uint64_t event_record_count{};
  std::uint64_t snapshot_record_count{};
};

struct ReplayManifestResult {
  std::optional<std::string> document;
  std::optional<DiagnosticWriteError> error;

  [[nodiscard]] bool valid() const noexcept { return document.has_value() && !error.has_value(); }
};

[[nodiscard]] ReplayManifestResult build_replay_manifest(const ReplayManifestInput& input);

// Publishes staged binary names, then the completed manifest, then atomically exposes the run
// directory. Existing targets are never replaced.
[[nodiscard]] std::optional<DiagnosticWriteError>
publish_replay_run(const ReplayRunPaths& paths, std::string_view manifest_document);

} // namespace itchlab
