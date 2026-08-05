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

### Serialisation/manifests

- Header offsets, record sizes and little-endian bytes.
- Every nullable field validity combination.
- Symbol dictionary order.
- Record-count header finalisation.
- Partial publication and hash mismatch.
- Unknown schema/flag rejection.

### Python datasets/models

- Price4 conversions avoid binary-float values until presentation.
- Each rolling feature on a hand-calculated event sequence.
- Future perturbation does not change earlier features.
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
| IT-007 | Conversion to Parquet | Dtypes, nulls, partition paths and values match |
| IT-008 | Feature/label pipeline | Hand-calculated rows and leakage guard pass |
| IT-009 | Training baselines | Expected output schemas and training-only preprocessing |
| IT-010 | Event/prediction stream through simulator | Golden orders, fills, cash and inventory |
| IT-011 | Report generation | Required headings, tables, limitations and reproduction commands |
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
7. Simulate baseline and signal-weight-zero variant.
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

Use a safely framed unknown type. Permissive replay completes degraded; conversion rejects it without allow-degraded; explicit override propagates disclosure into final report.

### E2E-004 cancellation

Cancel after output has begun. Assert exit 130, closed partial artefacts and successful clean rerun.

## Contract testing

### Binary contracts

- Golden byte files for event/snapshot schema v1 generated from synthetic records.
- C++ writer compared byte-for-byte with golden files.
- Python reader reads golden files.
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
| UT-OUT-001 | Binary writer | Event v1 record matches golden 72 bytes |
| UT-OUT-002 | Snapshot writer | Record size is 48 + 28×depth and null flags round-trip |
| UT-CFG-001 | Config | Unknown key and overlapping dates fail |
| UT-FEAT-001 | Causality | Future mutation cannot alter previous feature values |
| UT-LABEL-001 | Labels | Hand sequence yields down/flat/up and null tails |
| UT-MODEL-001 | Preprocessing | Fit indices are a subset of training-day indices |
| UT-SIM-001 | Latency | Order cannot fill before effective timestamp |
| UT-SIM-002 | Queue | Known ahead volume must deplete before fill |
| UT-SIM-003 | Cancellation race | Fill before cancellation-effective time is retained |
| UT-SIM-004 | Accounting | Cash+inventory mark−fees reconciles to reported P&L |
| UT-STRAT-001 | Inventory skew | Long inventory lowers reservation price |
| UT-STRAT-002 | Zero signal | Signal-weight zero emits baseline decisions |
| CT-BIN-001 | Cross-language | Python reads C++ golden event/snapshot files exactly |
| CT-JSON-001 | Manifest | Completed replay manifest validates; unknown key fails |
| SEC-FUZZ-001 | Parser safety | Maintained fuzz corpus completes without sanitizer finding |
| SEC-PATH-001 | Filesystem | Source/unrelated sentinel survives failure and cancellation |
| E2E-001 | Full pipeline | Synthetic source reaches validated report reproducibly |
| PERF-004 | Performance | Release parser+book throughput and digest recorded |
