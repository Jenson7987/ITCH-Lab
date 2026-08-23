# Execution checklist

Authoritative task details and evidence requirements are in docs/10-implementation-plan.md. Work on one item at a time.

## Current milestone — M5 Publishable project

## Queued

- [x] TASK-030: Finish CI, doctor and release packaging
  - Dependencies: TASK-028–029
  - Acceptance criteria: Clean offline installed E2E and safe release archive
  - Tests: Full CI/release smoke
  - Documentation to update: README and 09-deployment.md
  - Evidence: local installed-wheel E2E passed offline on macOS ARM64 (2,580 messages over three replay dates); Python suite passed (681 tests); C++ dev, release, coverage and sanitizer builds each passed all 133 runnable tests, with the authorised official-data test skipped; security, coverage and release-performance gates passed.
  - Limitation: the Ubuntu x86_64 and macOS ARM64 hosted GitHub Actions matrix definitions are in place but have not been executed remotely; all completed evidence is local.

- [ ] TASK-032: Final documentation and traceability review
  - Dependencies: TASK-031
  - Acceptance criteria: No contradiction or unmapped requirement; v0.1.0 ready
  - Tests: Docs lint, traceability validator, reviewer walkthrough
  - Documentation to update: All authoritative documents

## Completed

- [x] TASK-031: Execute official-data study
  - Completed: 2026-08-22
  - Evidence: The frozen AAPL/MSFT/AMZN study used chronological official sample days for train,
    validation and one-shot test. Strict inspection decoded 844,963,543 messages with zero errors;
    three replay runs deep-validated 14,455,244 event/snapshot records and authenticated the exact
    sources, children and final book digests. The immutable conversion, 604,054-row dataset,
    1,149,636-prediction experiment and twelve-cell simulation all authenticated; the final report
    retains every result, including eleven negative marked-P&L cells. A forced full December replay
    reproduced byte-identical children and final digests. Development, Release and ASan/UBSan each
    passed 136 CTests with one authorised-data opt-in skip; both 10,000-mutation fuzz targets, all
    684 Python tests, formatting, lint, mypy, package build, static analysis, dependency/secret and
    network-disabled security checks, docs lint and the installed release smoke passed. OQ-001 and
    conservative OQ-002 are resolved; the absent OQ-005 reference is documented in the evidence.

- [x] TASK-029: Benchmark/profile/optimise one bottleneck
  - Completed: 2026-08-18
  - Evidence: The deterministic untracked 1,000,003-message plain/gzip fixture, release CLI and
    pinned Google Benchmark harness cover PERF-001–006 with fixture/build/hardware metadata,
    warm-up, ten-sample medians/MAD, peak RSS, immutable JSON evidence and final book digests.
    Instruments attributed 324 of 849 resolved samples to `OrderBook::apply` and 110 to allocation;
    a per-book standard-library PMR pool reduced measured allocations from 0.902929 to 0.003599 per
    message and increased the comparable PERF-004 median from 6.29M to 9.66M messages/s (+53.7%)
    without changing the canonical digest. The full final median was 9.71M messages/s, exceeding
    NFR-003's 1M floor. PERF-007/008 record 120,000-row Python conversion throughput, peak RSS and
    bounded RSS growth. All 133 runnable development, Release and sanitizer CTests passed in the
    full gates (the authorised official-data case skipped as designed); the final path change also
    passed targeted sanitizer tests. All 614 Python tests, Ruff, mypy, Markdown lint, deterministic
    fixture verification, wheel/sdist build and the release benchmark harness passed. The
    performance note records profile evidence, before/after medians, trade-offs and Rosetta limits.

- [x] TASK-028: Complete security hardening
  - Completed: 2026-08-17
  - Evidence: Maintained framing/decoder fuzz targets now exercise committed deterministic boundary
    corpora with a 10,000-mutation budget per target under ASan/UBSan; CI requires real libFuzzer
    while Apple Clang uses the documented sanitizer-backed deterministic driver. SEC-PATH-001 and
    conversion/cancellation regressions preserve source and unrelated sentinels across aliases,
    symlinks, success, failure and cancellation. Repository policy tests cover executable
    serialisation/code execution, network APIs, credential schema fields, tracked data, private
    paths and packaged schema parity; report injection covers script and Markdown metacharacters.
    The reviewed secret baseline contains 24 false-positive test/study hashes/literals and zero
    secrets;
    pip-audit found no known vulnerability in the hashed release lock, and the exact dependency
    licence inventory has no unaccepted high/critical issue. The network-disabled synthetic smoke,
    clang-analyzer checks over all 28 project translation units, two SEC-FUZZ-001 tests, all 128
    runnable sanitizer CTests and all 613 Python tests passed; the one authorised official-data
    CTest skipped as designed. Ruff, mypy, C++ formatting, dev/release tests and wheel/sdist build
    also passed. The dated threat review maps evidence to all ten security acceptance criteria.

- [x] TASK-027: Run scenarios and finish simulation report
  - Completed: 2026-08-17
  - Evidence: The authenticated simulation service now freezes model family and signal weight on
    validation evidence before materialising test rows, runs both strategies over the required 3×2
    latency/maker-cost grid, and publishes manifest-last immutable orders, passive fills, terminal
    liquidations, equity, reconciled metrics and diagnostics. Completed manifests retain exact
    training calibration and selection evidence and revalidate parent lineage, schemas and child
    hashes; verified reuse, force-new-run and tamper rejection pass. The combined accessible report
    includes explicit scenario settings, fill/inventory/P&L/drawdown/turnover/100 ms markout
    evidence, prominent assumptions/anomalies/limitations and relative reproduction commands.
    Hand-reconciled IT-010 and full synthetic E2E-001 assertions pass. All 602 Python tests, Ruff
    formatting/lint, strict mypy and wheel/sdist build passed. Development, Release and sanitizer
    C++ builds passed; all 128 runnable CTest entries passed in each preset and the authorised
    official-sample entry skipped as designed.

- [x] TASK-026: Implement bounded signal adjustment
  - Completed: 2026-08-16
  - Evidence: The Python strategy domain now performs a bounded causal as-of join over one exact
    trading-day/symbol/model prediction stream, retains the complete prediction key, distinguishes
    missing and stale zero-score diagnostics, and rejects malformed, mis-keyed or future content
    atomically. Model family and signal weight selection consume validation evidence only, apply
    the documented tolerance and simplicity/smaller-weight tie-breaks against the true optimum,
    and freeze the configured result before test decisions. The signal strategy applies the
    clipped score adjustment while reusing baseline volatility, tick rounding, passivity and risk
    constraints; weight zero does not consume predictions and produces identical baseline economic
    decisions and order requests. UT-STRAT-002, causal join tests and the controlled-signal golden
    fixture all pass. All 589 Python tests, Ruff formatting/lint, strict mypy and wheel/sdist build
    passed. The Release C++ build passed; all 128 runnable CTest entries passed and the authorised
    official-sample entry skipped as designed.

- [x] TASK-025: Implement inventory-aware baseline
  - Completed: 2026-08-15
  - Evidence: The Python strategy domain now fits finite positive execution-intensity decay from
    fixed 0-through-10 outward same-side-best buckets using exact training-only nanosecond exposure,
    smoothed E/C counts and exposure-weighted least squares. Invalid symbol fits use the recorded
    pooled training estimate, while missing pooled calibration and non-training observations fail
    explicitly. A bounded per-session/symbol estimator computes causal trailing midpoint variance
    rates and the baseline applies the documented reservation-price and half-spread equations,
    exact outward tick rounding, strict passive constraints and existing projected full-fill risk
    limits. UT-STRAT-001's hand-calculated flat/long/short decision table plus calibration, future-
    prefix, clock-window, parameter, locked/off-grid book, price-domain, risk-boundary and atomicity
    cases all pass. All 556 Python tests, Ruff formatting/lint, strict mypy and wheel/sdist build
    passed. The Release C++ build passed; all 128 runnable CTest entries passed and the authorised
    official-sample entry skipped as designed.

- [x] TASK-024: Implement accounting, costs, risk and liquidation
  - Completed: 2026-08-14
  - Evidence: The Python simulation domain now consumes causal queue fills into a checked signed-
    int64 microusd ledger with deterministic fill IDs, exact per-symbol inventory, signed maker
    fees/rebates, causal doubled-midpoint marking and an exactly reconciling cash/P&L decomposition.
    Projected full-fill risk decisions suppress risk-increasing quotes outside inclusive inventory
    limits, and fill accounting independently enforces the same boundary atomically. Session-end
    settlement preflights and expires open orders, liquidates longs at the last visible bid and
    shorts at the last visible ask, rejects missing/crossed terminal quotes, applies signed taker
    fees and returns zero-safe metrics. UT-SIM-004's hand-reconciled golden trace plus boundary,
    overflow, atomicity, no-fill and generated-property tests cover both sides, fee signs, risk
    limits, causal marking and long/short liquidation. All 522 Python tests, Ruff formatting/lint,
    strict mypy and wheel/sdist build passed. The Release C++ build passed; all 128 runnable CTest
    entries passed and the authorised official-sample entry skipped as designed.

- [x] TASK-023: Implement queue tracking and partial fills
  - Completed: 2026-08-14
  - Evidence: The Python simulation domain now adapts validated normalised lifecycle rows into an
    exact-known visible-order model and snapshots every same-symbol/side/Price4 reference ahead at
    the market-first activation boundary. E/C/X/D/U events update only exact ahead references;
    later adds and replacement references remain behind. Once ahead is empty, same-price E/C flow
    can produce causal partial/full fills capped by both observed event quantity and simulated
    remainder, while C uses its displayed resting price and P/Q never fill. Cross-through
    remainders invalidate without invented liquidity, used-match B events fail with
    ERR_BROKEN_SIM_FILL, and atomic queue anomalies obey the explicit max_queue_anomalies config
    budget. ADR-004 records the complete policy. UT-SIM-002, the hand-reconciled IT-010 subset and
    generated queue properties cover exact activation/depletion, replacement priority,
    equal-timestamp ordering, fill/cancel races, overfill bounds, hidden/cross exclusions, broken
    trades, invalidation and anomaly limits. All 363 Python tests, Ruff formatting/lint, strict
    mypy and wheel/sdist build passed. The Release and ASan/UBSan C++ builds passed; all 128 runnable
    CTest entries passed in both presets and the authorised official-sample entry skipped as
    designed.

- [x] TASK-022: Implement order state machine and latency
  - Completed: 2026-08-14
  - Evidence: The Python simulation domain now owns immutable attempted passive orders across the
    documented pending-submit, active, partially-filled, pending-cancel, filled, cancelled,
    expired, rejected and counterfactually-invalidated states, with atomic typed failures and one
    non-terminal order per symbol/side. Its checked integer-nanosecond scheduler applies actions
    strictly before later market events, defers equal-time actions until every source message at
    that timestamp, retains request order for equal-time actions and safely supersedes losing
    activation/cancellation races. UT-SIM-001/003, the complete golden transition trace and
    generated lifecycle properties cover latency boundaries, every transition, fill/cancel races,
    slot release, quantity invariants and atomic invalid operations. All 315 Python tests, Ruff
    formatting/lint, strict mypy and wheel/sdist build passed. The Release C++ build passed; all 128
    runnable CTest entries passed and the authorised official-sample entry skipped as designed.

- [x] TASK-021: Generate predictive report section
  - Completed: 2026-08-08
  - Evidence: The `itchlab-research report` command reauthenticates a completed predictive
    experiment and its dataset, conversion and replay manifest lineage before publishing a
    format-scoped immutable report bundle. Deterministic Markdown and optional semantic HTML cover
    source/code lineage, chronological splits, feature definitions, every model candidate,
    validation/test aggregate and per-symbol metrics, confidence intervals, confusion matrices,
    calibration, negative results, limitations and relative reproduction commands. Bundles include
    canonical config snapshots, machine-readable calibration data and six labelled static SVGs
    with captions and adjacent text summaries; mismatched completed bundles are never overwritten.
    IT-011 plus CLI, injection, accessibility, lineage/output tamper and write-failure tests cover
    deterministic reuse, all output-format choices, safe partial retention, path privacy, escaped
    data, semantic tables, relative links and distinguishable plot colour/line/marker encodings. All
    223 Python tests, Ruff formatting/lint, strict mypy and wheel/sdist build passed. The Release C++
    build passed; all 128 runnable CTest entries passed and the authorised external-data test
    skipped as designed.

- [x] TASK-020: Train/evaluate predictive baselines
  - Completed: 2026-08-08
  - Evidence: The `itchlab-research train` command authenticates a completed frozen dataset before
    fitting the training-frequency prior and pooled multinomial-logistic/histogram-gradient-
    boosting grids with training-only median imputation, family-specific scaling and dense
    unknown-safe symbol encoding. Validation multiclass log loss and documented conservative
    tie-breaks freeze each candidate before the test partition is loaded and evaluated exactly once.
    The immutable experiment publishes a strict content-identified manifest, validation/test
    aggregate and per-symbol metrics, fixed-order confusion matrices, ten-bin calibration,
    seeded whole-day confidence intervals or an explicit omission, schema-validated predictions
    and safe diagnostics without model serialisation. UT-MODEL-001, metric hand cases and IT-009
    cover known/no signal, tie rules, train-only fits, unseen symbols, single test loading, lineage,
    CLI output, reuse, tampering and cancellation partials. All 216 Python tests, Ruff formatting/
    lint, strict mypy and wheel/sdist build passed. Dev, Release and ASan/UBSan builds passed; all
    128 runnable CTest entries passed in each preset and the authorised external-data test skipped
    as designed.

- [x] TASK-019: Implement labels, splits and leakage guards
  - Completed: 2026-08-07
  - Evidence: The `itchlab-research build-dataset` command revalidates completed conversion and
    replay lineage, independently streams past-only features and bounded 20/100/500 future labels,
    joins exact immutable row metadata, and applies disjoint history, primary-tail and original-
    ordinal stride filters before assigning complete chronological days to frozen train, validation
    and test partitions. It writes joined Zstandard Parquet plus hashed feature/data-quality
    metadata and atomically publishes a strict, private-path-free dataset manifest with reconciled
    row-drop, class and label-availability counts. UT-LABEL-001, partition/join properties and
    IT-008 cover exact thresholds/null tails, future-mutation leakage, all three classes, identity
    reuse/forced immutability, parent/output tampering, unsafe/missing input, cancellation and
    injected write failure. All 200 Python tests, Ruff formatting/lint, strict mypy, both fixture
    checks and wheel/sdist build passed. All 129 CTest entries passed in dev, Release and ASan/UBSan
    presets; the authorised official-sample entry skipped as designed.

- [x] TASK-018: Implement causal feature catalogue
  - Completed: 2026-08-07
  - Evidence: The partition-scoped PyArrow service streams exact event-v1 and snapshot-v1 batches
    into 33 documented version-1 features with immutable row metadata, explicit per-feature warm-up
    nulls and a complete-history flag. It implements spread, depth-1/5/10 imbalance, microprice and
    displacement, 20/100/500 OFI and realised-volatility windows, resting-side 100 ms/1 s event
    rates, observable E/C aggressor direction and B-corrected execution imbalance using only events
    at or before each decision index. The deterministic catalogue records dtypes, formulae,
    lookbacks, units, null policies and ownership. Thirteen TASK-018 tests include independent
    catalogue and hand-calculated goldens, exact window boundaries, causal B correction,
    equal-timestamp ordering, non-qualifying rows, stable invalid-input errors and UT-FEAT-001's
    unchanged-prefix future-mutation guard. All 168 Python tests, Ruff formatting/lint, strict mypy,
    the 25-file fixture check and wheel/sdist build passed. The Release C++ build passed; 128 CTest
    entries passed and the authorised official-sample entry skipped as designed.

- [x] TASK-017: Convert interchange to Parquet
  - Completed: 2026-08-07
  - Evidence: The `itchlab-research convert` command authenticates completed replay lineage and
    event-v1/snapshot-v1 children before bounded conversion to documented Zstandard Parquet schemas,
    with canonical trading-date/symbol partitions, strict per-partition message-index order and
    atomic immutable manifest publication. Degraded parents require an explicit override; matching
    runs are fully revalidated before reuse and forced runs never overwrite. IT-007 reconciles every
    golden value, dtype and null; multi-day/depth, tamper, path, write-failure and real-SIGINT tests
    pass. The 120,000-record case stays below its 128 MiB traced-allocation and 256 MiB RSS-growth
    limits. All 155 Python tests, Ruff formatting/lint, strict mypy, fixture checks and wheel/sdist
    build passed. The Release C++ producer build passed; 128 runnable CTests passed and the authorised
    external-data test skipped as designed.

- [x] TASK-016: Implement safe Python interchange readers
  - Completed: 2026-08-07
  - Evidence: Standard-library event-v1 and snapshot-v1 readers now require a trusted child
    SHA-256, validate the complete header, dictionary, size and stable file identity before yielding,
    and decode explicit little-endian records into frozen typed batches with an internal byte cap.
    Record validation mirrors the established C++ flag, null, ordering, quantity, ASCII, state and
    depth invariants; partial paths, unsupported/endian-mutated schemas, reserved bits, tampering and
    pickle-shaped input fail with stable typed errors. CT-BIN-001/IT-006 match all ten event and two
    snapshot golden records to independently generated JSON diagnostics across chunk sizes. All 131
    Python tests, Ruff, formatting, strict mypy, dependency and fixture checks, wheel/sdist build,
    reduced E2E smoke and the Release C++ build passed; all 128 runnable Release CTest entries passed
    and the authorised external-data entry skipped as designed.

- [x] TASK-015: Implement artefact validation
  - Completed: 2026-08-07
  - Evidence: The read-only `itchlab validate` command now accepts exactly one completed replay
    directory or standalone event-v1/snapshot-v1 file, reports stable human/JSON checks and can
    optionally authenticate exact source bytes. Shallow replay validation checks the strict bounded
    manifest, canonical config/identity lineage, child hashes/sizes/counts, headers, dictionaries and
    cross-file metadata before data use. Deep validation streams every record, checks ordering,
    validity/reserved bits, canonical null/depth and session semantics, rehashes against concurrent
    changes and reconstructs final visible books to authenticate counts and digests. IT-012 proves
    child tampering fails before record reads; CT-BIN-001 independently decodes every committed
    event and snapshot golden record in Python. Additional tests cover wrong sources, digest forgery,
    duplicate manifest keys, partial/truncated artefacts, unsupported versions, reserved fields,
    ordering, CLI output and exit categories. All 128 runnable CTest entries passed in dev, Release
    and ASan/UBSan presets; the authorised external-data entry skipped as designed. All 80 Python
    tests, Ruff, strict mypy, C++ formatting, diff/privacy checks, the Release C++ build and Python
    wheel/sdist build passed.

- [x] TASK-014: Implement snapshot writer and replay manifest
  - Completed: 2026-08-07
  - Evidence: The snapshot-v1 writer explicitly encodes fixed little-endian records of
    `48 + 28 × depth` bytes with nullable trade flags, deterministic depth padding and causal
    state/trade emission semantics; UT-OUT-002 matches an independently generated 344-byte golden
    byte for byte. Replay now stages event, snapshot, effective-config and manifest artefacts beneath
    a validated run root, verifies source/executable/config/child hashes, publishes the completed
    manifest last through an atomic directory rename and safely reuses only a fully verified identity
    unless `--force` requests a new run. IT-004, CT-JSON-001 and path/atomic/tamper tests cover strict
    schema validation, build lineage, private-path removal, symlink and alias rejection, failure and
    cancellation partials, idempotency and immutable completed runs. All 118 runnable CTest entries
    passed in dev, release, ASan/UBSan and clean coverage presets; the authorised external-data entry
    skipped as designed. The independent golden hashes, both fixture checks, reduced E2E smoke, all
    78 Python tests, Ruff, strict mypy, C++ formatting, shell syntax, diff/privacy checks and the
    Python wheel/sdist build passed.

- [x] TASK-013: Implement normalised event writer
  - Completed: 2026-08-06
  - Evidence: The event-v1 writer explicitly encodes little-endian 104-byte headers, requested-order
    16-byte symbol dictionaries and source-ordered 72-byte records with checked field bounds,
    validity flags and zeroed reserved bytes. Finalisation patches verified metadata and closes only
    the staged `.partial` path; publication remains assigned to TASK-014. UT-OUT-001 matches an
    independently generated 856-byte golden covering every event kind and validity bit, while
    validation, real-replay ordering and injected reservation/record/seek/patch/flush/close failure
    tests cover atomic rejection and terminal write errors. All 104 runnable CTest entries passed in
    dev, release, ASan/UBSan and clean coverage presets; the authorised external-data entry skipped
    as designed. The independent golden and 25-file fixture checks, reduced E2E smoke, all 75 Python
    tests, Ruff, strict mypy, changed C++ formatting, diff checks and the Python wheel/sdist build
    passed.

- [x] TASK-012: Implement error policy, progress and cancellation
  - Completed: 2026-08-06
  - Evidence: Replay now applies a stage-aware strict/permissive policy, counts stable errors,
    enforces the exact skip budget and marks successfully skipped runs degraded. Rate-limited human
    or JSONL progress stays on stderr and `--quiet` suppresses it. The first SIGINT is observed at a
    complete message boundary, closes newline-terminated partial diagnostics and exits 130; a
    second may terminate immediately. Twelve TASK-012 unit/integration cases cover E2E-003,
    real-process E2E-004, safe/unsafe classes, exact budgets, atomic book skips, progress rate and
    channel rules, cancellation tokens and signal handling. All 96 runnable CTest entries passed in
    dev, release, ASan/UBSan and coverage presets; the authorised external-data entry skipped as
    designed. The 25-file fixture check, reduced E2E smoke, all 75 Python tests, Ruff, strict mypy,
    C++ formatting, shell syntax, diff checks and the Python wheel/sdist build passed. Production
    interchange and manifest evidence remain correctly assigned to TASK-013/TASK-014.

- [x] TASK-011: Add directory, session/state and filtering
  - Completed: 2026-08-05
  - Evidence: Daily directory resolution now assigns requested-order SymbolIds, rejects
    contradictory records and filters unselected locates before book construction. Selected
    pre-session messages warm per-symbol books, H/P/Q/T and O/S/Q/M/E/C state is retained, the
    half-open session and optional tradable-state snapshot gate are enforced, and summaries expose
    complete global metadata plus exact all/selected/category count reconciliation. The independent
    25-message plain/gzip session fixture covers two selected symbols, filtered AMZN activity,
    warm-up, halt-time activity, resume and the exclusive end boundary; nine TASK-011 CTest cases
    passed 286 assertions. All 84 runnable CTest entries passed in dev, release, ASan/UBSan and
    coverage presets; the authorised external-data entry skipped as designed. The 25-file fixture
    check, reduced E2E smoke, all 75 Python tests, Ruff, strict mypy, C++ formatting and the Python
    wheel/sdist build passed.

- [x] TASK-010: Complete lifecycle, priority and aggregation
  - Completed: 2026-08-05
  - Evidence: The OrderBook now applies the complete A/F/E/C/X/D/U visible lifecycle with checked
    partial/full reductions, atomic replacement, retained F attribution, new U priority and exact
    FIFO/aggregate invariants. UT-BOOK-001/002/004, the independent lifecycle property model and
    IT-003's 14-state plain/gzip reader-decoder-book golden trace passed; every committed invalid
    lifecycle fixture preserved the digest and valid invariants. All 75 runnable CTest entries
    passed in dev, release, ASan/UBSan and coverage presets; the authorised official-data entry
    skipped as designed. The 23-file fixture check, reduced E2E smoke, all 73 Python tests, Ruff,
    strict mypy, C++ formatting and the Python wheel/sdist build passed.

- [x] TASK-009: Decode remaining MVP message types
  - Completed: 2026-08-05
  - Evidence: The stateless typed decoder now covers S/R/H/A/F/E/C/X/D/U/P/Q/B with exact
    pre-access length checks, big-endian field preservation and per-type integer/timestamp
    boundaries; the 31-message plain/gzip mixed golden matches exactly, C keeps display and
    execution-price semantics separate, P does not claim aggressor truth, and P/Q/B have no book
    mutation route. All 68 runnable CTest entries passed in dev, release, ASan/UBSan and coverage
    presets; the authorised official-data entry skipped as designed. The 23-file fixture check,
    reduced E2E smoke, all 73 Python tests, Ruff, strict mypy, C++ formatting and the Python
    wheel/sdist build passed.

- [x] TASK-008: Lock first E2E golden slice
  - Completed: 2026-08-05
  - Evidence: A fresh source-tree smoke configured and built the dev preset, verified all 23
    synthetic fixture files, reproduced exact inspect/replay envelopes, byte-identical repeated
    diagnostic events/snapshots and the pinned final book digest, and proved a CRC-corrupt gzip
    exits with ERR_FRAMING without publishing final diagnostic names. All 63 runnable CTest entries
    passed in dev, release, ASan/UBSan and coverage presets; the authorised external-data entry
    skipped as designed. All 73 Python tests, Ruff, strict mypy, C++ formatting, shell syntax,
    fixture verification and the Python wheel/sdist build passed.

- [x] TASK-007: Deliver minimal inspect and replay commands
  - Completed: 2026-08-04
  - Evidence: Bounded plain/gzip inspection and strict one-symbol S/R/A/D replay produce exact
    provisional diagnostic event/snapshot goldens with stable human/JSON channels and exit codes;
    the documented synthetic commands completed successfully. Across dev, release, ASan/UBSan and
    coverage presets, all 61 runnable CTest entries passed and the authorised external-data entry
    skipped as designed; 73 Python tests, fixture verification, Ruff, strict mypy, C++ formatting
    and the Python wheel/sdist build passed.

- [x] TASK-006: Implement minimal add/delete level-3 book
  - Completed: 2026-08-04
  - Evidence: Deterministic add/delete level-3 books preserve FIFO priority, checked aggregate
    totals, explicit top-N depth and canonical SHA-256 state digests; duplicate/missing references
    and invalid mutations leave state unchanged. All 51 runnable CTest entries passed in dev,
    release, ASan/UBSan and coverage builds (the existing official-data test remained opt-in and
    skipped); 73 Python tests, Ruff, strict mypy and C++ formatting checks passed.

- [x] TASK-005: Decode S/R/A/D
  - Completed: 2026-08-04
  - Evidence: Stateless typed decoding and bounded big-endian helpers implemented; exact golden
    S/R/A/D diagnostics match plain/gzip fixtures; 41 CTest cases passed in dev, release and
    ASan/UBSan presets and the opt-in official-data check passed separately; 23 fixture files
    verified; 73 Python tests, Ruff, strict mypy and C++ formatting checks passed.

- [x] TASK-004: Implement streaming byte sources and framed reader
  - Completed: 2026-08-04
  - Evidence: Bounded plain/gzip byte sources and `itch-length-v1` reader implemented; ADR-005 records official-sample framing and termination evidence; 32 CTest cases passed in dev, release and ASan/UBSan presets; the opt-in official-sample test, 73 Python tests, Ruff, strict mypy and C++ formatting checks passed.

- [x] TASK-003: Build independent synthetic ITCH fixture tooling
  - Completed: 2026-08-04
  - Evidence: 23 deterministic synthetic fixture/golden files generated and checked; 27 builder tests and 73 total Python tests passed; Ruff, strict mypy, dependency, package/wheel and C++ formatting checks passed; 15 CTest cases passed in dev, release and ASan/UBSan presets; every MVP type, plain/gzip equality, corruptions, invalid lifecycles and SHA-256 snapshots are covered.

- [x] TASK-002: Define domain primitives, errors and JSON schemas
  - Completed: 2026-08-03
  - Evidence: 15 CTest cases passed in dev, release and ASan/UBSan presets; 46 Python tests, Ruff, mypy, C++ formatting, dependency checks and clean-wheel schema/hash smoke passed; committed golden replay canonical bytes and matching C++/Python SHA-256 values.

- [x] TASK-001: Scaffold toolchains and repository boundaries
  - Completed: 2026-08-03
  - Evidence: dev/release/sanitizers/coverage configure, build and test passed with three CTest cases per preset; Python wheel/sdist and clean-wheel CLI smoke passed; 15 pytest cases, Ruff, mypy, clang-format, pip dependency checks and Git ignore-rule checks passed.

## Blocked

None.

## Deferred

- Live feed receivers and exchange/broker connectivity.
- OUCH order-entry simulation.
- Multi-venue support and distributed replay.
- Deep-learning/online-learning extensions.
- Graphical or hosted interface.
