# 08 — Testing strategy

## Objectives

Testing must provide evidence for:

- Safe, specification-correct decoding of untrusted binary messages.
- Deterministic level-3 state and output serialisation.
- Causal dataset construction and frozen chronological evaluation.
- Valid simulated-order state, queue and accounting.
- Reproducible end-to-end runs.
- Measured performance without trading correctness for speed.

Coverage percentages are secondary to domain scenarios, invariants and independently calculated expected outputs.

## Test layers

| Layer | Scope | Typical speed | Runs |
| --- | --- | --- | --- |
| C++ unit | Decode helpers, message decoders, book mutations, serializers | Milliseconds | Every change |
| Python unit | Config, features, labels, splits, metrics, strategy equations | Milliseconds–seconds | Every change |
| Property/fuzz | Frames/messages, order lifecycles, simulator state | Bounded CI plus longer local/scheduled | Every PR/scheduled |
| Integration | Reader→decoder→book→writer; binary→Parquet; model/simulator modules | Seconds | Every PR |
| Contract/golden | Binary schemas, manifests, CLI JSON and stable errors | Seconds | Every PR |
| End-to-end | Synthetic source through final report | Under five minutes target | Every PR |
| Performance | Release microbenchmarks and representative replay | Seconds–minutes | PR smoke; scheduled/publish manually |
| Official-data validation | Selected full-day source runs | Hours/storage dependent | Local before published result |

## Unit-testing approach

### C++ decoder

For each MVP message type S, R, H, A, F, E, C, X, D, U, P, Q and B:

- One exact byte fixture with independently specified expected fields.
- Wrong lengths from zero through expected−1 and expected+1 where framing permits.
- Minimum and maximum integer values.
- Six-byte timestamp and big-endian boundary cases.
- Space-padded symbol/attribution handling.
- Exact length and timestamp validation for spec-known ignored Y/L/V/W/K/I/N/J/h messages.
- Unknown type behaviour in strict/permissive policy.

Implemented decoder coverage:

| Type | Payload bytes | Typed message | Executable evidence |
| --- | ---: | --- | --- |
| S | 12 | SystemEvent | UT-DEC-001/002, IT-001/002 |
| R | 39 | StockDirectory | UT-DEC-001/002, IT-001/002 |
| H | 25 | TradingAction | UT-DEC-001/002, TASK-009 mixed-stream golden |
| A | 36 | AddOrder | UT-DEC-001/002, IT-001/002 |
| F | 40 | AddOrderWithAttribution | UT-DEC-001/002, TASK-009 mixed-stream golden |
| E | 31 | OrderExecuted | UT-DEC-001/002, TASK-009 mixed-stream golden |
| C | 36 | OrderExecutedWithPrice | UT-DEC-001/002, TASK-009 mixed-stream golden |
| X | 23 | OrderCancel | UT-DEC-001/002, TASK-009 mixed-stream golden |
| D | 19 | OrderDelete | UT-DEC-001/002, IT-001/002 |
| U | 35 | OrderReplace | UT-DEC-001/002, TASK-009 mixed-stream golden |
| P | 44 | Trade | UT-DEC-001/002, TASK-009 mixed-stream golden |
| Q | 40 | CrossTrade | UT-DEC-001/002, TASK-009 mixed-stream golden |
| B | 19 | BrokenTrade | UT-DEC-001/002, TASK-009 mixed-stream golden |
| Y/L/V/W/K/I/N/J/h | 20/26/35/12/28/50/20/35/21 | IgnoredMessage | TASK-031 decoder boundary and strict replay integration |

### C++ order book

- Add first order, add at existing level and add better/worse level.
- Partial/full execution and cancellation.
- Explicit deletion.
- Replacement to same/different price/quantity and new priority.
- Multiple levels and bid/ask ordering.
- Duplicate add, missing mutation, over-decrement and overflow.
- Atomic state unchanged after rejected mutation.
- Digest stable regardless of unordered-map bucket layout.

Implemented lifecycle evidence includes UT-BOOK-001 for partial/full E/C/X and D, UT-BOOK-002 for
same/different-price replacement and retained priority fields, UT-BOOK-004 for atomic quantity
errors, a deterministic full-lifecycle reference-model property test, and IT-003's 14-state
plain/gzip golden trace. Every committed invalid-lifecycle fixture checks its documented error,
unchanged digest and valid post-rejection invariants.

### C++ directory, session and replay filtering

- Directory unit tests fix `SymbolId` assignment to requested order, accept exact repeats and reject
  contradictory locate, symbol and metadata mappings without partial mutation.
- Session unit tests cover the global O/S/Q/M/E/C sequence, H/P/Q/T instrument states, default halt
  at system open, end-of-market close and invalid transitions.
- The independent `synthetic_session` plain/gzip fixture selects MSFT and AAPL while filtering AMZN;
  it includes pre-session warm-up, a half-open boundary, halt-time book/trade activity and resume.
- TASK-011 integration tests assert no unrequested or out-of-session snapshot, mandatory halt/resume
  snapshots, optional halt-time snapshot gating, requested-order identities, full global metadata
  and exact all/selected/category count reconciliation.
- The `synthetic_mixed` integration test routes every supported selected-instrument source type
  through the coordinator, while the CLI test proves byte-identical plain/gzip diagnostics.

### Serialisation/manifests

- Header offsets, record sizes and little-endian bytes.
- Every nullable field validity combination.
- Symbol dictionary order.
- Record-count header finalisation.
- Partial publication and hash mismatch.
- Unknown schema/flag rejection.

TASK-013 fixes event-v1 with an independently generated 856-byte synthetic golden containing the
104-byte header, two requested-order dictionary entries and all ten 72-byte event kinds. Unit tests
cover every validity bit, valid numeric zeroes, reserved bytes, checked 32-bit remaining quantity,
monotonic ordering and injected reservation/record/seek/patch/flush/close failures. A mixed-stream
integration test routes all selected events through the real replay coordinator and confirms exact
source message-index order while leaving only `events.ilb.partial`.

TASK-014 adds an independently generated 344-byte snapshot-v1 golden with depth two, paired
bid/ask validity, nullable trigger/last-trade fields and exact state/top-change flag bytes. IT-004
publishes a full replay directory, rehashes both children, checks manifest lineage/counts/build
metadata, proves identity reuse/forced immutability and recursively rejects private path leakage.
CT-JSON-001 validates the strict completed-manifest schema and rejects unknown keys.

### Python datasets/models

- TASK-017 IT-007 converts the independent event-v1/snapshot-v1 golden values and checks exact
  Arrow dtypes, nulls, integer zeroes, URI-safe partition paths, per-symbol order, row-group bounds,
  per-partition counts and the strict conversion-manifest schema.
- Conversion boundary tests cover authenticated parent tampering, degraded-policy propagation,
  multiple days, inconsistent snapshot depth, unsafe/source-overlapping output roots, verified
  identity reuse and immutable forced runs. Forging either a completed manifest count or a Parquet
  child plus its declared hash is rejected before reuse.
- A 120,000-record synthetic interchange stream stays below a 128 MiB traced-Python allocation peak
  and 256 MiB process peak-RSS growth while preserving configured row-group bounds. Injected writer
  failure and service cancellation leave only partial output; a real subprocess SIGINT exits 130
  without a completed manifest.
- TASK-019 UT-LABEL-001 fixes exact integer threshold semantics, batch-boundary horizons and null
  tails. Partition properties reject overlap, disorder and immutable-key mismatch, and prove that
  stride sampling uses the original qualifying ordinal after the disjoint history/tail filters.
- IT-008 publishes three complete synthetic day partitions with independently pinned retained-row,
  class and horizon-availability counts. It validates the strict manifest/child hashes and covers
  reuse, forced immutability, parent/output tampering, missing days, unsafe paths, cancellation and
  injected write failure.
- Price4 conversions avoid binary-float values until presentation.
- TASK-018 hand-calculated cases reconcile the exact catalogue/schema, current depth and
  microprice values, all 20/100/500 qualifying-transition boundaries, 100 ms/1 s event-rate
  boundaries, realised volatility, observable E/C direction and causal B corrections.
- UT-FEAT-001 mutates later event/snapshot input and proves the complete earlier Arrow prefix is
  byte-for-byte equivalent. Equal-timestamp events after the decision index are also excluded.
- Feature boundary tests reject schema/depth, partition/order, session-context, price and quantity
  violations with stable typed errors, and prove non-qualifying snapshots do not advance rolling
  windows.
- Labels for up/flat/down and unavailable tails.
- Whole-day partition overlap rejection.
- Imputer/scaler fit calls receive training partition only.
- Metrics on hand-calculated confusion matrices.

### Simulator/strategies

- Every allowed/forbidden state transition.
- Submission/cancellation latency boundaries.
- Queue insertion and exact known-ahead removal.
- Partial fills and no overfill.
- Fill-before-cancel race.
- Counterfactual invalidation when the historical book crosses an unfilled simulated limit.
- Inventory/cash/fee accounting.
- Inventory-limit quote suppression.
- End-of-session expiry/liquidation.
- Reservation price moves opposite inventory risk.
- Signal weight zero equals baseline.

TASK-023's UT-SIM-002 hand trace fixes the exact initial reference set, E/C/X/D/U depletion,
replacement priority reset and two bounded fills. Deterministic generated quantities assert that
current queue equals the exact ahead-reference sum and that cumulative/event fill limits hold.
The IT-010 subset additionally covers equal-timestamp activation, C display-price semantics,
P/Q exclusion, fill-before-cancel, counterfactual invalidation, broken fills and the exact queue
anomaly budget.

TASK-024's UT-SIM-004 hand trace independently reconciles a rebated buy, a rebated partial sell,
an intervening exact-midpoint revaluation and taker-cost terminal liquidation. Deterministic
generated side/quantity cases assert cash-plus-inventory-mark and P&L-component identities. Boundary
tests cover projected quote suppression, fill-limit defence, signed fee/rebate direction, cash/fee/
mark overflow with atomic state, fill ordering, long/short liquidation, missing/locked/crossed
terminal quotes, session-end expiry, per-symbol isolation and flat zero-fill metrics.

TASK-025's UT-STRAT-001 decision table fixes flat, long and short inventory equation inputs,
reservation prices, half-spreads and outward-rounded passive Price4 proposals. Calibration tests
independently calculate the smoothed intensities and exposure-weighted regression, exercise valid
symbol and pooled fallback estimates, reject non-training observations and fail when no valid
pooled estimate exists. Clock-window tests cover first-observation suppression, zero variance,
half-open expiry, equal-timestamp source order and future-prefix invariance. Parameter and price
boundaries cover non-finite values, atomic invalid observations, crossed/locked/off-grid books,
uint32 quote bounds and both projected inventory-limit suppressions.

TASK-026's UT-STRAT-002 compares the zero-weight signal path with the exact baseline economic
decision and order-request projection while a fail-on-read prediction stream proves it is not
consumed. The controlled signal golden covers positive/negative clipping, sub-tick adjustment,
missing and stale fallbacks and exact prediction-key propagation. Join tests cover exact-index and
as-of selection, inclusive age bounds, one-row future lookahead, future-score prefix invariance,
duplicate/out-of-order/scope/score/timestamp rejection and atomic semantic state. Validation-only
selection tests cover complete model and weight catalogues, the fixed selection scenario, exact
day means, both tie rules and rejection of test-labelled evidence.

## Property-based testing

Recommended frameworks: RapidCheck or a small deterministic generator for C++; Hypothesis for Python.

Properties:

1. Applying a generated valid lifecycle never produces negative remaining quantity.
2. Aggregated level quantity always equals live-order sum.
3. Removing all generated orders leaves an empty book.
4. Encoding then decoding a normalised record preserves all valid fields.
5. A simulator order's cumulative fills never exceed original quantity.
6. Inventory equals signed cumulative fills plus explicit liquidation.
7. Cash and inventory accounting reconcile to marked P&L.
8. Increasing latency cannot cause a fill before the lower-latency order's own activation; aggregate fill monotonicity is not assumed.
9. Reordering future rows cannot change past features.
10. Partition sets remain disjoint for arbitrary valid date lists.

Each failing generated case is reduced and committed as a regression fixture where legally safe.

## Integration testing

| ID | Scenario | Expected evidence |
| --- | --- | --- |
| IT-001 | Uncompressed fixture through reader, then decoder from TASK-005 | TASK-004 proves exact frames/offsets; decoder tests add typed sequence |
| IT-002 | gzip fixture through reader, then decoder from TASK-005 | TASK-004 proves identical framed payload digest; decoder tests add semantic digest |
| IT-003 | Full order lifecycle through reader/decoder/book | Exact 14-state plain/gzip golden trace |
| IT-004 | Replay through event/snapshot writers | Exact headers, counts, records and hashes |
| IT-005 | Interrupt replay during write | Partial suffix only; no completed manifest |
| IT-006 | C++ binary artefacts into Python readers | Exact round-trip typed records |
| IT-007 | Conversion to Parquet | Golden dtypes, nulls, integer values, partition paths, sort order and validated manifest match |
| IT-008 | Feature/label pipeline | Three complete synthetic days publish exact joined rows, disjoint drop/class/horizon counts, frozen splits and authenticated lineage; future mutation guards pass |
| IT-009 | Training baselines | Authenticated frozen data selects on validation, loads/evaluates test once, publishes required schemas/metrics without model serialisation, reuses valid output and leaves cancellation partial |
| IT-010 | Event/prediction stream through simulator | Golden orders, fills, cash, inventory, exact 3×2 grid, validation-only selection and immutable hashes |
| IT-011 | Report generation | Predictive and combined simulation headings, sensitivity/metric tables, limitations and reproduction commands |
| IT-012 | Hash tampering | Downstream validation fails before data use |

## End-to-end testing

### E2E-001 synthetic happy path

Input: generated framed ITCH day containing system events, three instruments, adds, partial/full executions, cancels, replacements, trades, a halt/resume and close.

Steps:

1. Inspect.
2. Replay one selected symbol.
3. Deep validate.
4. Convert.
5. Build a three-day dataset by varying deterministic synthetic days.
6. Train all baselines.
7. Select model/weight on validation, simulate both strategies over the required test grid and
   separately verify signal-weight-zero equivalence.
8. Generate report.

Assertions:

- Every command exits 0.
- Parent/child identities link correctly.
- Repeated execution produces identical deterministic artefact hashes and metrics.
- Signal-weight-zero result equals inventory-aware baseline.
- Report contains historical/simulated and limitation wording.

### E2E-002 malformed source

Truncate and mutate frames. Assert bounded typed failures, no crash and no completed artefact.

### E2E-003 degraded flow

Use a safely framed unknown type. Permissive replay completes degraded with exact error and skip
counts. Conversion rejects it without `allow_degraded`; an explicit override produces a degraded
conversion manifest. Later downstream tasks must propagate that disclosure into the final report.

### E2E-004 cancellation

Cancel a real replay subprocess after output has begun. Assert exit 130, newline-terminated closed
partial artefacts, no final paths, an unchanged source and a successful clean rerun.

## Contract testing

### Binary contracts

- Golden byte files for event/snapshot schema v1 generated from synthetic records.
- C++ writer compared byte-for-byte with golden files.
- The TASK-016 production Python readers authenticate and read every golden event/snapshot record
  into exact typed diagnostics across multiple chunk sizes. Independent `struct` decoding remains a
  separate oracle; corrupt schema, reserved-bit, endian, null-canonicalisation, ordering and hash
  cases fail with stable codes before an affected batch is yielded.
- Python tests independently construct expected values rather than importing C++ constants.

### JSON contracts

- JSON Schemas for configs and manifests.
- Golden valid and invalid instances.
- Unknown properties fail.
- Canonicalisation and identity hashes are stable.

### CLI contracts

- --help snapshots checked for required commands/options, not brittle whitespace.
- JSON success/error envelopes validated against schema.
- Exit codes tested per category.
- stderr/stdout separation tested.

### Compatibility policy

Any breaking file/CLI contract change requires:

1. Schema or major-version increment.
2. Updated ADR.
3. New golden fixtures.
4. Migration or explicit incompatibility note.

## Accessibility testing

- Run help and result tests with NO_COLOR and TERM=dumb.
- Verify --ascii eliminates non-ASCII decoration.
- Check output remains understandable at 79 columns.
- Validate HTML semantics with an automated accessibility checker.
- Verify every plot has alt text/caption and nearby numeric/text summary.
- Test colour palettes with a colour-vision-deficiency simulation tool before publication.
- Keyboard-navigate all report links; no interactive control may require a pointer.

## Security testing

Defined in 07-security-and-privacy.md and implemented through:

- ASan and UBSan builds.
- Framing/decoder fuzz targets.
- Checked-arithmetic boundary tests.
- Path alias/traversal/symlink tests in temporary directories.
- Hash-tamper tests.
- HTML injection strings.
- Network-disabled E2E run.
- Secret and dependency scans.
- Tests that arbitrary pickle/joblib input is rejected.

TASK-028 maintains separate framing and typed-decoder corpora under `tests/fuzz/corpus/`. The
generator derives valid type seeds from the independent mixed fixture and fixes empty, truncated,
maximum, oversized, unknown-type, wrong-length and invalid-timestamp boundaries. It also synthesises
valid common-header seeds for the TASK-031 spec-known ignored types. Verify the corpus and run the
configured 10,000-mutation budget with:

    python tests/fuzz/generate_corpus.py --check
    cmake --preset fuzz
    cmake --build --preset fuzz
    ctest --preset fuzz --output-on-failure -R SEC-FUZZ-001

The `auto` engine uses libFuzzer where the compiler provides its runtime. On Apple toolchains that
omit libFuzzer it uses the deterministic standalone corpus mutator with ASan/UBSan; the security CI
sets `ITCHLAB_FUZZ_ENGINE=libfuzzer`, so missing real libFuzzer support fails configuration.

The complete local gate is `scripts/security/task028-security.sh`. It checks corpus reproducibility,
warnings-as-errors, the full `clang-analyzer-*` family, sanitizer and fuzz tests, the Python suite,
the reviewed secret baseline, the hashed runtime dependency lock and a fail-closed network-isolated
synthetic smoke. `ITCHLAB_CLANG_TIDY` may identify a non-PATH executable without changing the
project toolchain.

## Performance testing

### Benchmarks

| ID | Benchmark | Primary metric |
| --- | --- | --- |
| PERF-001 | Framing only, uncompressed | Messages/s and bytes/s |
| PERF-002 | Decode known mixed types | Messages/s |
| PERF-003 | Directory plus selected-symbol filter | Messages/s |
| PERF-004 | Parser plus level-3 book | Messages/s, allocations/message |
| PERF-005 | gzip full pipeline | Compressed/uncompressed bytes/s |
| PERF-006 | Snapshot writer | Records/s and output bytes/record |
| PERF-007 | Python binary-to-Parquet conversion | Records/s and peak RSS |
| PERF-008 | Large synthetic streaming run | Peak RSS stability |

Rules:

- Publish release-build results only.
- Record hardware, OS, compiler, flags, fixture hash and repetitions.
- Report median plus dispersion; do not publish a single best run.
- Benchmark data structure alternatives using identical state digests.
- CI has a generous catastrophic-regression threshold; hardware-specific claims are produced on the named machine.

TASK-029's fixture recipe, commands, profile evidence, allocation counter and measured PERF-001–008
results are recorded in [the TASK-029 performance note](performance/TASK-029-performance.md).

## Test-data strategy

### Committed data

- Programmatically generated synthetic ITCH payloads.
- Tiny fixed golden byte files produced from those synthetic definitions.
- Synthetic multi-day research datasets with deliberately known signal/no-signal cases.
- Corrupt/truncated/malicious fixtures.

### Uncommitted data

- Official Nasdaq full-day files.
- Bulk normalised/Parquet datasets.
- Full model predictions and simulation outputs unless reduced to documented examples.

### Rules

- Synthetic events are labelled synthetic in filenames and metadata.
- Test builders use field definitions independent from the production decoder where practical.
- Official-data tests verify hashes from local configs and skip with a clear reason when absent.
- No confidential/personal data is introduced.

### Synthetic ITCH fixture corpus

TASK-003 provides a standard-library-only builder in `tests/fixtures/`. It encodes fields from the
Nasdaq TotalView-ITCH 5.0 layouts without importing the production reader, decoder or their
constants. ADR-005 records TASK-004 verification of the two-byte big-endian outer framing and
complete-frame EOF behaviour against an authorised official sample.

| Fixture family | Purpose |
| --- | --- |
| `synthetic_minimal.itch[.gz]` | S/R/A/D first vertical slice with a complete add/delete lifecycle |
| `synthetic_mixed.itch[.gz]` | Three symbols, every MVP type, partial/full mutations, trade/break, halt/resume and close |
| `invalid_lifecycle/synthetic_invalid_*.itch` | Correctly framed duplicate, missing-reference, over-decrement and replacement conflicts |
| `corrupt/synthetic_corrupt_*.itch[.gz]` | Zero/oversized/truncated frames, wrong known length, unknown type and damaged gzip members |

`tests/golden/itch50/synthetic_expected.json` records exact payload hex, independently declared
fields, message indices, frame/payload offsets and type counts. `fixture_sha256.json` pins the size
and SHA-256 of every binary fixture. Gzip generation uses compression level 9, an empty embedded
filename and modification time zero; its decompressed bytes must exactly equal the corresponding
uncompressed fixture.

Generate the fixed corpus atomically beneath the repository root:

    python -m tests.fixtures.generate_itch50

Verify committed bytes without writing:

    python -m tests.fixtures.generate_itch50 --check

Builder self-tests independently fix the 13 payload lengths and reviewed literal byte vectors.
They exercise every shorter observed length from zero through expected−1 plus expected+1 for each
MVP type without committing hundreds of redundant files. The committed corruption fixtures are
small representatives for integration, CLI and fuzz-regression tests.

## Mocking strategy

- Prefer in-memory ByteSource and real domain components over mocking.
- Mock only filesystem failure boundaries, clock used for observational metadata and process cancellation.
- Do not mock OrderBook in replay integration tests.
- Do not mock feature/label calculations in model tests; use small real frames.
- Strategy tests may use a deterministic market-event fixture, not a mocked fill result.
- Network mocking is unnecessary because network access is absent.

## Coverage requirements

| Criticality | Modules | Minimum |
| --- | --- | --- |
| Critical | Framing, decoder, book, binary reader/writer, simulator accounting/state | 90% line, 85% branch |
| High | Config validation, features, labels, splits, manifests, strategies | 85% line, 80% branch |
| Standard | CLI adapters, reporting/presentation | 80% line |

Generated code and third-party dependencies are excluded. Any exclusion in project-owned code requires a comment and review.

TASK-030's Python gate aggregates `coverage.py --branch` JSON without rounding before comparison.
Critical contains interchange readers plus simulator accounting/state; high contains canonical
config, config validation, features, labels, splits and strategies; every other package module is
standard. `scripts/ci/check_coverage.py` fails closed on missing tiers or malformed totals.

The C++ coverage preset and `scripts/ci/cpp-coverage.sh` also publish project-only gcov/gcovr line
and compiler-branch evidence. Its 75% line/35% compiler-branch values are a catastrophic regression
floor, not a reinterpretation of the source-level tier requirements above: compiler-generated C++
exception/control-flow arcs are not comparable with coverage.py branches. The scenario, invariant,
sanitizer and fuzz suites remain primary C++ evidence, and a release may not claim NFR-009 solely
from passing that compiler regression floor.

## CI checks

### Pull-request jobs

1. Markdown/link/diagram lint.
2. C++ format and warnings-as-errors build on Ubuntu.
3. C++ unit/integration tests.
4. ASan/UBSan test subset.
5. Python Ruff format/lint and mypy.
6. Python unit/integration tests with coverage.
7. Binary/JSON contract tests.
8. Synthetic E2E.
9. Secret scan and dependency audit.
10. Performance smoke threshold on a deterministic synthetic fixture.

`.github/workflows/security.yml` supplies the TASK-028 PR, main-branch and weekly scheduled
security job. Its third-party actions are pinned to full commits, Python installation uses the
hashed development lock, and real libFuzzer is required before the consolidated gate runs.

`.github/workflows/ci.yml` supplies documentation, quality/coverage, native-platform,
installed-release and performance-smoke jobs for pull requests, `main`, manual dispatch and a
weekly schedule. It uses fixed `ubuntu-24.04` x86-64 and `macos-15` ARM64 labels and asserts the
kernel/architecture before native release work. The release job resolves dependencies before
entering fail-closed operating-system network isolation, installs from the local wheelhouse and
built archives, and runs the full synthetic E2E without a source-tree package import.

### Main/scheduled jobs

- Full sanitizer suite.
- Longer fuzz corpus.
- macOS build/test when runner availability permits.
- Dependency vulnerability rescan.
- Documentation link check.

Official full-day files are never downloaded into public CI.

## Definition of done

A task is done only when:

- Its acceptance criteria pass.
- Required unit/integration/security/performance tests are added and pass.
- Formatting, static analysis and relevant sanitizer checks pass.
- No unrelated file is changed.
- Public behaviour/config/schema changes update authoritative documentation and ADRs.
- TASKS.md is updated with completion evidence.
- New assumptions or limitations are documented.
- Generated artefacts are not committed unless explicitly designated small examples.

## Requirement-to-test traceability process

- Tests use stable IDs in names or metadata, for example FR_004_replace_resets_priority.
- docs/11-traceability.md maps every FR, NFR and SEC requirement to task and test IDs.
- A CI script parses the matrix and fails if a requirement lacks a task or test reference.
- Reviewers update the matrix in the same change as a requirement.

## Major example test catalogue

| Test ID | Feature | Example assertion |
| --- | --- | --- |
| UT-DEC-001 | Decode | A-message fixture decodes exact order, side, shares, symbol and Price4 |
| UT-DEC-002 | Bounds | Every truncated known message returns ERR_MESSAGE_LENGTH before access |
| UT-BOOK-001 | Lifecycle | Add→partial execute→cancel→delete produces expected states |
| UT-BOOK-002 | Replace | Old ref disappears; new ref joins back of new level |
| UT-BOOK-003 | Atomic reference error | Duplicate add and missing delete return errors and the state digest is unchanged |
| UT-BOOK-004 | Atomic quantity error | Over-cancel returns error and state digest is unchanged |
| UT-OUT-001 | Binary writer | Event-v1 header, dictionary and all ten 72-byte kinds match the independent golden |
| UT-OUT-002 | Snapshot writer | Record size is 48 + 28×depth and null flags round-trip |
| UT-CFG-001 | Config | Unknown key and overlapping dates fail |
| UT-FEAT-001 | Causality | Future mutation cannot alter previous feature values |
| UT-LABEL-001 | Labels | Hand sequence yields exact-threshold down/flat/up and null primary/secondary tails across batch boundaries |
| UT-MODEL-001 | Baselines/preprocessing | Known-signal and no-signal data fit all required models; fit dates are training-only and unseen symbols encode all-zero |
| UT-SIM-001 | Latency | Order cannot fill before effective timestamp |
| UT-SIM-002 | Queue | Known ahead volume must deplete before fill |
| UT-SIM-003 | Cancellation race | Fill before cancellation-effective time is retained |
| UT-SIM-004 | Accounting | Cash+inventory mark−fees reconciles to reported P&L |
| UT-STRAT-001 | Inventory skew | Long inventory lowers reservation price |
| UT-STRAT-002 | Signal adjustment | Zero weight emits baseline decisions; controlled scores clip and retain exact causal keys |
| CT-BIN-001 | Cross-language | Python reads C++ golden event/snapshot files exactly |
| CT-JSON-001 | Manifest | Completed replay manifest validates; unknown key fails |
| SEC-FUZZ-001 | Parser safety | Maintained fuzz corpus completes without sanitizer finding |
| SEC-PATH-001 | Filesystem | Source/unrelated sentinel survives failure and cancellation |
| E2E-001 | Full pipeline | Synthetic source reaches validated report reproducibly |
| PERF-004 | Performance | Release parser+book throughput and digest recorded |
