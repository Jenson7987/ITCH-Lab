# 05 — Module, command and file contracts

## Scope

The MVP has no network API. Authentication, HTTP methods, routes, pagination and rate limiting are therefore not applicable. This document defines the equivalent public contracts:

- C++ module interfaces.
- C++ and Python command-line commands.
- Configuration and result envelopes.
- Cross-language file contracts.

All commands must support --help and --version. Long-form option names are stable within a major version.

## Common command contract

### Output channels

- stdout: requested result, table or JSON only.
- stderr: progress, warnings and human errors.
- --format json produces one valid JSON result object on stdout.
- --log-format jsonl produces structured logs on stderr.
- --quiet suppresses non-error progress, not final requested output.

### Common result envelope

    {
      "schema_version": 1,
      "command": "replay",
      "status": "completed",
      "run_id": "20260802T120000Z-a1b2c3d4e5f6",
      "summary": {},
      "warnings": []
    }

### Common error envelope

    {
      "schema_version": 1,
      "command": "replay",
      "status": "failed",
      "error": {
        "code": "ERR_MESSAGE_LENGTH",
        "message": "Message A has length 35; expected 36.",
        "context": {
          "message_index": 42,
          "source_offset": 1872,
          "source_type": "A"
        },
        "action": "Verify the source file and framing."
      }
    }

Raw message bytes and stack traces are absent unless an explicit debug flag is used.

### Process exit codes

| Code | Meaning |
| ---: | --- |
| 0 | Success, including a valid empty replay |
| 2 | Usage, configuration or schema error |
| 3 | Input path/compression/framing error |
| 4 | ITCH decode error |
| 5 | Book/domain invariant error |
| 6 | Output or filesystem error |
| 7 | Artefact validation or unsupported schema |
| 8 | Dataset/model/report error |
| 9 | Simulation error |
| 70 | Unexpected internal failure |
| 130 | Graceful cancellation after SIGINT |

### Configuration validation and hashing

The four version-1 command configs use JSON Schema draft 2020-12. Every object sets `additionalProperties` to false. Parsing rejects duplicate object names, invalid Unicode and non-finite numbers before schema validation. Structural failures use `ERR_CONFIG_SCHEMA`; semantic failures use the most specific stable code below. When several independent config failures can be reported safely, they are returned in ascending JSON-pointer order.

Domain validation follows schema validation and enforces relationships that JSON Schema does not express portably, including sorted unique horizons/windows, symbol-to-tick-map equality, chronological non-overlapping dates and strategy-to-prediction requirements. JSON integer fields are limited to the RFC 8785/I-JSON exact range even when the corresponding in-memory type is wider; numeric seeds are therefore 0 through 9,007,199,254,740,991.

`config_sha256` hashes the complete canonical effective config. `identity_config_sha256` hashes the locator-free identity projection defined in 04-data-model.md. Both are lowercase SHA-256 hexadecimal strings; C++ and Python must match the committed golden vectors.

## C++ CLI

### itchlab inspect

Purpose: qualify input, list message composition and inspect directory/timestamps without publishing derived data.

    itchlab inspect +      --input <path> +      [--limit <positive-integer>|--all] +      [--symbols <comma-separated>] +      [--mode strict|permissive] +      [--format human|json]

Validation:

- Exactly one input is required.
- limit and all are mutually exclusive; default limit is 1,000,000 messages.
- Requested symbols are exact case-sensitive source symbols after normalisation to uppercase input.

Success summary fields:

- compression, framing, source_size_bytes.
- messages_examined and counts_by_type.
- first_timestamp_ns and last_timestamp_ns.
- stock_directory_count and requested_symbols_found.
- parse_errors_by_code.

Idempotency: read-only and idempotent.

### itchlab replay

Purpose: create normalised events, snapshots and a completed replay manifest.

    itchlab replay +      --config <replay-config.json> +      [--output-root <directory>] +      [--format human|json] +      [--force-new-run]

Replay config v1:

    {
      "schema_version": 1,
      "input": {
        "path": "data/raw/01302019.NASDAQ_ITCH50.gz",
        "sha256": null,
        "trading_date": "2019-01-30",
        "exchange_timezone": "America/New_York"
      },
      "selection": {
        "symbols": ["AAPL", "MSFT", "AMZN"],
        "session_start_ns": 34200000000000,
        "session_end_ns": 57600000000000,
        "require_trading_state": true
      },
      "output": {
        "depth": 10,
        "emit_unchanged_trade_snapshots": true
      },
      "validation": {
        "mode": "strict",
        "max_skipped_messages": 0,
        "invariant_interval": 1
      }
    }

Semantic rules:

- SHA-256 may be null only before the first run; the completed manifest always contains the computed value.
- Symbols are unique and non-empty.
- Session is half-open [start, end).
- invariant_interval 1 means after every selected mutation; 0 is invalid.
- In strict mode max_skipped_messages must be 0; permissive mode may use a non-negative explicit budget.

Success artefacts:

- runs/replay/{replay-id}/replay-manifest.json
- runs/replay/{replay-id}/events.ilb
- runs/replay/{replay-id}/snapshots.ilb

Idempotency:

- If the same full identity digest already completed, return the existing run after validation.
- force-new-run creates a new timestamped identity but never overwrites the completed directory.
- An existing conflicting partial directory fails with ERR_RUN_EXISTS.

### itchlab validate

Purpose: verify a replay or standalone interchange artefact.

    itchlab validate +      (--run <replay-directory> | --file <events-or-snapshots-file>) +      [--verify-source <path>] +      [--deep] +      [--format human|json]

Default checks headers, supported schema, declared sizes/counts and file hashes. Deep mode streams records, validates ordering/flags/depth and optionally reconstructs the final book/state digest.

Success result includes checks performed, records examined and hashes. It never repairs data.

Idempotency: read-only.

### itchlab benchmark

Purpose: benchmark parser, filtering and book stages using a pinned fixture.

    itchlab benchmark +      --fixture <path> +      --stage parser|filter|book|all +      [--symbols <comma-separated>] +      [--repetitions <integer>] +      [--output <benchmark.json>]

Validation:

- Release build is required for publishable results.
- repetitions defaults to 10 and must be 3–100.
- Benchmark output includes final state digest for semantic equivalence.

## Python CLI

The console entry point may be invoked as python -m itchlab_research or itchlab-research.

### convert

    itchlab-research convert +      --config <dataset-conversion.json> +      [--allow-degraded]

Config fields:

- replay_manifests: non-empty list of completed replay manifests.
- output_root: relative or CLI-resolved path.
- parquet: compression (zstd recommended), row_group_size and partition keys.

Behaviour:

- Validates all parent artefacts first.
- Reads binary records in bounded chunks.
- Writes event and snapshot Parquet datasets to partial directories.
- Publishes conversion-manifest.json last.

### build-dataset

    itchlab-research build-dataset --config <dataset-config.json>

Required config shape:

    {
      "schema_version": 1,
      "conversion_manifests": ["runs/conversion/.../conversion-manifest.json"],
      "symbols": ["AAPL", "MSFT", "AMZN"],
      "tick_size4_by_symbol": {
        "AAPL": 100,
        "MSFT": 100,
        "AMZN": 100
      },
      "features": {
        "depth_levels": [1, 5, 10],
        "event_windows": [20, 100, 500],
        "clock_windows_ns": [100000000, 1000000000]
      },
      "labels": {
        "primary_event_horizon": 100,
        "secondary_event_horizons": [20, 500],
        "flat_threshold_ticks": 0
      },
      "sampling": {
        "row_stride": 10
      },
      "partitions": {
        "train_dates": ["2019-01-30"],
        "validation_dates": ["2019-03-27"],
        "test_dates": ["2019-07-30"]
      }
    }

The displayed dates are examples, not confirmed study dates.

Output:

- dataset-manifest.json.
- features and labels Parquet partitions.
- data-quality.json and feature-catalogue.json.

Idempotency follows immutable run identity.

### train

    itchlab-research train --config <experiment-config.json>

Experiment config v1:

    {
      "schema_version": 1,
      "dataset_manifest": "runs/dataset/.../dataset-manifest.json",
      "models": {
        "prior": {
          "enabled": true
        },
        "logistic_regression": {
          "c_values": [0.01, 0.1, 1.0, 10.0],
          "penalty": "l2",
          "solver": "lbfgs",
          "max_iter": 2000
        },
        "hist_gradient_boosting": {
          "learning_rates": [0.05, 0.1],
          "max_leaf_nodes": [15, 31],
          "l2_regularization": [0.0, 1.0],
          "max_iter": 100
        }
      },
      "preprocessing": {
        "continuous_imputation": "median",
        "standardise_logistic": true,
        "standardise_hist_gradient_boosting": false,
        "unknown_symbol": "all_zero"
      },
      "selection_metric": "multiclass_log_loss",
      "seed": 7987
    }

The candidate arrays are the complete ordered version-1 grids, not arbitrary user extensions. Prior, logistic regression and histogram gradient boosting are all required. Changing the grids or preprocessing rules creates a new experiment contract rather than an unrecorded default.

The command must not accept test dates different from the dataset manifest. It writes:

- experiment-manifest.json.
- metrics-validation.json and metrics-test.json.
- predictions.parquet.
- coefficients/features or safe model diagnostics.

Arbitrary pickle/joblib input loading is prohibited. If a library-native model artefact is retained, it is output-only and labelled trusted-local; reproduction retrains from config.

### simulate

    itchlab-research simulate --config <simulation-config.json>

Simulation config v1:

    {
      "schema_version": 1,
      "dataset_manifest": "runs/dataset/.../dataset-manifest.json",
      "prediction_manifest": "runs/experiment/.../experiment-manifest.json",
      "strategy": {
        "name": "signal_adjusted_avellaneda_stoikov",
        "decision_interval_ns": 100000000,
        "max_prediction_age_ns": 500000000,
        "order_quantity": 100,
        "inventory_limit": 1000,
        "gamma": 0.1,
        "volatility_window_ns": 60000000000,
        "risk_horizon_seconds": 10,
        "signal_weight_ticks": 1.0,
        "max_signal_ticks": 2.0
      },
      "execution": {
        "passive_only": true,
        "submission_latency_ns": 100000,
        "cancellation_latency_ns": 100000,
        "maker_fee_microusd_per_share": -2000,
        "taker_fee_microusd_per_share": 3000,
        "queue_policy": "known_orders_conservative",
        "terminal_liquidation": "cross_visible_spread"
      },
      "seed": 7987
    }

The `inventory_aware_avellaneda_stoikov` strategy uses the same shape with `prediction_manifest` set to null and `signal_weight_ticks` set to 0. The signal-adjusted strategy requires a non-null prediction manifest and a signal weight from the declared version-1 candidate set. `order_quantity` and `inventory_limit` are positive, inventory limit is at least one order quantity, gamma/risk horizon/volatility window are positive, latency is 0 through 10 seconds in nanoseconds, and each fee/rebate has absolute value at most 1,000,000 microusd per share. Version 1 requires passive-only execution, `known_orders_conservative` queue policy and `cross_visible_spread` terminal liquidation.

Outputs:

- simulation-manifest.json.
- orders.parquet, fills.parquet and equity.parquet.
- metrics.json and diagnostics.json.

Validation rejects marketable orders in passive-only mode, negative latency, invalid risk limits and prediction keys not present in the dataset.

### report

    itchlab-research report +      --run-id <simulation-or-experiment-id> +      [--output-format markdown|html|both]

The report command reads completed manifests only. It writes report.md, optional report.html, plot data and static plots. It includes exact reproduction commands with relative paths.

## C++ public module interfaces

The signatures below are normative shapes; namespaces and minor value-category details may change without altering behaviour.

```cpp
struct Frame {
  MessageIndex message_index;
  std::uint64_t source_offset;
  std::span<const std::byte> payload;
};

struct ReadResult {
  std::size_t bytes_read;
  bool end_of_file;
  std::optional<SourceError> error;
};

class ByteSource {
public:
  virtual ReadResult read(std::span<std::byte> destination) = 0;
  virtual SourceProgress progress() const noexcept = 0;
  virtual ~ByteSource() = default;
};

struct FrameReadResult {
  std::optional<Frame> frame;
  std::optional<FrameError> error;
};

class FramedMessageReader {
public:
  FrameReadResult next();
};
```

Contract:

- next returns null optional only at a clean frame boundary at EOF.
- A zero or over-512 length returns ERR_FRAMING; an incomplete prefix or payload returns
  ERR_TRUNCATED_MESSAGE.
- Returned payload remains valid until the next call.
- Frame length is capped at 512 payload bytes before payload access or allocation.
- source_offset is the zero-based position of the frame-length prefix in the uncompressed stream.
- gzip EOF is clean only after the gzip member trailer has been validated.
- File/gzip opening and source reads return typed errors; expected input failures do not throw.

```cpp
using ItchMessage = std::variant<
  SystemEvent, StockDirectory, TradingAction,
  AddOrder, AddOrderWithAttribution,
  OrderExecuted, OrderExecutedWithPrice,
  OrderCancel, OrderDelete, OrderReplace,
  Trade, CrossTrade, BrokenTrade>;

class ItchDecoder {
public:
  Result<ItchMessage, DecodeError> decode(std::span<const std::byte> payload) const;
};
```

Contract:

- Decoder has no mutable global state.
- Exact type length is checked before field access.
- Unknown messages return ERR_UNKNOWN_MESSAGE with observed type/length.

```cpp
class OrderBook {
public:
  Result<BookDelta, BookError> apply(const BookMessage& message);
  TopLevels top_levels(std::uint16_t depth) const;
  BookDigest digest() const;
  InvariantReport check_invariants() const;
};
```

Contract:

- apply is atomic from the caller's perspective: on error, book state is unchanged.
- TopLevels contains explicit validity for unoccupied levels.
- No mutable internal container is exposed.

```cpp
class ReplayEngine {
public:
  Result<ReplaySummary, ReplayError> run(
    ByteSource& source,
    const ReplayConfig& config,
    EventSink& events,
    SnapshotSink& snapshots,
    CancellationToken cancellation);
};
```

Contract:

- Calls sinks in source order.
- Checks cancellation at bounded message intervals.
- Does not publish final artefacts; publication belongs to the command coordinator.

## Python public service interfaces

    def read_events(path: Path, *, chunk_records: int) -> Iterator[EventBatch]: ...
    def read_snapshots(path: Path, *, chunk_records: int) -> Iterator[SnapshotBatch]: ...
    def validate_replay(manifest: Path, *, deep: bool = False) -> ValidationReport: ...
    def build_features(snapshots: LazyFrame, config: FeatureConfig) -> LazyFrame: ...
    def build_labels(snapshots: LazyFrame, config: LabelConfig) -> LazyFrame: ...
    def create_partitions(frame: LazyFrame, config: PartitionConfig) -> PartitionedDataset: ...
    def train_baselines(dataset: PartitionedDataset, config: ExperimentConfig) -> ExperimentResult: ...
    def simulate(inputs: SimulationInputs, config: SimulationConfig) -> SimulationResult: ...

Contracts:

- Readers reject unsupported magic/version/record size before yielding a batch.
- Feature functions may inspect current/past rows only.
- Label functions are isolated so their future access cannot leak into feature expressions.
- PartitionedDataset exposes test rows for final evaluation, not training selection.
- Simulation performs a causal as-of selection of the latest same-symbol prediction at or before each decision and records that prediction's exact immutable row identity.

## Error codes

Stable public codes include:

- ERR_INPUT_PATH, ERR_UNSUPPORTED_COMPRESSION, ERR_FRAMING, ERR_TRUNCATED_MESSAGE, ERR_EMPTY_INPUT.
- ERR_MESSAGE_LENGTH, ERR_UNKNOWN_MESSAGE, ERR_TIMESTAMP.
- ERR_UNKNOWN_SYMBOL, ERR_TRADING_DATE, ERR_ORDER_REFERENCE, ERR_QUANTITY, ERR_PRICE, ERR_BOOK_CROSSED, ERR_INVARIANT.
- ERR_OUTPUT_PATH, ERR_DISK_WRITE, ERR_HASH_MISMATCH, ERR_SCHEMA_VERSION, ERR_PARTIAL_ARTEFACT.
- ERR_CONFIG_SCHEMA, ERR_SESSION_WINDOW, ERR_TIMEZONE, ERR_DEPTH, ERR_HORIZON, ERR_PARTITION, ERR_ROW_STRIDE, ERR_SEED, ERR_EMPTY_DATASET.
- ERR_LEAKAGE_GUARD, ERR_MODEL_TRAINING, ERR_PREDICTION_KEY.
- ERR_LATENCY, ERR_COST, ERR_QUEUE_STATE, ERR_INVENTORY_LIMIT, ERR_SIMULATION_ANOMALY, ERR_BROKEN_SIM_FILL.
- ERR_RUN_EXISTS, ERR_CANCELLED and ERR_INTERNAL.

New codes may be added within a major version. Existing meanings must not change silently.

Non-fatal diagnostic codes start with DIAG\_; DIAG_STALE_PREDICTION records signal fallback without changing command success by itself.

## File-level idempotency and concurrency

- One process owns a run directory while its lock file exists.
- Lock creation must be atomic.
- A stale lock is never removed automatically solely by age; the user invokes an inspect/cleanup action.
- Final files are immutable.
- Simultaneous runs with different IDs are allowed.
- The MVP provides no record pagination or rate limiting; chunk size bounds memory when reading files.
