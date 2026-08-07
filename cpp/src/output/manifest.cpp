#include "itchlab/output/manifest.hpp"

#include "itchlab/core/sha256.hpp"

#include <nlohmann/json.hpp>

#include <algorithm>
#include <array>
#include <cctype>
#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <limits>
#include <optional>
#include <span>
#include <string>
#include <string_view>
#include <system_error>
#include <utility>

namespace itchlab {
namespace {

using Json = nlohmann::json;

constexpr std::size_t kHashBufferSize = 64U * 1024U;
constexpr std::uintmax_t kMaximumManifestBytes = 1U << 20U;
constexpr std::string_view kEventFilename{"events.ilb"};
constexpr std::string_view kSnapshotFilename{"snapshots.ilb"};
constexpr std::string_view kManifestFilename{"replay-manifest.json"};

[[nodiscard]] DiagnosticWriteError output_error(std::string message) {
  return DiagnosticWriteError{ErrorCode::output_path, std::move(message)};
}

[[nodiscard]] DiagnosticWriteError write_error(std::string message) {
  return DiagnosticWriteError{ErrorCode::disk_write, std::move(message)};
}

[[nodiscard]] DiagnosticWriteError hash_error(std::string message) {
  return DiagnosticWriteError{ErrorCode::hash_mismatch, std::move(message)};
}

[[nodiscard]] bool path_is_within(const std::filesystem::path& child,
                                  const std::filesystem::path& parent) {
  auto child_iterator = child.begin();
  auto parent_iterator = parent.begin();
  for (; parent_iterator != parent.end(); ++parent_iterator, ++child_iterator) {
    if (child_iterator == child.end() || *child_iterator != *parent_iterator) {
      return false;
    }
  }
  return true;
}

[[nodiscard]] bool valid_run_id(const std::string_view value) noexcept {
  constexpr std::size_t timestamp_size = 26;
  constexpr std::size_t digest_size = 12;
  if (value.size() != timestamp_size + 1 + digest_size || value[8] != 'T' || value[15] != '.' ||
      value[25] != 'Z' || value[26] != '-') {
    return false;
  }
  for (std::size_t index = 0; index < timestamp_size; ++index) {
    if (index == 8 || index == 15 || index == 25) {
      continue;
    }
    if (std::isdigit(static_cast<unsigned char>(value[index])) == 0) {
      return false;
    }
  }
  return std::ranges::all_of(value.substr(27), [](const char character) {
    return (character >= '0' && character <= '9') || (character >= 'a' && character <= 'f');
  });
}

[[nodiscard]] bool valid_lock_name(const std::string_view value) noexcept {
  if (value.size() != 70 || value.front() != '.' || !value.ends_with(".lock")) {
    return false;
  }
  return std::ranges::all_of(value.substr(1, 64), [](const char character) {
    return (character >= '0' && character <= '9') || (character >= 'a' && character <= 'f');
  });
}

[[nodiscard]] bool safe_basename(const std::string_view value) {
  if (value.empty() || value.find('/') != std::string_view::npos ||
      value.find('\\') != std::string_view::npos) {
    return false;
  }
  const std::filesystem::path path{value};
  return !path.has_root_path() && path.filename() == path && value != "." && value != "..";
}

[[nodiscard]] bool valid_git_revision(const std::string_view value) noexcept {
  if (value == "unknown") {
    return true;
  }
  return value.size() == 40 && std::ranges::all_of(value, [](const char character) {
           return (character >= '0' && character <= '9') || (character >= 'a' && character <= 'f');
         });
}

[[nodiscard]] bool regular_file_without_symlink(const std::filesystem::path& path,
                                                std::error_code& error) {
  const auto status = std::filesystem::symlink_status(path, error);
  return !error && std::filesystem::is_regular_file(status);
}

[[nodiscard]] bool path_entry_exists(const std::filesystem::path& path, std::error_code& error) {
  const auto status = std::filesystem::symlink_status(path, error);
  if (error == std::errc::no_such_file_or_directory) {
    error.clear();
    return false;
  }
  return !error && std::filesystem::exists(status);
}

[[nodiscard]] std::optional<std::string>
read_small_regular_file(const std::filesystem::path& path, const std::uintmax_t maximum_size) {
  std::error_code error;
  if (!regular_file_without_symlink(path, error)) {
    return std::nullopt;
  }
  const auto size = std::filesystem::file_size(path, error);
  if (error || size > maximum_size) {
    return std::nullopt;
  }
  std::ifstream stream{path, std::ios::binary};
  if (!stream.is_open()) {
    return std::nullopt;
  }
  return std::string{std::istreambuf_iterator<char>{stream}, std::istreambuf_iterator<char>{}};
}

[[nodiscard]] std::optional<DiagnosticWriteError>
remove_exact_lock(const std::filesystem::path& path) {
  std::error_code error;
  if (!std::filesystem::remove(path, error) || error) {
    return write_error("Replay identity lock could not be removed before publication.");
  }
  return std::nullopt;
}

[[nodiscard]] std::optional<ExistingReplay>
verify_existing_replay(const std::filesystem::path& directory, const ContentHash& expected_identity,
                       std::optional<DiagnosticWriteError>& error,
                       const CancellationToken cancellation) {
  const auto manifest_path = directory / kManifestFilename;
  const auto document = read_small_regular_file(manifest_path, kMaximumManifestBytes);
  if (!document) {
    return std::nullopt;
  }

  Json manifest;
  try {
    manifest = Json::parse(*document);
  } catch (const std::exception&) {
    return std::nullopt;
  }
  if (!manifest.is_object() || !manifest.contains("identity_sha256") ||
      !manifest.at("identity_sha256").is_string() ||
      manifest.at("identity_sha256").get<std::string>() != content_hash_to_hex(expected_identity)) {
    return std::nullopt;
  }
  if (!manifest.contains("replay_id") || !manifest.at("replay_id").is_string() ||
      !manifest.contains("status") || !manifest.at("status").is_string() ||
      !manifest.contains("artefacts") || !manifest.at("artefacts").is_array()) {
    error = hash_error("An existing replay with the requested identity has an invalid manifest.");
    return std::nullopt;
  }
  const auto replay_id = manifest.at("replay_id").get<std::string>();
  const auto status = manifest.at("status").get<std::string>();
  if (!valid_run_id(replay_id) ||
      !replay_id.ends_with(content_hash_to_hex(expected_identity).substr(0, 12)) ||
      replay_id != directory.filename().string() ||
      (status != "completed" && status != "degraded")) {
    error = hash_error("An existing replay with the requested identity is not completed.");
    return std::nullopt;
  }

  std::optional<HashedFile> expected_events;
  std::optional<HashedFile> expected_snapshots;
  if (manifest.at("artefacts").size() != 2) {
    error = hash_error("An existing replay manifest has an invalid artefact count.");
    return std::nullopt;
  }
  for (const auto& artefact : manifest.at("artefacts")) {
    if (!artefact.is_object() || !artefact.contains("path") || !artefact.at("path").is_string() ||
        !artefact.contains("size_bytes") || !artefact.at("size_bytes").is_number_unsigned() ||
        !artefact.contains("sha256") || !artefact.at("sha256").is_string()) {
      error = hash_error("An existing replay artefact entry is invalid.");
      return std::nullopt;
    }
    const auto path = artefact.at("path").get<std::string>();
    const auto hash = content_hash_from_hex(artefact.at("sha256").get<std::string>());
    if (!hash) {
      error = hash_error("An existing replay artefact hash is invalid.");
      return std::nullopt;
    }
    const HashedFile expected{*hash, artefact.at("size_bytes").get<std::uint64_t>()};
    if (path == kEventFilename) {
      if (expected_events) {
        error = hash_error("An existing replay manifest repeats the event artefact.");
        return std::nullopt;
      }
      expected_events = expected;
    } else if (path == kSnapshotFilename) {
      if (expected_snapshots) {
        error = hash_error("An existing replay manifest repeats the snapshot artefact.");
        return std::nullopt;
      }
      expected_snapshots = expected;
    } else {
      error = hash_error("An existing replay manifest names an unexpected artefact.");
      return std::nullopt;
    }
  }
  if (!expected_events || !expected_snapshots) {
    error = hash_error("An existing replay manifest does not name both binary artefacts.");
    return std::nullopt;
  }

  std::error_code filesystem_error;
  if (!regular_file_without_symlink(directory / kEventFilename, filesystem_error) ||
      !regular_file_without_symlink(directory / kSnapshotFilename, filesystem_error)) {
    error = hash_error("An existing replay artefact is not a regular run-owned file.");
    return std::nullopt;
  }

  const auto actual_events =
      hash_file(directory / kEventFilename, ErrorCode::hash_mismatch, cancellation);
  if (!actual_events.valid()) {
    error = *actual_events.error;
    return std::nullopt;
  }
  const auto actual_snapshots =
      hash_file(directory / kSnapshotFilename, ErrorCode::hash_mismatch, cancellation);
  if (!actual_snapshots.valid()) {
    error = *actual_snapshots.error;
    return std::nullopt;
  }
  if (*actual_events.file != *expected_events || *actual_snapshots.file != *expected_snapshots) {
    error = hash_error("An existing replay with the requested identity failed file verification.");
    return std::nullopt;
  }

  ReplayRunPaths paths;
  paths.replay_root = directory.parent_path();
  paths.output_root = paths.replay_root.parent_path();
  paths.final_directory = directory;
  paths.event_path = directory / kEventFilename;
  paths.snapshot_path = directory / kSnapshotFilename;
  paths.manifest_path = manifest_path;
  return ExistingReplay{replay_id, status, std::move(paths), *document};
}

[[nodiscard]] bool json_integer(const std::uint64_t value) noexcept {
  return value <= kMaxJsonInteger;
}

[[nodiscard]] bool summary_integers_are_safe(const ReplaySummary& summary) noexcept {
  const std::array values{summary.messages_processed,
                          summary.decoded_messages,
                          summary.global_system_messages,
                          summary.directory_messages,
                          summary.selected_instrument_messages,
                          summary.filtered_instrument_messages,
                          summary.selected_events,
                          summary.snapshots_written,
                          summary.errors_observed,
                          summary.skipped_messages,
                          summary.source_progress.source_bytes_consumed,
                          summary.source_progress.uncompressed_bytes_delivered};
  if (!std::ranges::all_of(values, json_integer)) {
    return false;
  }
  const auto map_safe = [](const auto& counts) {
    return std::ranges::all_of(counts, [](const auto& item) { return json_integer(item.second); });
  };
  return map_safe(summary.all_counts_by_type) && map_safe(summary.selected_counts_by_type) &&
         map_safe(summary.error_counts_by_code);
}

[[nodiscard]] Json instrument_json(const ReplayInstrumentSummary& item) {
  return Json{{"final_book_digest", content_hash_to_hex(item.final_book_digest)},
              {"final_order_count", item.final_order_count},
              {"final_trading_state", trading_state_name(item.final_trading_state)},
              {"financial_status", std::string(1, item.instrument.financial_status)},
              {"market_category", std::string(1, item.instrument.market_category)},
              {"round_lot_size", item.instrument.round_lot_size},
              {"round_lots_only", item.instrument.round_lots_only},
              {"stock_locate", item.instrument.stock_locate},
              {"symbol", item.instrument.symbol},
              {"symbol_id", item.instrument.symbol_id}};
}

[[nodiscard]] std::optional<std::uint64_t>
expected_file_size(const std::size_t instrument_count, const std::uint16_t record_size,
                   const std::uint64_t record_count) noexcept {
  const auto dictionary = checked_multiply<std::uint64_t>(
      static_cast<std::uint64_t>(instrument_count), kInterchangeSymbolEntrySize);
  const auto prefix =
      dictionary ? checked_add<std::uint64_t>(kInterchangeHeaderSize, *dictionary) : std::nullopt;
  const auto records = checked_multiply<std::uint64_t>(record_count, record_size);
  return prefix && records ? checked_add<std::uint64_t>(*prefix, *records) : std::nullopt;
}

} // namespace

FileHashResult hash_file(const std::filesystem::path& path, const ErrorCode read_error_code,
                         const CancellationToken cancellation) {
  std::error_code filesystem_error;
  if (!std::filesystem::is_regular_file(path, filesystem_error) || filesystem_error) {
    return FileHashResult{
        std::nullopt,
        DiagnosticWriteError{read_error_code, "Hash input is not a readable regular file."}};
  }
  const auto size = std::filesystem::file_size(path, filesystem_error);
  const auto converted_size =
      filesystem_error ? std::nullopt : checked_integral_cast<std::uint64_t>(size);
  if (!converted_size) {
    return FileHashResult{
        std::nullopt,
        DiagnosticWriteError{read_error_code, "Hash input size exceeds the supported range."}};
  }

  std::ifstream stream{path, std::ios::binary};
  if (!stream.is_open()) {
    return FileHashResult{std::nullopt,
                          DiagnosticWriteError{read_error_code, "Hash input could not be opened."}};
  }
  Sha256Hasher hasher;
  std::array<std::byte, kHashBufferSize> buffer{};
  std::uint64_t observed{};
  while (stream) {
    if (cancellation.is_cancellation_requested()) {
      return FileHashResult{
          std::nullopt, DiagnosticWriteError{ErrorCode::cancelled, "File hashing was cancelled."}};
    }
    stream.read(reinterpret_cast<char*>(buffer.data()),
                static_cast<std::streamsize>(buffer.size()));
    const auto count = stream.gcount();
    if (count < 0) {
      return FileHashResult{std::nullopt,
                            DiagnosticWriteError{read_error_code, "Hash input read failed."}};
    }
    if (count != 0) {
      const auto byte_count = static_cast<std::size_t>(count);
      const auto next = checked_add(observed, static_cast<std::uint64_t>(byte_count));
      if (!next || !hasher.update(std::span<const std::byte>{buffer.data(), byte_count})) {
        return FileHashResult{std::nullopt, DiagnosticWriteError{ErrorCode::internal,
                                                                 "SHA-256 byte count overflowed."}};
      }
      observed = *next;
    }
  }
  if (!stream.eof() || observed != *converted_size) {
    return FileHashResult{
        std::nullopt,
        DiagnosticWriteError{read_error_code, "Hash input changed or failed during reading."}};
  }
  const auto digest = hasher.finalise();
  if (!digest) {
    return FileHashResult{
        std::nullopt, DiagnosticWriteError{ErrorCode::internal, "SHA-256 finalisation failed."}};
  }
  return FileHashResult{HashedFile{*digest, observed}, std::nullopt};
}

ContentHash replay_identity_hash(const ContentHash& source_sha256,
                                 const ContentHash& identity_config_sha256,
                                 const ContentHash& executable_sha256) noexcept {
  Sha256Hasher hasher;
  constexpr std::string_view domain{"itchlab-replay-v1"};
  const std::byte separator{};
  const std::array<std::byte, 2> version{std::byte{0}, std::byte{1}};
  static_cast<void>(hasher.update(std::as_bytes(std::span{domain.data(), domain.size()})));
  static_cast<void>(hasher.update(std::span<const std::byte>{&separator, 1}));
  static_cast<void>(hasher.update(source_sha256));
  static_cast<void>(hasher.update(identity_config_sha256));
  static_cast<void>(hasher.update(executable_sha256));
  static_cast<void>(hasher.update(version));
  return *hasher.finalise();
}

ReplayRunPreparation prepare_replay_run(const std::filesystem::path& output_root,
                                        const std::filesystem::path& source_path,
                                        const ContentHash& identity_sha256, std::string replay_id,
                                        const bool force_new_run,
                                        const CancellationToken cancellation) {
  if (output_root.empty() || !valid_run_id(replay_id) ||
      !replay_id.ends_with(content_hash_to_hex(identity_sha256).substr(0, 12))) {
    return ReplayRunPreparation{
        std::nullopt, std::nullopt,
        output_error("Replay output root or internally generated run ID is invalid.")};
  }

  std::error_code filesystem_error;
  const auto source = std::filesystem::canonical(source_path, filesystem_error);
  if (filesystem_error) {
    return ReplayRunPreparation{std::nullopt, std::nullopt,
                                output_error("Replay source path could not be resolved.")};
  }
  const auto requested_root = std::filesystem::absolute(output_root, filesystem_error);
  if (filesystem_error) {
    return ReplayRunPreparation{std::nullopt, std::nullopt,
                                output_error("Replay output root could not be resolved.")};
  }
  const auto root_exists = std::filesystem::exists(requested_root, filesystem_error);
  if (filesystem_error) {
    return ReplayRunPreparation{std::nullopt, std::nullopt,
                                output_error("Replay output root could not be inspected.")};
  }
  if (root_exists && std::filesystem::is_symlink(
                         std::filesystem::symlink_status(requested_root, filesystem_error))) {
    return ReplayRunPreparation{
        std::nullopt, std::nullopt,
        output_error("A symlink is not accepted as the replay output root.")};
  }
  if (filesystem_error) {
    return ReplayRunPreparation{std::nullopt, std::nullopt,
                                output_error("Replay output root could not be inspected.")};
  }
  if (root_exists) {
    if (filesystem_error || !std::filesystem::is_directory(requested_root, filesystem_error) ||
        filesystem_error) {
      return ReplayRunPreparation{std::nullopt, std::nullopt,
                                  output_error("Replay output root is not a writable directory.")};
    }
  } else {
    std::filesystem::create_directories(requested_root, filesystem_error);
    if (filesystem_error) {
      return ReplayRunPreparation{
          std::nullopt, std::nullopt,
          output_error("Replay output root directory could not be created.")};
    }
  }

  const auto resolved_root = std::filesystem::weakly_canonical(requested_root, filesystem_error);
  const auto current_path = std::filesystem::current_path(filesystem_error);
  const auto current_directory = filesystem_error
                                     ? std::filesystem::path{}
                                     : std::filesystem::canonical(current_path, filesystem_error);
  if (filesystem_error || resolved_root == resolved_root.root_path() ||
      resolved_root == current_directory || path_is_within(source, resolved_root)) {
    return ReplayRunPreparation{
        std::nullopt, std::nullopt,
        output_error("Replay output root aliases an unsafe broad or source-containing path.")};
  }

  const auto replay_root = resolved_root / "replay";
  if (std::filesystem::exists(replay_root, filesystem_error)) {
    if (filesystem_error || !std::filesystem::is_directory(replay_root, filesystem_error) ||
        filesystem_error ||
        std::filesystem::is_symlink(
            std::filesystem::symlink_status(replay_root, filesystem_error))) {
      return ReplayRunPreparation{std::nullopt, std::nullopt,
                                  output_error("Replay run root is not a safe directory.")};
    }
  } else {
    std::filesystem::create_directory(replay_root, filesystem_error);
    if (filesystem_error) {
      return ReplayRunPreparation{std::nullopt, std::nullopt,
                                  output_error("Replay run root could not be created.")};
    }
  }

  const auto identity_text = content_hash_to_hex(identity_sha256);
  const auto lock_path = replay_root / ("." + identity_text + ".lock");
  if (!std::filesystem::create_directory(lock_path, filesystem_error) || filesystem_error) {
    return ReplayRunPreparation{
        std::nullopt, std::nullopt,
        DiagnosticWriteError{ErrorCode::run_exists,
                             "A replay with this identity is already running or left a lock."}};
  }
  const auto fail_after_lock = [&](DiagnosticWriteError failure) {
    if (const auto lock_error = remove_exact_lock(lock_path)) {
      return ReplayRunPreparation{std::nullopt, std::nullopt, lock_error};
    }
    return ReplayRunPreparation{std::nullopt, std::nullopt, std::move(failure)};
  };

  if (!force_new_run) {
    for (std::filesystem::directory_iterator iterator{replay_root, filesystem_error}, end;
         !filesystem_error && iterator != end; iterator.increment(filesystem_error)) {
      const auto entry_status = iterator->symlink_status(filesystem_error);
      if (filesystem_error) {
        break;
      }
      if (!std::filesystem::is_directory(entry_status) || iterator->path() == lock_path) {
        continue;
      }
      if (iterator->path().extension() == ".partial") {
        const auto marker = read_small_regular_file(iterator->path() / "identity.sha256", 65);
        if (marker && *marker == identity_text + '\n') {
          return fail_after_lock(DiagnosticWriteError{
              ErrorCode::run_exists, "A partial replay with this identity already exists."});
        }
        continue;
      }
      std::optional<DiagnosticWriteError> existing_error;
      if (auto existing = verify_existing_replay(iterator->path(), identity_sha256, existing_error,
                                                 cancellation)) {
        if (const auto lock_error = remove_exact_lock(lock_path)) {
          return ReplayRunPreparation{std::nullopt, std::nullopt, lock_error};
        }
        return ReplayRunPreparation{std::nullopt, std::move(existing), std::nullopt};
      }
      if (existing_error) {
        return fail_after_lock(*existing_error);
      }
    }
    if (filesystem_error) {
      return fail_after_lock(output_error("Existing replay directories could not be read."));
    }
  }

  ReplayRunPaths paths;
  paths.output_root = resolved_root;
  paths.replay_root = replay_root;
  paths.lock_path = lock_path;
  paths.staging_directory = replay_root / (replay_id + ".partial");
  paths.final_directory = replay_root / replay_id;
  if (std::filesystem::exists(paths.final_directory, filesystem_error) || filesystem_error ||
      !std::filesystem::create_directory(paths.staging_directory, filesystem_error) ||
      filesystem_error) {
    return fail_after_lock(DiagnosticWriteError{
        ErrorCode::run_exists, "Replay run path already exists; no path was replaced."});
  }

  std::ofstream marker{paths.staging_directory / "identity.sha256", std::ios::binary};
  marker << identity_text << '\n';
  marker.flush();
  marker.close();
  if (marker.fail()) {
    return fail_after_lock(write_error("Replay identity marker could not be written."));
  }

  paths.event_path = paths.staging_directory / kEventFilename;
  paths.snapshot_path = paths.staging_directory / kSnapshotFilename;
  paths.manifest_path = paths.staging_directory / kManifestFilename;
  return ReplayRunPreparation{std::move(paths), std::nullopt, std::nullopt};
}

ReplayManifestResult build_replay_manifest(const ReplayManifestInput& input) {
  const auto identity_text = content_hash_to_hex(input.identity_sha256);
  const auto config_hashes = replay_config_hashes(input.effective_config);
  const auto snapshot_size = snapshot_record_size(input.effective_config.output.depth);
  const auto expected_events = expected_file_size(input.summary.instruments.size(),
                                                  kEventRecordSize, input.event_record_count);
  const auto expected_snapshots =
      snapshot_size ? expected_file_size(input.summary.instruments.size(), *snapshot_size,
                                         input.snapshot_record_count)
                    : std::nullopt;
  const auto fail = [](std::string message) {
    return ReplayManifestResult{std::nullopt,
                                DiagnosticWriteError{ErrorCode::invariant, std::move(message)}};
  };

  if (!valid_run_id(input.replay_id) || !input.replay_id.ends_with(identity_text.substr(0, 12)) ||
      input.config_hashes.config_sha256 != config_hashes.config_sha256 ||
      input.config_hashes.identity_config_sha256 != config_hashes.identity_config_sha256 ||
      !input.effective_config.input.sha256 ||
      *input.effective_config.input.sha256 != input.source.sha256 ||
      !safe_basename(input.effective_config.input.path) || !safe_basename(input.source_name)) {
    return fail("Replay manifest identity, config or source metadata is inconsistent.");
  }
  if (input.summary.instruments.empty() ||
      input.summary.selected_events != input.event_record_count ||
      input.summary.snapshots_written != input.snapshot_record_count || !snapshot_size ||
      !expected_events || !expected_snapshots || input.events.size_bytes != *expected_events ||
      input.snapshots.size_bytes != *expected_snapshots ||
      input.source.size_bytes > kMaxJsonInteger || input.events.size_bytes > kMaxJsonInteger ||
      input.snapshots.size_bytes > kMaxJsonInteger || !summary_integers_are_safe(input.summary)) {
    return fail("Replay manifest counts or artefact sizes are inconsistent.");
  }
  if (input.build.application_version.empty() || !valid_git_revision(input.build.git_revision) ||
      input.build.compiler.empty() || input.build.compiler_version.empty() ||
      input.build.target.empty() || input.build.build_type.empty() || input.started_at.empty() ||
      input.completed_at.empty()) {
    return fail("Replay manifest build or observational metadata is incomplete.");
  }

  Json effective_config;
  try {
    effective_config = Json::parse(canonical_replay_config(input.effective_config));
  } catch (const std::exception&) {
    return fail("Replay effective config could not be serialised.");
  }

  auto instruments = Json::array();
  for (const auto& item : input.summary.instruments) {
    if (item.final_order_count > kMaxJsonInteger) {
      return fail("Replay final order count exceeds the I-JSON range.");
    }
    instruments.push_back(instrument_json(item));
  }
  auto session_events = Json::array();
  for (const auto& event : input.summary.global_session_events) {
    if (!json_integer(event.message_index) || !json_integer(event.timestamp_ns)) {
      return fail("Replay session metadata exceeds the I-JSON range.");
    }
    session_events.push_back({{"event_code", std::string(1, event.event_code)},
                              {"message_index", event.message_index},
                              {"timestamp_ns", event.timestamp_ns}});
  }

  const auto status = input.summary.degraded ? "degraded" : "completed";
  const auto code_revision = input.build.git_revision + (input.build.git_dirty ? "+dirty" : "");
  const Json manifest{
      {"artefacts",
       {{{"kind", "events"},
         {"path", kEventFilename},
         {"record_count", input.event_record_count},
         {"record_size", kEventRecordSize},
         {"schema_version", kInterchangeSchemaVersion},
         {"sha256", content_hash_to_hex(input.events.sha256)},
         {"size_bytes", input.events.size_bytes}},
        {{"depth", input.effective_config.output.depth},
         {"kind", "snapshots"},
         {"path", kSnapshotFilename},
         {"record_count", input.snapshot_record_count},
         {"record_size", *snapshot_size},
         {"schema_version", kInterchangeSchemaVersion},
         {"sha256", content_hash_to_hex(input.snapshots.sha256)},
         {"size_bytes", input.snapshots.size_bytes}}}},
      {"build",
       {{"application_version", input.build.application_version},
        {"build_type", input.build.build_type},
        {"compiler", input.build.compiler},
        {"compiler_version", input.build.compiler_version},
        {"target", input.build.target}}},
      {"code_revision", code_revision},
      {"completed_at", input.completed_at},
      {"config", std::move(effective_config)},
      {"config_sha256", content_hash_to_hex(input.config_hashes.config_sha256)},
      {"counts",
       {{"all_by_type", input.summary.all_counts_by_type},
        {"decoded_messages", input.summary.decoded_messages},
        {"directory_messages", input.summary.directory_messages},
        {"errors_observed", input.summary.errors_observed},
        {"filtered_instrument_messages", input.summary.filtered_instrument_messages},
        {"global_system_messages", input.summary.global_system_messages},
        {"messages_processed", input.summary.messages_processed},
        {"selected_by_type", input.summary.selected_counts_by_type},
        {"selected_events", input.summary.selected_events},
        {"selected_instrument_messages", input.summary.selected_instrument_messages},
        {"skipped_messages", input.summary.skipped_messages},
        {"snapshots_written", input.summary.snapshots_written}}},
      {"error_summary", input.summary.error_counts_by_code},
      {"executable_sha256", content_hash_to_hex(input.executable.sha256)},
      {"global_session_events", std::move(session_events)},
      {"identity_config_sha256", content_hash_to_hex(input.config_hashes.identity_config_sha256)},
      {"identity_sha256", identity_text},
      {"instruments", std::move(instruments)},
      {"publishable", !input.build.git_dirty && input.build.build_type == "Release" &&
                          input.build.git_revision != "unknown"},
      {"replay_id", input.replay_id},
      {"schema_version", kReplayManifestSchemaVersion},
      {"source",
       {{"canonical_name", input.source_name},
        {"compression", input_compression_name(input.compression)},
        {"exchange_timezone", input.effective_config.input.exchange_timezone},
        {"framing", "itch-length-v1"},
        {"sha256", content_hash_to_hex(input.source.sha256)},
        {"size_bytes", input.source.size_bytes},
        {"trading_date", input.effective_config.input.trading_date}}},
      {"started_at", input.started_at},
      {"status", status}};
  try {
    return ReplayManifestResult{manifest.dump(2, ' ', false, Json::error_handler_t::strict) + '\n',
                                std::nullopt};
  } catch (const Json::exception&) {
    return fail("Replay manifest contains text that cannot be serialised as strict UTF-8.");
  }
}

std::optional<DiagnosticWriteError> publish_replay_run(const ReplayRunPaths& paths,
                                                       const std::string_view manifest_document) {
  const auto expected_staging_name = paths.final_directory.filename().string() + ".partial";
  if (paths.output_root.empty() || paths.replay_root != paths.output_root / "replay" ||
      !valid_run_id(paths.final_directory.filename().string()) ||
      paths.staging_directory.parent_path() != paths.replay_root ||
      paths.final_directory.parent_path() != paths.replay_root ||
      paths.staging_directory.filename() != expected_staging_name ||
      paths.event_path != paths.staging_directory / kEventFilename ||
      paths.snapshot_path != paths.staging_directory / kSnapshotFilename ||
      paths.manifest_path != paths.staging_directory / kManifestFilename ||
      paths.lock_path.parent_path() != paths.replay_root ||
      !valid_lock_name(paths.lock_path.filename().string()) || manifest_document.empty() ||
      manifest_document.size() > kMaximumManifestBytes) {
    return output_error("Replay publication paths or manifest bounds are inconsistent.");
  }
  const auto manifest_size = checked_integral_cast<std::streamsize>(manifest_document.size());
  if (!manifest_size) {
    return output_error("Replay manifest exceeds the supported write range.");
  }
  auto event_partial = paths.event_path;
  auto snapshot_partial = paths.snapshot_path;
  auto manifest_partial = paths.manifest_path;
  event_partial += ".partial";
  snapshot_partial += ".partial";
  manifest_partial += ".partial";

  std::error_code filesystem_error;
  const auto staging_status =
      std::filesystem::symlink_status(paths.staging_directory, filesystem_error);
  if (filesystem_error || !std::filesystem::is_directory(staging_status) ||
      !regular_file_without_symlink(event_partial, filesystem_error) ||
      !regular_file_without_symlink(snapshot_partial, filesystem_error) ||
      path_entry_exists(manifest_partial, filesystem_error) || filesystem_error ||
      path_entry_exists(paths.event_path, filesystem_error) || filesystem_error ||
      path_entry_exists(paths.snapshot_path, filesystem_error) || filesystem_error ||
      path_entry_exists(paths.manifest_path, filesystem_error) || filesystem_error ||
      path_entry_exists(paths.final_directory, filesystem_error) || filesystem_error) {
    return output_error("Replay publication targets are not fresh staged paths.");
  }

  std::ofstream manifest{manifest_partial, std::ios::binary};
  manifest.write(manifest_document.data(), *manifest_size);
  manifest.flush();
  manifest.close();
  if (manifest.fail()) {
    return write_error("Replay manifest partial file could not be written and closed.");
  }
  if (const auto lock_error = remove_exact_lock(paths.lock_path)) {
    return lock_error;
  }

  std::filesystem::rename(event_partial, paths.event_path, filesystem_error);
  if (filesystem_error) {
    return write_error("Event artefact could not be staged under its final basename.");
  }
  std::filesystem::rename(snapshot_partial, paths.snapshot_path, filesystem_error);
  if (filesystem_error) {
    return write_error("Snapshot artefact could not be staged under its final basename.");
  }
  std::filesystem::rename(manifest_partial, paths.manifest_path, filesystem_error);
  if (filesystem_error) {
    return write_error("Completed replay manifest could not be staged last.");
  }
  std::filesystem::remove(paths.staging_directory / "identity.sha256", filesystem_error);
  if (filesystem_error) {
    return write_error("Replay identity marker could not be removed before publication.");
  }
  std::filesystem::rename(paths.staging_directory, paths.final_directory, filesystem_error);
  if (filesystem_error) {
    return write_error("Completed replay directory could not be atomically published.");
  }
  return std::nullopt;
}

} // namespace itchlab
