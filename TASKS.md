# Execution checklist

Authoritative task details and evidence requirements are in docs/10-implementation-plan.md. Work on one item at a time.

## Current milestone — M2 Full MVP message lifecycle and validated artefacts

## Queued

- [ ] TASK-012: Implement error policy, progress and cancellation
  - Dependencies: TASK-011
  - Acceptance criteria: Strict/permissive/error budget and safe exit 130
  - Tests: E2E-003/E2E-004
  - Documentation to update: 02-user-flows.md

- [ ] TASK-013: Implement normalised event writer
  - Dependencies: TASK-010, TASK-012
  - Acceptance criteria: Golden 104-byte header and 72-byte records
  - Tests: UT-OUT-001 and write failures
  - Documentation to update: 04-data-model.md on schema change

- [ ] TASK-014: Implement snapshot writer and replay manifest
  - Dependencies: TASK-011–013
  - Acceptance criteria: Golden snapshots; atomic completed manifest; safe paths
  - Tests: UT-OUT-002, CT-JSON-001, path tests
  - Documentation to update: 04/05 contracts

- [ ] TASK-015: Implement artefact validation
  - Dependencies: TASK-013–014
  - Acceptance criteria: Shallow/deep validation catches tampering/partial/version errors
  - Tests: IT-012, CT-BIN-001
  - Documentation to update: README validate command

- [ ] TASK-016: Implement safe Python interchange readers
  - Dependencies: TASK-015
  - Acceptance criteria: Chunked cross-platform golden-file reads; no pickle
  - Tests: CT-BIN-001 and corrupt schema tests
  - Documentation to update: 05-api-contracts.md

- [ ] TASK-017: Convert interchange to Parquet
  - Dependencies: TASK-016
  - Acceptance criteria: Typed chunked partitions and atomic conversion manifest
  - Tests: IT-007 and memory/failure tests
  - Documentation to update: 04-data-model.md if schema differs

- [ ] TASK-018: Implement causal feature catalogue
  - Dependencies: TASK-017
  - Acceptance criteria: Required past-only features and metadata
  - Tests: UT-FEAT-001
  - Documentation to update: Feature catalogue and 01 requirements

- [ ] TASK-019: Implement labels, splits and leakage guards
  - Dependencies: TASK-018
  - Acceptance criteria: Three-class horizons, chronological whole days, frozen manifest
  - Tests: UT-LABEL-001, IT-008
  - Documentation to update: Final horizon/split decision

- [ ] TASK-020: Train/evaluate predictive baselines
  - Dependencies: TASK-019
  - Acceptance criteria: Prior/logistic/gradient boosting with train-only preprocessing and single test evaluation
  - Tests: UT-MODEL-001, IT-009
  - Documentation to update: Experiment config/metrics

- [ ] TASK-021: Generate predictive report section
  - Dependencies: TASK-020
  - Acceptance criteria: Reproducible accessible report with negative results/limitations
  - Tests: IT-011 and injection/accessibility
  - Documentation to update: README report example

- [ ] TASK-022: Implement order state machine and latency
  - Dependencies: TASK-017, TASK-019
  - Acceptance criteria: Valid deterministic lifecycle and action timing
  - Tests: UT-SIM-001/003 and properties
  - Documentation to update: 02-user-flows.md if transitions change

- [ ] TASK-023: Implement queue tracking and partial fills
  - Dependencies: TASK-022
  - Acceptance criteria: Known visible queue logic; no invented/over fills
  - Tests: UT-SIM-002 and queue properties
  - Documentation to update: Queue assumption ADR

- [ ] TASK-024: Implement accounting, costs, risk and liquidation
  - Dependencies: TASK-023
  - Acceptance criteria: Integer reconciliation and inventory enforcement
  - Tests: UT-SIM-004 and edge cases
  - Documentation to update: Simulation contract

- [ ] TASK-025: Implement inventory-aware baseline
  - Dependencies: TASK-022, TASK-024
  - Acceptance criteria: Causal calibrated tick-rounded quotes and correct inventory skew
  - Tests: UT-STRAT-001
  - Documentation to update: Equations/calibration reference

- [ ] TASK-026: Implement bounded signal adjustment
  - Dependencies: TASK-020, TASK-025
  - Acceptance criteria: Exact prediction join; zero weight equals baseline
  - Tests: UT-STRAT-002
  - Documentation to update: Signal rule/config

- [ ] TASK-027: Run scenarios and finish simulation report
  - Dependencies: TASK-023–026
  - Acceptance criteria: Required latency/cost grid, metrics and limitations
  - Tests: IT-010 and E2E-001
  - Documentation to update: Report reproduction commands

- [ ] TASK-028: Complete security hardening
  - Dependencies: TASK-015, TASK-017, TASK-027
  - Acceptance criteria: All security acceptance criteria pass
  - Tests: SEC-FUZZ-001, SEC-PATH-001 and security suite
  - Documentation to update: 07-security-and-privacy.md threat review

- [ ] TASK-029: Benchmark/profile/optimise one bottleneck
  - Dependencies: TASK-015, TASK-028
  - Acceptance criteria: Full benchmark evidence and semantic equivalence
  - Tests: PERF-001–008
  - Documentation to update: Performance note/ADR

- [ ] TASK-030: Finish CI, doctor and release packaging
  - Dependencies: TASK-028–029
  - Acceptance criteria: Clean offline installed E2E and safe release archive
  - Tests: Full CI/release smoke
  - Documentation to update: README and 09-deployment.md

- [ ] TASK-031: Execute official-data study
  - Dependencies: TASK-030
  - Acceptance criteria: Three+ symbols/days and complete validated final study
  - Tests: Full-day validation and reproduction spot-check
  - Documentation to update: Resolve OQ-001/OQ-005 and final report

- [ ] TASK-032: Final documentation and traceability review
  - Dependencies: TASK-031
  - Acceptance criteria: No contradiction or unmapped requirement; v0.1.0 ready
  - Tests: Docs lint, traceability validator, reviewer walkthrough
  - Documentation to update: All authoritative documents

## Completed

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
