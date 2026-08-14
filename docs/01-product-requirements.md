# 01 — Product requirements

## Requirement classification

High-level capabilities selected with the ITCH-Lab concept are marked **Confirmed requirement**. Exact mechanisms and thresholds introduced by this specification are marked **Recommendation** unless they are unavoidable consequences of a confirmed capability.

## Functional requirements

| ID | Classification | Requirement | Acceptance criteria |
| --- | --- | --- | --- |
| FR-001 | Confirmed requirement | Inspect an ITCH source without performing a full replay. | The inspect command reports compression, framing status, detected message types, first/last decoded timestamps, stock-directory count and bounded parse errors; limit and symbol filters work; the command does not create derived data. |
| FR-002 | Confirmed requirement | Decode the MVP ITCH 5.0 message set. | Synthetic byte fixtures for S, R, H, A, F, E, C, X, D, U, P, Q and B decode to the exact expected typed fields; invalid lengths return a typed error before field access; integers and Price4 values match the specification. |
| FR-003 | Confirmed requirement | Filter replay by configured instruments and session window. | Symbols are resolved from Stock Directory messages; only selected stock-locate codes enter the instrument pipeline; selected pre-session messages are applied and emitted as warm-up events so session-start book/queue state is correct; events carry an in-session flag; snapshots and research rows are limited to the half-open configured interval; processing may stop after session end. |
| FR-004 | Confirmed requirement | Reconstruct the visible level-3 book. | Adds create unique live orders; executions/cancels reduce remaining shares; deletes remove orders; replacements remove the old reference and add the new reference at new priority; a golden lifecycle fixture produces the expected FIFO queues and quantities after every event. |
| FR-005 | Confirmed requirement | Maintain aggregated depth and top-N views. | Aggregated quantities equal the sum of live orders at each price; best bid/ask and configured top-N levels update after every mutation; empty levels are absent; prices remain integer Price4 in the C++ domain. |
| FR-006 | Recommendation | Provide strict and permissive error policies. | Strict mode stops at the first malformed or inconsistent message with source offset and message index; permissive mode skips only errors classified as safely skippable, counts them by code, applies a configurable maximum, and marks output as degraded. |
| FR-007 | Confirmed requirement | Write normalised selected-symbol lifecycle and trading-state events. | Each output event contains schema version, source message index, timestamp, symbol identity, event kind, relevant order/match identifiers, side, Price4, quantities and event subtype where applicable; records preserve input order; a reader round-trip test is lossless for the documented fields. |
| FR-008 | Confirmed requirement | Write top-N book snapshots. | A snapshot is emitted after every selected-symbol event that changes the exported top-N state, every selected trading-state change, and configured trade events; unused depth slots use documented validity flags rather than sentinel prices; duplicate unchanged snapshots are otherwise not emitted by default. |
| FR-009 | Recommendation | Write a run manifest for every replay. | The manifest records source SHA-256, source size, trading date, configuration and its hash, schema versions, code revision, compiler/build mode, start/end time, record counts, error counts, selected symbols and completion status. |
| FR-010 | Confirmed requirement | Convert versioned binary output to research tables. | The Python conversion command validates schema and hashes, writes typed Parquet event and snapshot tables, preserves integer prices, and rejects unsupported schema versions without partially publishing final output. |
| FR-011 | Confirmed requirement | Generate causal microstructure features. | The dataset includes spread, top-level imbalance, multi-level imbalance, microprice displacement, order-flow imbalance, recent event rates, realised short-window volatility and trade direction where observable; every row uses only information available at or before its decision index. |
| FR-012 | Confirmed requirement | Generate labels and chronological partitions. | The primary label is three-class mid-price direction after a configured number of future qualifying updates; unavailable tail labels are null and excluded; complete days are assigned to non-overlapping train, validation and test partitions in chronological order. |
| FR-013 | Confirmed requirement | Train and evaluate required predictive baselines. | A majority/prior baseline, multinomial logistic regression and histogram gradient boosting run from one config; preprocessing fits on training data only; validation selects declared hyperparameters; test metrics are computed once after selection and include class distribution, log loss, balanced accuracy, macro F1 and confusion matrix. |
| FR-014 | Confirmed requirement | Simulate historical passive-order lifecycles. | The simulator supports pending-submit, active, partially filled, filled, cancel-pending, cancelled, expired, rejected and counterfactually invalidated states; transitions follow event/message order and configured latency; every fill references the market event that caused it. |
| FR-015 | Confirmed requirement | Model visible queue position and partial fills. | A new passive order joins behind visible orders at its effective timestamp; known ahead-order executions, deletions, cancellations and replacements adjust queue state according to the selected policy; fills cannot exceed remaining simulated quantity or observed executable quantity. |
| FR-016 | Confirmed requirement | Apply latency, fees and risk constraints. | Submission/cancellation latency is non-negative and applied before an action affects the simulation; signed maker fees/rebates enter cash P&L; inventory limits prevent risk-increasing quotes; results are produced for at least three latency and two cost configurations. |
| FR-017 | Confirmed requirement | Implement an inventory-aware market-making baseline. | The strategy derives reservation price and quote distance from declared volatility, risk-aversion and arrival-intensity estimates; quotes are rounded to valid ticks; parameters and calibration windows are recorded; a deterministic fixture verifies quote direction changes with inventory. |
| FR-018 | Confirmed requirement | Implement one signal-adjusted strategy. | The selected model score changes quote placement through a documented bounded rule; setting signal weight to zero is equivalent to the inventory-aware baseline; no test-set metric is used to tune signal weight. |
| FR-019 | Confirmed requirement | Produce a reproducible research report. | The report contains data lineage, dataset sizes, split dates, feature definitions, model metrics, calibration, simulation assumptions, latency/cost sensitivity, inventory/P&L/risk metrics, negative results, limitations and commands needed to reproduce the run. |
| FR-020 | Confirmed requirement | Benchmark and profile the performance core. | Release-build benchmarks report parser-only and parser-plus-book throughput, peak resident memory, input mode and hardware; at least one before/after optimisation is supported by profiler evidence and regression tests. |
| FR-021 | Recommendation | Use immutable, content-addressed run identities. | The run ID contains a UTC timestamp plus the first 12 hexadecimal characters of the canonical config-and-input hash; an existing completed run is never overwritten without an explicit force flag; force creates a new run ID. |
| FR-022 | Recommendation | Validate completed artefacts. | The validate command checks manifest completeness, file sizes and hashes, supported schemas, monotonic message indices/timestamps within each source, record counts and declared completion; it exits non-zero with stable error codes on failure. |

## Non-functional requirements

| ID | Classification | Requirement | Verification |
| --- | --- | --- | --- |
| NFR-001 | Confirmed requirement | Determinism | Identical input bytes, canonical config, code revision and build mode produce byte-identical normalised records and semantically identical reports; timestamps describing wall-clock run time are isolated in manifests. |
| NFR-002 | Confirmed requirement | Bounded streaming memory | Raw files are never fully decompressed or loaded into memory; working memory grows with live selected-symbol orders and configured buffers, not source-file length; a large synthetic stream demonstrates stable memory after warm-up. |
| NFR-003 | Recommendation | Measured performance | Target at least 1,000,000 uncompressed messages/second for parser-plus-book replay of the benchmark fixture on the M2 Pro release build; failure to hit the target blocks optimisation sign-off until the bottleneck and revised target are documented in an ADR. |
| NFR-004 | Recommendation | Data integrity | Final artefacts are written to temporary paths, flushed, hashed and atomically renamed; interrupted outputs retain a partial suffix and are not accepted by downstream commands. |
| NFR-005 | Recommendation | Portability | The core builds and tests on current macOS ARM64 and Ubuntu x86-64; code does not depend on host struct packing or host endianness. |
| NFR-006 | Recommendation | Reproducibility | Dependencies are version constrained, configs are versioned, random seeds are explicit, source hashes and Git revision are recorded, and a clean-machine synthetic run is documented. |
| NFR-007 | Recommendation | Observability | Long commands emit structured progress to stderr at bounded intervals and a final summary; machine-readable stdout modes remain free of progress text. |
| NFR-008 | Confirmed requirement | Safe handling of untrusted input | Lengths and arithmetic are validated before allocation/access; sanitizers and fuzz tests find no crash or out-of-bounds access over the maintained corpus. |
| NFR-009 | Recommendation | Test quality | Critical parser, book and simulator domain logic has at least 90% line and 85% branch coverage; other project-owned code has at least 80% line coverage; coverage is evidence, not a replacement for scenario tests. |
| NFR-010 | Confirmed requirement | Research integrity | No random row split, future-derived feature, silent test-set tuning or unrealistic immediate-fill assumption is permitted; deviations require an ADR and prominent report disclosure. |
| NFR-011 | Recommendation | Accessible outputs | CLI and reports do not communicate status by colour alone; colour can be disabled; tables have text equivalents; plots use distinguishable palettes, labels and adjacent textual summaries. |
| NFR-012 | Recommendation | Maintainability | Public module interfaces are documented; complex algorithms cite their source or derivation; accepted ADRs govern architecture; unrelated abstractions are not added pre-emptively. |

## Quantitative definitions

These definitions are **recommendations** that remove ambiguity from FR-011 through FR-018. Changing one after test results have been viewed creates a new experiment family and must be documented.

### Sampling and prices

- The dataset config supplies tick_size4_by_symbol. For the initial selected US equities above USD 1, the expected value is 100, representing USD 0.01. Source prices are not rejected merely because an execution price is off this grid.
- Let b_t and a_t be best bid/ask Price4 after qualifying snapshot t, and let B_t(d) and A_t(d) be summed bid/ask quantities across the best d valid levels.
- Define mid2_t = b_t + a_t. This exact integer represents twice the mid-price and avoids half-tick floating values.
- A qualifying research row is an in-session snapshot with top_n_changed=true, trading_state=trading and valid bid/ask. Rolling research windows reset at session start; pre-session events warm book/queue state but do not enter feature windows.
- The primary row cadence is every qualifying top-N change, not every raw ITCH message or trade-only snapshot.
- Features and labels are calculated on every qualifying row, then the model dataset retains every tenth qualifying row per day/symbol starting with ordinal 0. This deterministic row_stride=10 is the MVP default; synthetic tests may use 1.

### Required feature formulae

For each qualifying row:

- `spread_ticks = (a_t − b_t) / tick_size4`.
- `imbalance_d = (B_t(d) − A_t(d)) / (B_t(d) + A_t(d))` for d in {1, 5, 10};
  absent depth slots contribute zero and the result is null only when the denominator is zero.
- `microprice4 = (a_t × B_t(1) + b_t × A_t(1)) / (B_t(1) + A_t(1))`.
- `microprice_displacement_ticks = (microprice4 − mid2_t / 2) / tick_size4`.
- Top-of-book order-flow increment:

  e_t = I[b_t ≥ b_(t−1)]B_t(1) − I[b_t ≤ b_(t−1)]B_(t−1)(1)
        − I[a_t ≤ a_(t−1)]A_t(1) + I[a_t ≥ a_(t−1)]A_(t−1)(1).

- `ofi_W` sums the W increments ending at t. Because e_0 has no predecessor, it first becomes
  valid at qualifying ordinal W. `ofi_normalised_W` divides that sum by the corresponding trailing
  sum of `B(1)+A(1)`, returning null only for a zero denominator.
- Event rates count add, cancel/delete and execution events by resting side in the half-open clock
  window `(t−window, t]`, divided by window seconds. Events at the decision timestamp are eligible
  only when their message index is at or before the feature-row message index. Required clock
  windows are 100 ms and 1 s. Version 1 counts `add`, `cancel`/`delete` and
  `execute`/`execute_price` respectively; `replace`, P, Q and B are not silently reclassified.
- `realised_volatility_W` is `sqrt(sum(log(m_j/m_(j−1))²))` over the W returns ending
  at t, where `m_j = mid2_j / (2×10000)`. It is not annualised and first becomes valid at
  qualifying ordinal W.
- session_progress is (timestamp_ns−session_start_ns)/(session_end_ns−session_start_ns), clipped to [0,1]; session_progress_squared captures intraday curvature.
- For E/C events that exactly trigger the qualifying snapshot, `aggressor_sign` is the negative of
  the known resting-order side; it is null on other feature rows. P and Q messages do not provide
  dependable aggressor direction and are excluded from signed-flow features.
  `execution_imbalance_W` uses E/C executions in
  `(message_index_(t−W), message_index_t]`: signed executed quantity divided by total eligible
  executed quantity. It first becomes valid at qualifying ordinal W and is zero when the complete
  window contains no eligible execution.
- A B broken-trade message never rewrites an earlier feature row or restores the historical book.
  At break time, `execution_imbalance_W` removes the referenced E/C match while that original
  execution remains in the causal event window; displayed-book execution-rate features retain the
  original E/C mutation. Unknown, P/Q or already broken matches do not create signed flow.

Required event windows are 20, 100 and 500 qualifying transitions. Clock windows are incomplete
until that much session time has elapsed. Infinite values are invalid. The feature output retains
all qualifying rows with individual warm-up values set to null and an explicit `history_complete`
flag; TASK-019 counts and excludes rows without every required history window before sampling.

### Labels

- The primary horizon H is 100 future qualifying rows; secondary reported horizons are 20 and 500.
- For the same day and symbol, compare mid2_(t+H) with mid2_t.
- With flat_threshold_ticks k, label up when the difference is greater than 2×tick_size4×k, down when it is less than the negative threshold, and flat otherwise. MVP default k is 0.
- Rows without H future qualifying rows before session end have a null label and are excluded.
- Labels are computed in a separate module and joined to past-only features by trading date, symbol and message index.

### Model selection

- Prior baseline predicts training-set class frequencies for every row.
- The MVP fits one pooled model across all selected symbols, one-hot encodes the source symbol string (never the day-local SymbolId), and reports both aggregate and per-symbol metrics. Leave-one-symbol-out evaluation is post-MVP.
- Logistic regression uses training-only median imputation and standardisation for continuous fields, one-hot symbol encoding, L2 penalty, lbfgs, max_iter=2000 and C in {0.01, 0.1, 1, 10}.
- Histogram gradient boosting uses training-only median imputation and dense one-hot symbol encoding without continuous-field standardisation, max_iter=100, learning_rate in {0.05, 0.1}, max_leaf_nodes in {15, 31} and l2_regularization in {0, 1}. Unknown validation/test symbols encode as all-zero categorical columns.
- Select minimum validation multiclass log loss. Logistic ties within 1e-6 choose smaller C. Gradient-boosting ties choose smaller max_leaf_nodes, then larger l2_regularization, then lower learning_rate.
- Freeze the selected candidate before the one final test evaluation.
- The simulator signal score is P(up) − P(down), necessarily in [−1, 1].

## Simulation definitions

### Decision and event ordering

- Default decision interval is 100 ms. At the first market event after each elapsed grid boundary, the strategy observes the already-applied current state and uses the latest prediction for the same symbol whose message index is at or before the decision event. If several boundaries pass without an event, they coalesce into one decision.
- A decision never uses a later prediction. The exact prediction row key used is written to the simulated-order record.
- A prediction older than max_prediction_age_ns=500,000,000 is stale; the signal component becomes zero and a diagnostic is counted, while the inventory-only baseline may continue.
- Submission/cancellation effective at the same timestamp as one or more source events is applied after all source messages at that timestamp. This conservative tie-break allows those events to occur before the action.
- Effective actions at the same timestamp are applied in request order. A cancellation requested
  while submission is pending still observes cancellation latency: it cancels before activation
  only when its effective time is earlier; if submission becomes effective first, the order is
  exposed in cancel-pending state until cancellation becomes effective.
- The MVP permits at most one live or pending order per symbol/side. A replacement waits until the prior cancellation becomes effective.

### Queue and fills

- At activation, a passive order joins behind every then-visible order at the same side/price.
- Executions reduce exact known ahead orders. Cancellation, deletion and replacement reduce queue ahead only when the affected reference is known to be ahead; a replacement's new order joins behind the simulated order when at the same price.
- Adds after activation are behind the simulated order and do not increase queue ahead.
- Hidden P trades and cross Q trades do not fill a simulated displayed order.
- An E/C execution at the simulated order's resting side/price first consumes known queue ahead; remaining eligible quantity may fill the simulated order at its limit price.
- If the historical book path would cross through an active simulated limit without eligible displayed execution sufficient to fill it, the order becomes counterfactually invalidated with no assumed fill; the scenario records a diagnostic.
- A B message that references a market match used to cause a simulated fill aborts that scenario with ERR_BROKEN_SIM_FILL; the MVP does not invent order reinstatement after a trade break. Other B messages are recorded but do not mutate queue/book state.
- Version 1 configures an explicit non-negative max_queue_anomalies budget. Only inconsistent known
  visible lifecycle events consume it; expected counterfactual invalidations and unrelated broken-
  trade observations remain separately counted diagnostics. The checked-in strict example uses 0.
- Market impact, hidden priority and other venues are unobserved and disclosed, never filled optimistically.

### Inventory-aware baseline

Use prices in ticks and inventory in order-size units:

- s is current mid-price in ticks.
- q = inventory_shares / configured_order_quantity.
- sigma_squared is the causal trailing sum of squared mid-price changes in ticks divided by elapsed seconds over a default 60-second window.
- tau = min(configured risk_horizon_seconds, seconds until session end), with a default risk horizon of 10 seconds.
- gamma is positive risk aversion in inverse tick per inventory unit.
- kappa is positive execution-intensity decay per tick.
- Reservation price: r = s − q × gamma × sigma_squared × tau.
- Optimal half-spread approximation: delta = gamma × sigma_squared × tau / 2 + log(1 + gamma/kappa) / gamma.
- Desired bid is floor(r−delta) ticks and ask is ceil(r+delta) ticks, then constrained to remain passive at or behind the current best prices.

Calibrate kappa using training days only. For resting-distance buckets delta_ticks=0…10, measure visible-level exposure seconds and E/C execution counts. Use smoothed intensity lambda_delta=(count+1)/(exposure_seconds+1), then weighted least-squares fit log(lambda_delta)=intercept−kappa×delta_ticks with exposure as weight. A non-positive/invalid symbol estimate falls back to a declared training-only pooled estimate; absence of a valid pooled estimate prevents that strategy run.

### Signal adjustment and accounting

- Signal-adjusted reservation price is r_signal = r + clip(w×score, −max_signal_ticks, max_signal_ticks).
- Candidate w values are {0, 0.5, 1, 2} ticks and are selected on validation simulation only; ties choose the smaller absolute value. Default max_signal_ticks is 2.
- The main baseline fixes gamma=0.1, risk_horizon_seconds=10, order_quantity=100 and inventory_limit=1000 before test evaluation. Optional gamma sensitivity does not replace the main result.
- Select w by highest mean validation-day net P&L under the default 100 microsecond latency and −2000 microusd/share maker-rebate scenario; a tie within one microusd chooses the smaller w. The selected w is then frozen for every test scenario.
- Setting w=0 must reproduce baseline decisions and results byte-for-byte except strategy name metadata.
- Default order quantity is 100 shares. Buy quotes are suppressed at the upper inventory limit and sell quotes at the lower limit.
- Fee/rebate is signed integer microusd per filled share. Trade cash before fees is −side×Price4×quantity×100 microusd.
- Required test scenarios use equal submission/cancellation latency of 0, 100,000 and 1,000,000 ns crossed with maker cost/rebate of −2000 and +3000 microusd/share. The manifest also records a 3000 microusd/share taker cost for terminal liquidation.
- Session-end open orders expire. Remaining inventory is liquidated against the last valid visible opposite best price, charged the configured taker cost, and reported separately.

## User roles and permissions

The MVP has no authentication or network service.

| Capability | Developer | Researcher | Reviewer |
| --- | --- | --- | --- |
| Read source and derived data | Local filesystem permission | Local filesystem permission | Local filesystem permission |
| Run replay/conversion | Yes | Yes | Optional |
| Change code/config | Yes | Yes | No by convention |
| Run experiments/simulation | Optional | Yes | Reproduce only |
| Publish artefacts | Repository-owner decision | Repository-owner decision | No |

Operating-system filesystem permissions are the only enforcement layer. Adding multi-user access is out of scope.

## User stories

### US-001 — Inspect a source file

As a developer, I want to inspect framing and message composition before a long replay so that invalid input fails early.

Acceptance criteria:

- Given a valid synthetic source, inspect returns exit code 0 and the exact expected counts.
- Given a truncated payload, inspect reports ERR_TRUNCATED_MESSAGE with byte offset and returns non-zero in strict mode.
- Supplying limit 0 is rejected during config validation.
- No data file is written.

### US-002 — Replay selected instruments

As a researcher, I want to replay only selected symbols so that full-day files remain manageable.

Acceptance criteria:

- Symbols are resolved only after Stock Directory messages.
- An unknown requested symbol fails before final outputs are published.
- Normalised events contain no unrequested instrument.
- Global system events needed for session state remain available in the manifest.

### US-003 — Verify book correctness

As a developer, I want invariant checks and a deterministic state digest so that optimisations cannot silently corrupt the book.

Acceptance criteria:

- The golden fixture validates remaining quantities, order priority and top levels after every mutation.
- Missing, duplicate or over-decremented order references produce stable error codes.
- Debug validation can run after every mutation; release validation can run at a configured sampling interval.
- The final state digest is stable across supported platforms.

### US-004 — Build a research dataset

As a researcher, I want causal features and labels with day-level splits so that model results are not caused by leakage.

Acceptance criteria:

- Each feature has a definition, type and lookback window in dataset metadata.
- A leakage guard test perturbs future records without changing earlier feature rows.
- No trading day appears in more than one partition.
- Rows lacking sufficient history or future label horizon are explicitly counted and removed.

### US-005 — Compare predictive baselines

As a reviewer, I want simple baselines before complex models so that claimed improvements have context.

Acceptance criteria:

- All models use identical frozen partitions.
- Training-only transformations are asserted by tests.
- Test output includes the prior baseline and confidence intervals produced by day-block bootstrap where at least five days are available.
- The report retains an unfavourable result rather than suppressing it.

### US-006 — Run a conservative execution simulation

As a researcher, I want latency- and queue-aware fills so that forecasts are not converted directly into impossible P&L.

Acceptance criteria:

- A simulated order cannot fill before its activation time.
- A cancellation does not take effect before cancellation latency.
- Queue-ahead quantity never becomes negative.
- A zero-liquidity or crossed-data anomaly prevents a fill and creates a diagnostic.
- P&L decomposes into spread capture, mark-to-market, fees/rebates and terminal liquidation.

### US-007 — Reproduce a run

As a reviewer, I want one command and a manifest so that I can verify the published result.

Acceptance criteria:

- The report states the exact config, source hashes and Git revision.
- Validate detects any changed output byte.
- Re-running with the same inputs reproduces normalised data and metrics within declared floating-point tolerance.
- Missing raw data produces an actionable message without attempting an unauthorised download.

### US-008 — Interrupt a long replay safely

As a developer, I want Ctrl-C to leave no apparently complete dataset so that downstream analysis cannot consume corrupt output.

Acceptance criteria:

- The first interrupt requests graceful cancellation; a second may terminate immediately.
- Graceful cancellation closes handles, records cancelled status where possible and leaves partial-suffixed files.
- Final paths and completed manifests are not published.
- Downstream commands reject partial files.

### US-009 — Benchmark an optimisation

As a developer, I want repeatable benchmarks and a state digest so that speed improvements preserve behaviour.

Acceptance criteria:

- The benchmark pins its fixture, config, build type, compiler and hardware description.
- Before/after results include repeated samples and median throughput.
- State digests and correctness tests match.
- The optimisation and trade-off are documented in the relevant ADR or performance note.

## Validation rules

| Input | Rule | Error |
| --- | --- | --- |
| Source path | Must resolve to a readable regular file; output must not alias it | ERR_INPUT_PATH |
| Compression | gzip or uncompressed framing; unsupported formats fail explicitly | ERR_UNSUPPORTED_COMPRESSION |
| Message framing | Outer frame is 1–512 bytes; boundary EOF is clean, while zero, oversized and partial frames fail before decoding | ERR_FRAMING or ERR_TRUNCATED_MESSAGE |
| Message length | A safely framed known type must equal its exact specified length before field access | ERR_MESSAGE_LENGTH |
| Unknown message | Strict mode fails; permissive mode may skip using the outer length frame and count it | ERR_UNKNOWN_MESSAGE |
| Symbol | Must exactly match a symbol announced by Stock Directory after trimming right padding | ERR_UNKNOWN_SYMBOL |
| Trading date | ISO 8601 date supplied by config; filename inference may suggest but never silently confirm it | ERR_TRADING_DATE |
| Exchange timezone | Must be the IANA identifier America/New_York for the Nasdaq MVP | ERR_TIMEZONE |
| Session | Start is less than end; both are within 00:00:00–24:00:00 exchange-local time | ERR_SESSION_WINDOW |
| Depth | Integer from 1 to 50; MVP default 10 | ERR_DEPTH |
| Price | Unsigned Price4; conversion to decimal is presentation-only | ERR_PRICE |
| Quantity | Positive on add/replace; decrement must not exceed remaining shares | ERR_QUANTITY |
| Order reference | Unique while live; referenced mutations require a live order | ERR_ORDER_REFERENCE |
| Horizon | Positive integer qualifying-event count; values unique and sorted | ERR_HORIZON |
| Row stride | Positive integer; deterministic ordinal sampling resets per day/symbol | ERR_ROW_STRIDE |
| Partition | Complete days, chronological, non-empty and non-overlapping | ERR_PARTITION |
| Latency | Integer nanoseconds from 0 through 10 seconds | ERR_LATENCY |
| Fee/rebate | Signed integer microusd per share with absolute value at most 1,000,000 | ERR_COST |
| Queue anomaly budget | Integer from 0 through 2^53−1 | ERR_QUEUE_STATE |
| Random seed | Unsigned 64-bit integer recorded in the manifest | ERR_SEED |
| Existing run | Completed directory is immutable unless a new run ID is requested | ERR_RUN_EXISTS |

## Error conditions and recovery

Errors use stable codes, a human message and structured context. Paths, source offsets and message indices may be included; raw payloads are excluded from normal logs.

| Category | Default behaviour | Recovery |
| --- | --- | --- |
| Config/schema error | Fail before reading large data | Correct config and rerun |
| Input corruption | Strict failure; partial output not published | Verify source hash or use a different authorised source |
| Book inconsistency | Strict failure | Inspect event context; permissive mode is for diagnosis only |
| Disk full/write failure | Stop, close files, preserve partial suffix | Free space and rerun; resume is not MVP |
| Unsupported output schema | Downstream command refuses input | Use matching code revision or explicit migration |
| Numerical/model failure | Mark experiment failed and retain diagnostics | Correct data/config; create a new run |
| Simulation anomaly | Skip affected action, count diagnostic; abort if configured maximum exceeded | Inspect event and sensitivity settings |

## Empty, progress and loading states

- Inspecting a file with no decodable messages returns ERR_EMPTY_INPUT.
- A valid session with zero selected events completes replay but marks the dataset empty; training and simulation then fail with ERR_EMPTY_DATASET.
- Long-running commands print a first progress update after five seconds and then at least every 30 seconds or ten million messages, whichever comes first.
- Non-interactive output contains newline-delimited progress rather than terminal animations.
- Progress includes messages processed, source bytes, selected events, elapsed time and current error count; it must not estimate completion when compressed total work is unknown.

## Offline and degraded behaviour

- After authorised data is present locally, the complete MVP works without network access.
- The application never silently downloads market data or dependencies at runtime.
- Permissive decoding creates degraded output that training/simulation reject by default; an explicit allow-degraded flag is required and is recorded in the report.
- Missing optional plotting support still permits machine-readable metrics and Markdown tables.

## Accessibility requirements

- All CLI functions are available without colour or pointer interaction.
- Help text uses plain language, stable option names and examples.
- Errors include suggested corrective action.
- Generated HTML uses semantic headings/tables, keyboard-accessible links and alt text.
- Plots include axis labels, units, legends and adjacent textual summaries.
- Colour palettes must remain distinguishable under common red-green colour-vision deficiency simulations.

## Privacy and data-governance requirements

- The MVP processes no personal data and requires no account credentials.
- Raw ITCH files and bulk derived datasets are ignored by Git.
- Raw data is obtained directly by the user under the source's applicable terms and is not redistributed by the project.
- Synthetic fixtures must not be presented as real market events.
- Logs omit raw payload bytes and do not upload telemetry.
- Run manifests may record relative paths and hashes; published manifests must remove user-specific absolute paths.

## Explicitly out of scope

- Authentication, authorisation servers and user administration.
- Brokerage/exchange credentials, live order placement and portfolio advice.
- A promise of profitable performance.
- Tick-by-tick activity outside Nasdaq's visible feed.
- Hidden-liquidity inference as ground truth.
- Distributed processing and cloud deployment.
- A graphical or web frontend.
- Automatic resumability after interrupted binary output.
