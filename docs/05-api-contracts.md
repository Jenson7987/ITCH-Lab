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

The five version-1 command configs use JSON Schema draft 2020-12. Every object sets `additionalProperties` to false. Parsing rejects duplicate object names, invalid Unicode and non-finite numbers before schema validation. Structural failures use `ERR_CONFIG_SCHEMA`; semantic failures use the most specific stable code below. When several independent config failures can be reported safely, they are returned in ascending JSON-pointer order.

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

The implemented command accepts one or more configured symbols, strict or permissive mode, either
value of `require_trading_state` and either a null or expected input SHA-256. It resolves daily
identities from Stock Directory messages, assigns `SymbolId` values in requested-symbol order and
routes H/A/F/E/C/X/D/U/P/Q/B for selected locates. Selected events before `session_start_ns` warm
the book and are emitted with `in_session=false`; selected events at or after `session_end_ns` are
not applied or emitted. Global S events continue to be retained after that boundary so the
completed manifest has complete session metadata.

The production command writes a staged immutable replay directory beneath
`<output-root>/replay/`. Snapshot timestamps are limited to the configured half-open session.
Every changed in-session selected trading state emits a snapshot. Ordinary top-N changes and
configured P/Q trade snapshots additionally require the instrument to be trading when
`require_trading_state=true`. P/Q observations update the causal last-trade pair even when their
unchanged snapshot is disabled.

Before publication, the command verifies the source/executable/config identities, finalises both
binary headers, checks expected sizes/counts and hashes both children. The staging directory retains
a `.partial` suffix until a same-filesystem directory rename makes `events.ilb`, `snapshots.ilb` and
the completed manifest visible together. The read-only validate command below independently
rechecks published bytes.

    itchlab replay +      --config <replay-config.json> +      [--output-root <directory>] +      [--format human|json] +      [--log-format human|jsonl] +      [--quiet] +      [--force-new-run]

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
- Strict mode stops on the first decoder or book error. Permissive mode may skip only frame-local
  decoder errors (`ERR_UNKNOWN_MESSAGE`, `ERR_MESSAGE_LENGTH`, `ERR_TIMESTAMP` and decoder field
  `ERR_INVARIANT`) or atomic book rejections (`ERR_ORDER_REFERENCE` and `ERR_QUANTITY`). Outer
  framing, I/O, internal and final invariant errors remain fatal.
- Every policy error is counted by stable code. Each skipped message consumes one budget unit; the
  first otherwise-skippable error beyond the budget is fatal and is not counted as skipped.
- Replay progress is written only to stderr: first after five seconds, then after 30 seconds or ten
  million further messages, whichever comes first. `--log-format jsonl` emits one JSON object per
  update and `--quiet` suppresses progress.
- The first SIGINT requests cancellation at the next complete message boundary, closes streams and
  retains only a `.partial` staging directory/files before exit 130. A second SIGINT may terminate
  immediately.

Success artefacts:

- runs/replay/{replay-id}/replay-manifest.json
- runs/replay/{replay-id}/events.ilb
- runs/replay/{replay-id}/snapshots.ilb

Idempotency:

- If the same full identity digest already completed, return it after bounded manifest/file
  size-and-hash verification.
- `--force-new-run` creates a new timestamped run directory for the same content identity but never
  overwrites a completed directory.
- An existing conflicting partial directory fails with ERR_RUN_EXISTS.

### itchlab validate

Purpose: verify a replay or standalone interchange artefact.

    itchlab validate +      (--run <replay-directory> | --file <events-or-snapshots-file>) +      [--verify-source <path>] +      [--deep] +      [--format human|json]

Default checks headers, supported schema, declared sizes/counts and file hashes. Deep mode streams records, validates ordering/flags/depth and optionally reconstructs the final book/state digest.

Success result includes checks performed, records examined and hashes. It never repairs data.

Idempotency: read-only.

### itchlab benchmark

Purpose: benchmark parser, filtering and book stages using a pinned fixture.

    itchlab benchmark \
        --fixture <path> \
        --stage parser|filter|book|all \
        [--symbols <comma-separated>] \
        [--repetitions <integer>] \
        [--output <benchmark.json>] \
        [--format human|json]

Validation:

- The fixture must be a regular, valid uncompressed or gzip `itch-length-v1` stream.
- Release build is required for publishable results; non-Release results are labelled.
- repetitions defaults to 10 and must be 3–100.
- Existing final or `.partial` evidence paths are never replaced.
- Benchmark output includes the fixture hash, environment, samples, median, MAD, peak RSS and final
  state digest for semantic equivalence. A gzip input selects the PERF-005 full-pipeline measure;
  uncompressed `all` also includes the PERF-006 snapshot-writer measure.

## Python CLI

The console entry point may be invoked as python -m itchlab_research or itchlab-research.

### doctor

Purpose: validate an installed offline Python/native environment without reading market data or
using the network.

    itchlab-research doctor \
        [--binary <itchlab-path>] \
        [--format human|json] \
        [--ascii] \
        [--no-colour]

The command checks Python 3.11 or later; the installed runtime dependencies; all packaged version-1
config and manifest schemas; an executable `itchlab` whose exact semantic version matches the
Python package; and existing, non-symlink, narrow writable run and derived-data roots. `--binary`
overrides the default `PATH` lookup. `ITCHLAB_RUNS_DIR` defaults to `runs`, while the derived root
is `<ITCHLAB_DATA_DIR>/derived` with `ITCHLAB_DATA_DIR` defaulting to `data`.

All checks are bounded and accumulated so one invocation reports every safe diagnostic. Directory
writability uses a temporary probe that is removed before return. The command does not create a
missing root, inspect raw data, initiate research, contact the network or expose an absolute path in
its normal output. JSON uses schema version 1, command `doctor`, status `completed` or `failed`, a
platform summary, network status `not_required_or_tested`, and ordered checks containing `name`,
`status` and `summary`. Exit 0 means healthy, 7 means one or more health checks failed, 2 means
invalid usage and 70 means an unexpected internal failure.

### convert

    itchlab-research convert \
        --config <conversion-config.json> \
        [--allow-degraded] \
        [--force-new-run] \
        [--format human|json] \
        [--log-format human|jsonl] \
        [--quiet]

Conversion config v1:

    {
      "schema_version": 1,
      "replay_manifests": [
        "runs/replay/20190130-example/replay-manifest.json"
      ],
      "output_root": "runs",
      "parquet": {
        "compression": "zstd",
        "row_group_size": 65536,
        "partition_keys": ["trading_date", "symbol"]
      },
      "allow_degraded": false
    }

Config locators are non-empty safe relative paths, resolved from the command working directory;
absolute paths, traversal, symlinked output roots, partial components and source/output overlap are
rejected. Replay locators are unique. Parents must have unique trading dates and the same snapshot
depth. Version 1 fixes compression to `zstd`, partition keys to `trading_date,symbol`, and row-group
size to 1–1,048,576 rows.

Behaviour:

- Strictly validates each completed replay manifest, canonical lineage, child size/hash/header and
  symbol dictionary before creating an output root.
- Rejects a degraded parent by default. Config `allow_degraded=true` or CLI `--allow-degraded`
  accepts it and propagates a literal degraded status and warning.
- Reads authenticated binary records in bounded chunks and writes the documented integer/null
  schema to URI-safe Hive partitions. At most 32 Parquet files are open concurrently.
- Writes beneath `<output_root>/conversion/<conversion-id>.partial`, validates schemas, strict
  per-partition message-index order, row counts, sizes and hashes, writes the manifest, then uses a
  same-filesystem directory rename for atomic publication.
- The first SIGINT requests cancellation at a complete batch boundary, closes writers, retains only
  partial output and exits 130. A write failure cannot publish a completed directory.

Idempotency follows the stage identity in 04-data-model.md. A matching completed run is reused only
after its manifest lineage, Parquet schemas/order/counts and all child hashes are revalidated.
`--force-new-run` creates a new timestamped immutable directory with the same full identity. A
matching partial run or concurrent identity lock fails with `ERR_RUN_EXISTS`; completed runs are
never overwritten.

Success artefacts:

- `runs/conversion/<conversion-id>/conversion-manifest.json`
- `runs/conversion/<conversion-id>/events/trading_date=.../symbol=.../part-N.parquet`
- `runs/conversion/<conversion-id>/snapshots/trading_date=.../symbol=.../part-N.parquet`

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

- `runs/dataset/<dataset-id>/dataset-manifest.json`.
- Joined feature/label Parquet children beneath
  `dataset/partition=<train|validation|test>/trading_date=.../symbol=.../part-0.parquet`.
- Hashed `data-quality.json` and `feature-catalogue.json` supporting artefacts.

Behaviour:

- Accepts only safe relative completed conversion-manifest locators. It revalidates each conversion
  schema, count, child hash/size/order and authenticated replay lineage before data use; degraded
  conversions, missing days/symbols and insufficient snapshot depth fail before staging output.
- Computes feature and label batches independently for one complete day/symbol at a time and joins
  them only when trading date, symbol, message index, symbol ID, timestamp and qualifying ordinal
  agree exactly.
- The primary horizon is 100 qualifying rows and secondary horizons are 20 and 500. Labels compare
  future and current integer `mid2`; exact threshold equality is flat. Primary null tails are
  excluded while secondary null tails are retained.
- Applies disjoint filters in the fixed order `history_complete`, primary-label availability, then
  `qualifying_ordinal % row_stride == 0`. The stride never renumbers rows after an earlier filter.
- Requires sorted, non-overlapping chronological whole-day train, validation and test lists; every
  split must retain rows and the complete dataset must retain down, flat and up primary classes.
- Writes bounded Zstandard Parquet row groups below a run-owned `.partial` directory, rechecks all
  parent file identities, validates the strict dataset-manifest schema and publishes the manifest
  last with a same-filesystem atomic directory rename. SIGINT and write failure cannot publish a
  completed manifest.

Idempotency follows the canonical dataset config, ordered conversion-manifest hashes and Python
package-content digest. A matching run is reused only after its lineage, supporting documents,
Parquet schemas/order/counts/classes and all child hashes are revalidated. `--force-new-run` creates
a new immutable timestamped directory with the same full identity.

### train

    itchlab-research train --config <experiment-config.json> \
      [--force-new-run] \
      [--format human|json] \
      [--log-format human|jsonl] \
      [--quiet] [--ascii] [--no-colour]

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

The candidate arrays are the complete ordered version-1 grids, not arbitrary user extensions. Prior,
logistic regression and histogram gradient boosting are all required. Changing the grids or
preprocessing rules creates a new experiment contract rather than an unrecorded default.

The dataset locator must be safe and relative. Before fitting, the command validates the completed
dataset-manifest schema and identity, logical/physical Arrow schemas, every child path/hash/size/row
count, strict message-index order, finite-or-null features, primary-label domain and recorded class
counts. Training must contain all three classes. The loader exposes authenticated metadata; train
and validation rows load first, and test rows remain unloaded until both train-fitted candidate
families have been selected and frozen.

All feature imputation, scaling and symbol categories fit on training rows only. The prior uses
training class frequencies. The complete logistic and histogram-gradient-boosting grids are fitted
against the same training partition; failed candidates remain in validation metrics with stable,
payload-free reasons. Selection uses validation multiclass log loss and the documented 1e-6
tie-breaks. Each frozen selected estimator then produces test probabilities exactly once. No test
metric or test row changes model selection.

The command writes beneath `runs/experiment/<experiment-id>/`:

- experiment-manifest.json.
- metrics-validation.json and metrics-test.json.
- predictions.parquet.
- model-diagnostics.json containing coefficients, preprocessing statistics and safe fit metadata.

`predictions.parquet` contains validation and test rows for all three models using the Prediction
schema in 04-data-model.md. Metric documents contain aggregate and per-symbol class distributions,
log loss, balanced accuracy, fixed-three-class macro F1, fixed-order confusion matrices and ten-bin
one-vs-rest calibration. Test metrics add a seeded 1,000-repetition whole-day block bootstrap when
at least five days exist, or an explicit omission reason otherwise.

The experiment manifest uses `itchlab-experiment-v1` identity over the dataset-manifest hash,
locator-free experiment config, package-content digest and schema version. It authenticates all four
children and is published last by same-filesystem rename. A matching completed run is reused only
after lineage, hashes, JSON and prediction content are revalidated. `--force-new-run` creates a new
immutable timestamped directory for the same identity. Cancellation and failures leave only a
`.partial` directory and no completed manifest. Exit 8 covers model/dataset failures; graceful
cancellation exits 130.

Arbitrary pickle/joblib loading is prohibited, and version 1 writes no model object. Reproduction
retrains from the recorded config, seed, parent hash and package-content digest.

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
        "signal_weight_ticks": null,
        "max_signal_ticks": 2.0
      },
      "execution": {
        "passive_only": true,
        "submission_latency_ns": 100000,
        "cancellation_latency_ns": 100000,
        "maker_fee_microusd_per_share": -2000,
        "taker_fee_microusd_per_share": 3000,
        "queue_policy": "known_orders_conservative",
        "max_queue_anomalies": 0,
        "terminal_liquidation": "cross_visible_spread"
      },
      "seed": 7987
    }

The `inventory_aware_avellaneda_stoikov` strategy uses the same shape with `prediction_manifest`
set to null and `signal_weight_ticks` set to 0. The signal-adjusted strategy requires a non-null
experiment-manifest locator. A null signal weight requests validation-only selection from the
declared version-1 candidates; a numeric value is accepted only when it equals the independently
selected value and otherwise fails before test simulation. It chooses the minimum validation-log-
loss model family before signal-weight selection; values within 1e-6 tie in prior, logistic-
regression, gradient-boosting order. Both manifest locators must be safe relative paths without
partial components. `order_quantity` and `inventory_limit` are positive, inventory limit is at
least one order quantity, gamma/risk horizon/volatility window are positive, latency is 0 through
10 seconds in nanoseconds, each fee/rebate has absolute value at most 1,000,000 microusd per share,
and `max_queue_anomalies` is an explicit integer from 0 through 2^53−1 and participates in
canonical configuration identity. Version 1 requires passive-only execution,
`known_orders_conservative` queue policy and `cross_visible_spread` terminal liquidation.
Inconsistent visible-lifecycle events are diagnosed and skipped for simulated effects only while
they remain within that budget; the first excess aborts with `ERR_SIMULATION_ANOMALY`.

The bounded rule is
`r_signal = r + clip(w×score, −max_signal_ticks, max_signal_ticks)`. A missing prediction or one
whose age is strictly greater than
`max_prediction_age_ns` uses zero effective score and emits DIAG_MISSING_PREDICTION or
DIAG_STALE_PREDICTION. Equality at the age bound remains fresh. A zero signal weight bypasses the
prediction stream and emits baseline-equivalent economic decisions and order requests.

Signal-weight selection fixes symmetric latency at 100,000 ns, maker cost at −2,000
microusd/share and terminal taker cost at +3,000 microusd/share for every validation candidate.

Accounting starts from zero cash and zero per-symbol inventory. Before accepting a quote, the risk
gate projects a complete fill and suppresses it if the resulting symbol inventory would leave the
inclusive configured range. Every actual fill repeats that invariant. Gross fill cash excludes fees
and is `−side×price4×quantity×100`; signed fee is the configured per-share value times quantity;
net cash adds gross cash minus signed fee.

The current exact mark is the latest causal visible `mid2=best_bid_price4+best_ask_price4`.
Inventory value is `inventory×mid2×50` microusd. Passive spread capture, inventory mark-to-market,
terminal liquidation slippage and signed fees reconcile exactly to net marked P&L as specified in
04-data-model.md. All values and intermediate totals are checked signed int64; overflow is
`ERR_COST`, while a fill or proposal outside the inventory range is `ERR_INVENTORY_LIMIT`.

At session end every non-terminal order expires before publication. Long inventory sells at the
last valid visible bid and short inventory buys at the last valid visible ask; the configured taker
fee applies. Locked quotes are accepted. A crossed quote is `ERR_BOOK_CROSSED`, while missing or
invalid required terminal prices are `ERR_PRICE`. Flat days settle without a quote and emit valid
zero fill, quantity, cash, fee, inventory and P&L metrics.

The command always runs 0/100,000/1,000,000 ns symmetric latency crossed with −2,000/+3,000
microusd/share maker cost and 3,000 microusd/share taker cost on held-out test days. A distinct
configured latency/maker/taker-cost cell is added.
A signal run executes both the inventory-only control and selected signal strategy; a baseline-only
config remains valid but carries a prominent comparison warning.

Outputs:

- simulation-manifest.json.
- orders.parquet, fills.parquet, liquidations.parquet and equity.parquet.
- metrics.json and diagnostics.json.

The completed manifest retains the exact training dates, pooled and per-symbol calibration bucket
aggregates, fitted intercept/kappa values and whether each symbol used its own or the pooled fit.

Validation rejects marketable orders in passive-only mode, negative latency, invalid risk limits and prediction keys not present in the dataset.

### report

    itchlab-research report \
        --run-id <experiment-id> \
        [--output-format markdown|html|both] \
        [--format human|json]

The command accepts either a completed predictive experiment ID or completed simulation ID. A
simulation report combines upstream predictive evidence when present with strategy selection,
the complete sensitivity table, P&L decomposition, inventory, drawdown, turnover, 100 ms adverse
selection and prominent assumptions/anomalies/limitations. The default output format is
`markdown`; `html`
writes only HTML and `both` writes both human-readable forms. The command locates the input beneath
the appropriate completed run root and publishes a separate immutable bundle beneath
`runs/report/<run-id>/<output-format>/`, so it never modifies completed evidence.

The command authenticates the completed experiment, dataset and manifest lineage before rendering.
It writes the selected `report.md` and/or `report.html`, canonical reproduction-config snapshots,
machine-readable calibration plot data and static SVG plots. Every plot has labelled axes, alt text,
a caption and an adjacent textual summary. Reproduction commands contain only relative paths.
Publication uses a format-scoped partial directory and atomic rename. A byte-identical existing
bundle is reused; an inconsistent completed bundle or matching partial/lock fails without overwrite.

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
- `OrderExecutedWithPrice.execution_price4` is the execution price from C; it does not replace the
  display price established by the order's A/F message.
- `Trade.buy_sell_indicator` is the raw P-message field and is not an inferred aggressor side.
- Trade, CrossTrade and BrokenTrade are observations with no visible-book mutation interface.

```cpp
using BookMessage = std::variant<
  BookAdd, BookExecute, BookCancel, BookDelete, BookReplace>;

class OrderBook {
public:
  explicit OrderBook(StockLocate stock_locate);
  BookApplyResult apply(const BookMessage& message);
  TopLevels top_levels(std::uint16_t depth) const;
  std::optional<PriceLevelView> level(Side side, Price4 price4) const;
  BookDigest digest() const;
  InvariantReport check_invariants() const;
};
```

Contract:

- apply is atomic from the caller's perspective: on error, book state is unchanged.
- A/F create positive live orders; optional F attribution remains attached across replacement.
- E and C both apply BookExecute to the original display level. C execution-price/print fields stay
  on the decoded message and never reprice the visible order.
- E/C/X decrement positive quantities and remove the order and empty level when remaining reaches
  zero; D removes the complete remainder.
- U removes the original reference and creates the distinct new reference with its declared total
  quantity and display price. It retains side/attribution and joins the target FIFO at the U source
  MessageIndex.
- TopLevels contains explicit validity for unoccupied levels.
- level returns a read-only copy of aggregate quantity and FIFO order state.
- No mutable internal container is exposed.
- BookDigest is SHA-256 over canonical state beginning with the ASCII domain
  `itchlab-book-state-v1` and a NUL byte. The remaining fields are explicit big-endian integers:
  owning StockLocate as u16; then bid and ask sections identified by ASCII `B` and `S`. Each
  section contains a u64 level count and levels in best-to-worst order. Each level contains Price4
  u32, aggregate quantity u64, order count u64, then FIFO orders as reference u64, remaining
  quantity u64 and priority MessageIndex u64. Container capacities, iterators, addresses and hash
  bucket layout are excluded. Attribution is retained book state but is not part of this fixed
  version-1 digest byte contract.

```cpp
class ReplayEngine {
public:
  Result<ReplaySummary, ReplayError> run(
    ByteSource& source,
    const ReplayConfig& config,
    EventSink& events,
    SnapshotSink& snapshots,
    CancellationToken cancellation,
    ProgressReporter* progress);
};
```

Contract:

- Calls sinks in source order.
- Checks cancellation at complete framed-message boundaries.
- Reports observational progress without contributing wall-clock state to deterministic output.
- Does not publish final artefacts; publication belongs to the command coordinator.

## Python public service interfaces

    def read_events(
        path: Path,
        *,
        expected_sha256: str,
        chunk_records: int,
    ) -> Iterator[EventBatch]: ...
    def read_snapshots(
        path: Path,
        *,
        expected_sha256: str,
        chunk_records: int,
    ) -> Iterator[SnapshotBatch]: ...
    def validate_replay(manifest: Path, *, deep: bool = False) -> ValidationReport: ...
    def feature_schema(config: FeatureConfig) -> Schema: ...
    def feature_catalogue(config: FeatureConfig) -> tuple[FeatureDefinition, ...]: ...
    def build_feature_batches(
        events: Iterable[RecordBatch],
        snapshots: Iterable[RecordBatch],
        config: FeatureConfig,
        context: FeaturePartitionContext,
    ) -> Iterator[RecordBatch]: ...
    def label_schema(config: LabelConfig) -> Schema: ...
    def build_label_batches(
        snapshots: Iterable[RecordBatch],
        config: LabelConfig,
        context: FeaturePartitionContext,
    ) -> Iterator[RecordBatch]: ...
    def join_feature_label_batches(
        feature_batches: Iterable[RecordBatch],
        label_batches: Iterable[RecordBatch],
        feature_config: FeatureConfig,
        label_config: LabelConfig,
        sampling: SamplingConfig,
        partitions: PartitionConfig,
        expected_date: date,
        counts: PartitionJoinCounts,
    ) -> Iterator[RecordBatch]: ...
    def build_dataset(config: DatasetConfig, *, force_new_run: bool = False) -> DatasetResult: ...
    def train_baselines(dataset: PartitionedDataset, config: ExperimentConfig) -> ExperimentResult: ...
    def load_completed_dataset(
        dataset_manifest: str,
        *,
        base_directory: Path | None = None,
    ) -> PartitionedDataset: ...
    def load_completed_experiment(
        run_id: str,
        *,
        base_directory: Path | None = None,
    ) -> AuthenticatedExperiment: ...
    def generate_report(
        run_id: str,
        *,
        output_format: Literal["markdown", "html", "both"] = "markdown",
        base_directory: Path | None = None,
    ) -> ReportResult: ...
    def accounting_metrics(
        source: AccountingLedger | AccountingSnapshot,
    ) -> AccountingMetrics: ...
    def settle_session_end(
        state_machine: OrderStateMachine,
        ledger: AccountingLedger,
        *,
        session_end_timestamp_ns: int,
        last_quotes: Iterable[TerminalQuote],
        taker_fee_microusd_per_share: int,
    ) -> TerminalSettlement: ...
    class CausalIntensityCalibrator:
        def record_exposure(
            self, trading_date: date, symbol: str, distance_ticks: int, exposure_ns: int
        ) -> None: ...
        def record_execution(
            self, trading_date: date, symbol: str, distance_ticks: int, count: int = 1
        ) -> None: ...
        def finalise(self) -> IntensityCalibration: ...
    class InventoryAwareAvellanedaStoikov:
        def observe_quote(
            self,
            *,
            message_index: int,
            timestamp_ns: int,
            best_bid_price4: int,
            best_ask_price4: int,
        ) -> VolatilityEstimate | None: ...
        def decide(
            self, *, decision_message_index: int, timestamp_ns: int, inventory_shares: int
        ) -> BaselineDecision: ...
    class CausalPredictionJoin:
        def select(
            self, *, decision_message_index: int, timestamp_ns: int
        ) -> PredictionSelection: ...
    class SignalAdjustedAvellanedaStoikov:
        def observe_quote(
            self,
            *,
            message_index: int,
            timestamp_ns: int,
            best_bid_price4: int,
            best_ask_price4: int,
        ) -> VolatilityEstimate | None: ...
        def decide(
            self, *, decision_message_index: int, timestamp_ns: int, inventory_shares: int
        ) -> SignalAdjustedDecision: ...
    def select_signal_model(
        evaluations: Iterable[ModelValidationMetric],
    ) -> SignalModelSelection: ...
    def select_signal_weight(
        evaluations: Iterable[ValidationSignalPnl],
    ) -> SignalWeightSelection: ...
    def simulate(
        config: SimulationConfig,
        *,
        base_directory: Path | None = None,
        force_new_run: bool = False,
    ) -> SimulationResult: ...
    def load_completed_simulation(
        simulation_id: str,
        *,
        base_directory: Path | None = None,
    ) -> AuthenticatedSimulation: ...

Contracts:

- `expected_sha256` is the lowercase child hash authenticated by the owning completed manifest;
  standalone contract tests use the independently pinned golden hash. It is mandatory because a
  computed hash without a trusted expected value does not authenticate an artefact.
- Readers open one regular file descriptor, reject partial path components, validate the complete
  header/dictionary and exact declared size, hash the file incrementally and verify that the file
  identity remained stable before yielding a batch.
- Readers reject unsupported magic/version/header size/record size/depth/price scale, invalid dates,
  reserved header bits/bytes, placeholder config/source hashes and non-canonical dictionaries before
  yielding a batch.
- EventBatch and SnapshotBatch carry common immutable metadata plus a source-ordered tuple of frozen
  typed records. Python integers preserve unsigned binary values; trading dates use `datetime.date`,
  enum fields use string enums and invalid fields become `None` only through the documented validity
  bits. Snapshot depth slots remain fixed and independently nullable by side.
- Each complete chunk is checked before it is yielded. Record validation covers source ordering,
  kind/source consistency, required and reserved flags, canonical absent-field zeroes, quantities,
  timestamps, ASCII values and snapshot depth/state/trigger invariants. A later corrupt chunk may
  fail after earlier validated chunks have been consumed.
- `chunk_records` must be a positive integer and is an upper bound; the reader may split it further
  to keep an encoded batch within its fixed internal byte limit. A valid zero-record final artefact
  yields no batches.
- Expected file/domain failures raise `InterchangeReadError` with a stable `ErrorCode` and optional
  zero-based record index; messages omit raw payloads and absolute paths.
- Readers use explicit little-endian field decoding and bounded reads. They do not use native struct
  packing, mmap, pickle, joblib, eval or exec and perform no network or filesystem writes.
- `load_completed_dataset` authenticates a completed dataset directly for read-only downstream
  consumers. `load_completed_experiment` validates the strict experiment manifest, its dataset and every
  declared experiment artefact before returning reporting evidence; content changes during
  validation fail with a stable hash error.
- `generate_report` additionally authenticates conversion and replay manifest lineage, escapes all
  data-derived presentation fields, rejects private absolute paths and publishes through a
  format-scoped partial directory and atomic rename. It reuses only a byte-identical completed
  report bundle and never modifies completed experiment or simulation evidence.
- Feature calculation operates on one `(trading_date, symbol)` partition at a time. Context supplies
  the authenticated replay session bounds, configured tick size and expected day-local symbol ID.
- Event and snapshot batches must use the documented conversion schemas and strict message-index
  order. The feature engine consumes no event with an index after the current decision index and
  retains only bounded event/clock-window state.
- The feature catalogue and Arrow schema are deterministic functions of the validated version-1
  feature config. Warm-up nulls and intentional semantic nulls are distinguished by catalogue null
  policy plus the row's `history_complete` metadata.
- Label functions are isolated so their future access cannot leak into feature expressions. Their
  bounded future buffer advances only on qualifying snapshots and resets for every day/symbol.
- Dataset publication requires exact feature/label immutable-key agreement, chronological disjoint
  whole-day splits and reconciled row/class/label-availability counts. Expected dataset failures
  raise `DatasetBuildError` with a stable `ErrorCode` and no raw payload or absolute path.
- PartitionedDataset exposes test rows for final evaluation, not training selection.
- `simulate` authenticates dataset/experiment/conversion/replay lineage, materialises only
  authorised validation predictions before selection, freezes model and signal weight, then opens
  test events/predictions and publishes the required immutable scenario grid manifest-last.
  `load_completed_simulation` revalidates config/parent identity, selection/calibration scope,
  output schemas and every child size/hash/count before returning report evidence.
- Simulation performs a causal as-of selection of the latest same-symbol prediction at or before each decision and records that prediction's exact immutable row identity.
- Accounting consumes queue fills in lifecycle order, validates their order/event identity, assigns
  deterministic fill IDs and leaves its prior snapshot unchanged on any validation, limit or
  arithmetic failure. Terminal settlement preflights every required quote and monetary operation
  before expiring orders or committing the flattened ledger.
- Intensity calibration accepts only its declared configured symbols and sorted unique training
  dates, stores exact nanosecond exposure and execution counts in the fixed 0-through-10 distance
  buckets, and rejects later partitions with `ERR_LEAKAGE_GUARD`. Finalisation requires a finite
  positive pooled fit; an invalid symbol fit records and uses that pooled estimate. Invalid records
  do not mutate prior aggregates.
- One baseline strategy instance owns one trading-date/symbol causal volatility window. Quote
  observations must remain source ordered, and a decision cannot precede the latest observed key.
  The first observation yields no variance estimate; once positive time has elapsed, zero variance
  is valid. Decisions expose the equation inputs/results, exact Price4 proposals and stable
  side-specific suppression reasons. Price-grid/passivity failures suppress only the affected side,
  while the existing inventory risk gate independently suppresses a projected limit breach.
- One causal prediction join owns one authenticated trading-date/symbol/model stream. It requires
  strictly increasing prediction message indices and non-decreasing dataset timestamps, selects the
  latest row at or before a non-decreasing decision key, retains its complete immutable key and
  holds only one future-key lookahead in normal operation. Invalid input leaves its semantic cursor
  unchanged. Missing/stale rows are diagnostics; malformed scores or scope mismatches are errors.
- Signal model selection requires exactly one finite non-negative validation log loss for every
  required model family. Signal-weight selection requires all four candidates over identical
  validation-day sets under the fixed 100 microsecond latency and −2000 microusd/share maker-cost
  plus +3000 microusd/share terminal-taker-cost scenario. It compares exact rational day means and
  treats a difference of at most one microusd as a tie. Train/test-labelled, duplicate, incomplete
  or wrong-scenario evidence is rejected.
- The signal-adjusted strategy delegates volatility, half-spread, outward tick rounding, passivity
  and projected inventory checks to the inventory-aware baseline. It changes only the reservation
  price through the clipped configured score rule; weight zero does not advance its prediction
  stream.

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

Non-fatal diagnostic codes start with DIAG\_; DIAG_MISSING_PREDICTION and
DIAG_STALE_PREDICTION record zero-signal fallbacks without changing command success by themselves.

## File-level idempotency and concurrency

- One process owns a run directory while its lock file exists.
- Lock creation must be atomic.
- A stale lock is never removed automatically solely by age; the user invokes an inspect/cleanup action.
- Final files are immutable.
- Simultaneous runs with different IDs are allowed.
- The MVP provides no record pagination or rate limiting; chunk size bounds memory when reading files.
