# Execution checklist

Authoritative task details and evidence requirements are in docs/10-implementation-plan.md. Work on one item at a time.

## Current milestone — M0 Reproducible repository skeleton

- [ ] TASK-002: Define domain primitives, errors and JSON schemas
  - Dependencies: TASK-001
  - Acceptance criteria: Fixed types/errors and strict config schemas; canonical hashes agree cross-language
  - Tests: UT-CFG-001, boundary and hash contract tests
  - Documentation to update: 04-data-model.md, 05-api-contracts.md

- [ ] TASK-003: Build independent synthetic ITCH fixture tooling
  - Dependencies: TASK-002
  - Acceptance criteria: Independent framed/gzip fixtures cover all required types and corruptions
  - Tests: Builder self-tests and fixture hashes
  - Documentation to update: 08-testing-strategy.md

## Queued

- [ ] TASK-004: Implement streaming byte sources and framed reader
  - Dependencies: TASK-003
  - Acceptance criteria: Bounded gzip/plain reads, exact offsets, safe EOF/truncation, verified sample framing
  - Tests: IT-001, IT-002, sanitizer/property tests
  - Documentation to update: ADR if framing differs

- [ ] TASK-005: Decode S/R/A/D
  - Dependencies: TASK-004
  - Acceptance criteria: Exact length/endian/field behaviour
  - Tests: UT-DEC-001, UT-DEC-002
  - Documentation to update: 05-api-contracts.md if types differ

- [ ] TASK-006: Implement minimal add/delete level-3 book
  - Dependencies: TASK-005
  - Acceptance criteria: FIFO/totals/top-N/digest and atomic errors
  - Tests: UT-BOOK-003 and invariants
  - Documentation to update: ADR-001 only if representation changes materially

- [ ] TASK-007: Deliver minimal inspect and replay commands
  - Dependencies: TASK-004–006
  - Acceptance criteria: One symbol reaches deterministic diagnostic output
  - Tests: CLI and integration smoke
  - Documentation to update: README examples

- [ ] TASK-008: Lock first E2E golden slice
  - Dependencies: TASK-007
  - Acceptance criteria: Clean repeated minimal run; corrupt input fails safely
  - Tests: Reduced E2E-001/E2E-002
  - Documentation to update: TASKS evidence

- [ ] TASK-009: Decode remaining MVP message types
  - Dependencies: TASK-008
  - Acceptance criteria: H/F/E/C/X/U/P/Q/B complete
  - Tests: Per-type boundary fixtures
  - Documentation to update: Decoder coverage table

- [ ] TASK-010: Complete lifecycle, priority and aggregation
  - Dependencies: TASK-009
  - Acceptance criteria: Full lifecycle and atomic invariants
  - Tests: UT-BOOK-001/002 and properties
  - Documentation to update: 04-data-model.md if semantics change

- [ ] TASK-011: Add directory, session/state and filtering
  - Dependencies: TASK-009–010
  - Acceptance criteria: Correct locate resolution and selected/tradable session output
  - Tests: Multi-symbol halt/resume integration
  - Documentation to update: 01-product-requirements.md if policy changes

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
