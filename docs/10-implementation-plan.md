# 10 — Implementation plan

## Planning rules

- Work on one TASKS.md item at a time.
- Each task must leave the repository runnable or more verifiably correct.
- Do not start a dependent task before its dependencies meet their acceptance criteria.
- Update requirements/ADRs before changing public behaviour or architecture.
- Complexity describes expected reasoning/integration effort, not elapsed time: Small, Medium or Large.
- Application code is not present in this documentation pack; paths below are expected implementation locations.

## Phase 0 — Foundation

### Milestone M0 — Reproducible repository skeleton

#### TASK-001 — Scaffold toolchains and repository boundaries

- Dependencies: none.
- Covers: NFR-005, NFR-006, NFR-012.
- Expected files/components: CMakeLists.txt, CMakePresets.json, cpp skeleton, python/pyproject.toml, .gitignore, formatting/static-analysis configs.
- Acceptance criteria: dev/release/sanitizer CMake presets configure; placeholder C++ and Python CLIs print versions; raw/derived/runs paths are ignored; documented setup works on the development Mac.
- Required tests: C++ smoke test, Python import/CLI smoke test, ignore-rule test.
- Completion evidence: command transcript and CI-ready build metadata committed.
- Complexity: Medium.

#### TASK-002 — Define domain primitives, error catalogue and JSON schemas

- Dependencies: TASK-001.
- Covers: FR-006, FR-009, FR-021; NFR-006; SEC-002.
- Expected files/components: core/types.hpp, core/errors.hpp, config models, schemas/*.schema.json, example configs.
- Acceptance criteria: fixed-width types and checked conversion helpers exist; stable error codes match contracts; replay/dataset/experiment/simulation configs reject unknown keys and invalid cross-field values; canonical JSON hashing is specified and cross-language consistent.
- Required tests: UT-CFG-001, integer boundary tests, C++/Python canonical-hash contract test.
- Completion evidence: valid/invalid golden configs and matching hash output.
- Complexity: Medium.

#### TASK-003 — Build independent synthetic ITCH fixture tooling

- Dependencies: TASK-002.
- Covers: FR-002, NFR-008, NFR-009.
- Expected files/components: tests/fixtures builder, declarative synthetic-day definitions, corruption mutators, golden expected JSON.
- Acceptance criteria: builder emits length-framed uncompressed and gzip fixtures without using the production decoder; covers every MVP message and complete/invalid lifecycles; every fixture is labelled synthetic.
- Required tests: builder self-tests, gzip/uncompressed semantic equality, fixture SHA-256 snapshots.
- Completion evidence: small committed fixtures and documented generation command.
- Complexity: Medium.

## Phase 1 — First vertical slice

### Milestone M1 — Inspect and replay a minimal synthetic order lifecycle

#### TASK-004 — Implement streaming byte sources and framed-message reader

- Dependencies: TASK-003.
- Covers: FR-001, FR-003; NFR-002, NFR-008; SEC-001, SEC-012.
- Expected files/components: input/byte_source.*, input/file_source.*, input/gzip_source.*, input/framed_reader.*.
- Acceptance criteria: bounded streaming works for gzip/uncompressed fixtures; clean EOF is distinct from truncation; frame length validated before buffer use; source offset/message index are correct; cancellation check is possible between frames; official sample framing assumption is locally verified and recorded.
- Required tests: IT-001/IT-002 reader portions, short-read/truncation/property tests, sanitizer
  run. TASK-005 extends IT-001/IT-002 through typed decoding.
- Completion evidence: inspect-style diagnostic over synthetic fixture plus a short framing-verification note.
- Complexity: Large.

#### TASK-005 — Decode system, directory, add and delete messages

- Dependencies: TASK-004.
- Covers: FR-002, FR-003; SEC-001, SEC-002.
- Expected files/components: itch/messages.hpp, itch/byte_decode.*, itch/decoder.*, per-type decoders for S/R/A/D.
- Acceptance criteria: exact-length validation precedes access; big-endian/timestamp/Price4 fields match independent fixtures; decoder is stateless; unknown types return typed errors.
- Required tests: UT-DEC-001, UT-DEC-002, the typed-decoder portions of IT-001/IT-002 and boundary
  fixtures for S/R/A/D.
- Completion evidence: JSON diagnostic output exactly matches golden expected fields.
- Complexity: Medium.

#### TASK-006 — Implement minimal add/delete level-3 book

- Dependencies: TASK-005.
- Covers: FR-004, FR-005; NFR-001.
- Expected files/components: book/order.hpp, book/price_level.hpp, book/order_book.* and digest/invariant helpers.
- Acceptance criteria: add/delete maintain FIFO, totals, best prices and top-N; duplicate/missing references fail atomically; deterministic digest ignores container bucket layout.
- Required tests: add/delete unit tests, UT-BOOK-003, invariant/property tests.
- Completion evidence: golden book state after each minimal fixture message.
- Complexity: Large.

#### TASK-007 — Deliver minimal inspect and replay commands

- Dependencies: TASK-004, TASK-005, TASK-006.
- Covers: FR-001, FR-003, FR-007, FR-008; NFR-007, NFR-011.
- Expected files/components: apps/itchlab command adapters, ReplayCoordinator, provisional diagnostic event/snapshot sinks.
- Acceptance criteria: inspect reports bounded statistics; replay resolves one symbol and processes S/R/A/D into deterministic diagnostic event/snapshot files; human/JSON channels and exit codes follow contracts; no final production interchange claim yet.
- Required tests: CLI help/envelope tests and minimal command integration.
- Completion evidence: one documented command produces expected selected-symbol state.
- Complexity: Medium.

#### TASK-008 — Lock the first end-to-end golden vertical slice

- Dependencies: TASK-007.
- Covers: NFR-001, NFR-006, NFR-009.
- Expected files/components: tests/cpp/integration, tests/golden/minimal, CI smoke script.
- Acceptance criteria: clean checkout builds, inspects and replays minimal fixture; repeated output and state digest match; corrupt fixture fails without final output; documentation commands match reality.
- Required tests: reduced E2E-001, E2E-002.
- Completion evidence: passing CI transcript and golden artefact review.
- Complexity: Small.

## Phase 2 — Complete replay core

### Milestone M2 — Full MVP message lifecycle and validated artefacts

#### TASK-009 — Decode the remaining MVP message types

- Dependencies: TASK-008.
- Covers: FR-002.
- Expected files/components: decoders for H/F/E/C/X/U/P/Q/B, typed-message variant expansion.
- Acceptance criteria: every required type has exact field/length fixtures; execute-with-price preserves display and execution semantics; trade-side field is not labelled aggressor truth; cross/broken trades are normalised without mutating the visible book.
- Required tests: remaining decoder unit/boundary tests and full mixed-type stream.
- Completion evidence: decoder coverage table shows all required types.
- Complexity: Large.

#### TASK-010 — Complete order lifecycle, priority and aggregation

- Dependencies: TASK-009.
- Covers: FR-004, FR-005.
- Expected files/components: full OrderBook mutations and InvariantChecker.
- Acceptance criteria: partial/full E/C/X, D and U semantics match golden lifecycle; replacement resets priority; level/order invariants pass after every fixture mutation; rejected mutations leave digest unchanged.
- Required tests: UT-BOOK-001, UT-BOOK-002, lifecycle property tests.
- Completion evidence: per-event golden state trace.
- Complexity: Large.

#### TASK-011 — Add directory resolution, session/trading-state and early filtering

- Dependencies: TASK-009, TASK-010.
- Covers: FR-003, FR-008.
- Expected files/components: InstrumentDirectory, SessionState, ReplayCoordinator filtering.
- Acceptance criteria: daily locate codes resolve requested symbols; unknown symbol fails before publication; selected pre-session events warm the book and are flagged out of session; configured half-open session and tradable-state filter work; snapshots contain no out-of-session row or unrequested instrument; global session metadata remains recorded.
- Required tests: multi-symbol/halt/resume integration fixtures.
- Completion evidence: selected/all message-count reconciliation.
- Complexity: Medium.

#### TASK-012 — Implement error policy, progress and cancellation

- Dependencies: TASK-011.
- Covers: FR-006; NFR-004, NFR-007; SEC-012.
- Expected files/components: ErrorPolicy, ProgressReporter, CancellationToken, top-level signal adapter.
- Acceptance criteria: strict stops at first error; permissive skips only safely framed classes and enforces budget; progress observes channel/rate rules; first SIGINT closes partial output and exits 130; second may terminate.
- Required tests: E2E-003, E2E-004, error-budget and non-TTY output tests.
- Completion evidence: logs/manifests for failed, degraded and cancelled examples.
- Complexity: Medium.

#### TASK-013 — Implement normalised event interchange writer

- Dependencies: TASK-010, TASK-012.
- Covers: FR-007; NFR-001, NFR-004; SEC-004.
- Expected files/components: output/binary_encode.*, output/event_writer.*, v1 header/record constants.
- Acceptance criteria: explicit little-endian 104-byte header, 16-byte symbol entries and 72-byte event records match 04-data-model.md; validity flags cover nullable fields; output order matches source; partial file is not final.
- Required tests: UT-OUT-001 and writer failure-injection tests.
- Completion evidence: byte-for-byte golden event file.
- Complexity: Large.

#### TASK-014 — Implement snapshot writer and completed replay manifest

- Dependencies: TASK-011, TASK-012, TASK-013.
- Covers: FR-008, FR-009, FR-021; NFR-004, NFR-006; SEC-003, SEC-004, SEC-010.
- Expected files/components: output/snapshot_writer.*, output/manifest.*, atomic publication helper, replay JSON Schema.
- Acceptance criteria: snapshot record size/flags/depth conform to 48 + 28×depth; unchanged snapshots are suppressed except required state/trade events; manifest contains required lineage/counts/build data; absolute local paths are removed from publishable fields; completed manifest is published last.
- Required tests: UT-OUT-002, CT-JSON-001, path/atomic-write tests.
- Completion evidence: validated golden replay directory.
- Complexity: Large.

#### TASK-015 — Implement shallow/deep artefact validation

- Dependencies: TASK-013, TASK-014.
- Covers: FR-022; SEC-004.
- Expected files/components: validation/validator.*, validate command.
- Acceptance criteria: shallow validation checks schemas/sizes/hashes/counts; deep validation streams records, flags and ordering and optionally reconstructs digest; tampering, partial status and unsupported version fail distinctly.
- Required tests: IT-012, CT-BIN-001, tamper/unknown-version tests.
- Completion evidence: pass/fail validation report over golden directories.
- Complexity: Medium.

## Phase 3 — Research pipeline

### Milestone M3 — Causal dataset and predictive baselines

#### TASK-016 — Implement safe Python interchange readers

- Dependencies: TASK-015.
- Covers: FR-010; NFR-002, NFR-005; SEC-004, SEC-006.
- Expected files/components: python/interchange headers, records, chunk readers.
- Acceptance criteria: readers validate magic/version/record size/hash before yielding; chunked batches preserve types/null flags/order; no pickle; C++ golden files round-trip exactly on supported platforms.
- Required tests: CT-BIN-001, corrupt/reserved-bit/endian tests.
- Completion evidence: Python diagnostic representation matches golden JSON.
- Complexity: Large.

#### TASK-017 — Convert validated interchange to partitioned Parquet

- Dependencies: TASK-016.
- Covers: FR-010, FR-021; NFR-002, NFR-004.
- Expected files/components: conversion service/CLI, conversion manifest/schema.
- Acceptance criteria: events/snapshots convert in bounded chunks with documented dtypes/nulls; partition paths and sort keys conform; publication is atomic; degraded parents rejected by default.
- Required tests: IT-007, large synthetic memory test, cancellation/write failure.
- Completion evidence: validated conversion manifest and Parquet schema dump.
- Complexity: Medium.

#### TASK-018 — Implement causal feature catalogue

- Dependencies: TASK-017.
- Covers: FR-011; NFR-010.
- Expected files/components: datasets/features modules and feature-catalogue output.
- Acceptance criteria: required spread, imbalance, microprice, OFI, event-rate, volatility and observable trade features exist; formulas/windows/dtypes recorded; warm-up nulls explicit; only current/past rows read.
- Required tests: UT-FEAT-001 and hand-calculated feature cases.
- Completion evidence: feature catalogue plus example rows reconciled manually.
- Complexity: Large.

#### TASK-019 — Implement labels, day partitions and leakage guards

- Dependencies: TASK-018.
- Covers: FR-012; NFR-010.
- Expected files/components: datasets/labels, datasets/splits, dataset manifest.
- Acceptance criteria: primary three-class event-horizon label and tails work; day partitions are chronological/non-overlapping; features and labels join by immutable row key; future-perturbation leakage guard passes.
- Required tests: UT-LABEL-001, partition property tests, IT-008.
- Completion evidence: row-drop/class/day counts in validated dataset manifest.
- Complexity: Large.

#### TASK-020 — Train and evaluate required baselines

- Dependencies: TASK-019.
- Covers: FR-013; NFR-006, NFR-010.
- Expected files/components: models/prior, logistic, gradient_boosting; metrics; experiment manifest/predictions.
- Acceptance criteria: preprocessing fits training only; validation selects predefined candidates; test runs once after selection; required metrics/calibration/confusion matrix produced; failures remain visible; reproduction uses recorded seeds/config.
- Required tests: UT-MODEL-001, metric hand cases, IT-009.
- Completion evidence: completed experiment directory on synthetic known-signal and no-signal data.
- Complexity: Large.

#### TASK-021 — Generate predictive research report section

- Dependencies: TASK-020.
- Covers: FR-019; NFR-011; SEC-009, SEC-010, SEC-011.
- Expected files/components: reporting templates/renderers, report command initial version.
- Acceptance criteria: data lineage, splits, features, models, metrics, calibration, negative results and reproduction commands render in Markdown and optional HTML; injection/absolute-path tests pass; plots have text summaries.
- Required tests: IT-011, accessibility and report-escaping tests.
- Completion evidence: reviewed synthetic report.
- Complexity: Medium.

## Phase 4 — Execution simulation

### Milestone M4 — Conservative strategy comparison

#### TASK-022 — Implement simulated-order state machine and latency scheduler

- Dependencies: TASK-017, TASK-019.
- Covers: FR-014, FR-016.
- Expected files/components: simulation/order, state_machine, scheduler.
- Acceptance criteria: all documented states/transitions work; actions become effective after integer-nanosecond latency; fill-before-cancel race is ordered by market message index/time; invalid transitions fail.
- Required tests: UT-SIM-001, UT-SIM-003 and state-machine property tests.
- Completion evidence: golden transition trace.
- Complexity: Large.

#### TASK-023 — Implement visible queue tracking and partial fills

- Dependencies: TASK-022.
- Covers: FR-015; NFR-010.
- Expected files/components: simulation/queue_model, market-event adapter.
- Acceptance criteria: passive order joins behind known visible queue at activation; exact known-ahead lifecycle changes update queue; hidden/unknown liquidity is not invented; fills are caused by eligible executions and never overfill; anomalies are bounded/recorded.
- Required tests: UT-SIM-002, queue property tests and IT-010 subset.
- Completion evidence: hand-reconciled queue/fill trace.
- Complexity: Large.

#### TASK-024 — Implement accounting, fees, inventory risk and liquidation

- Dependencies: TASK-023.
- Covers: FR-016.
- Expected files/components: simulation/accounting, risk_limits, liquidation, metrics.
- Acceptance criteria: signed fees/rebates, cash, inventory and marked P&L reconcile in integer microusd; risk-increasing quotes stop at limit; terminal liquidation rule is explicit; zero-fill/day edge cases have valid metrics.
- Required tests: UT-SIM-004, overflow/boundary and no-fill cases.
- Completion evidence: independent spreadsheet/manual reconciliation of golden trace.
- Complexity: Large.

#### TASK-025 — Implement inventory-aware baseline strategy

- Dependencies: TASK-022, TASK-024.
- Covers: FR-017.
- Expected files/components: strategies/avellaneda_stoikov, causal calibration utilities.
- Acceptance criteria: reservation price/quote distance follow documented equations; volatility/intensity estimates use trailing/configured prior only; quotes round to tick/passive constraints; inventory skew direction test passes.
- Required tests: UT-STRAT-001, parameter-boundary and no-calibration cases.
- Completion evidence: strategy decision table over synthetic scenarios with paper citation.
- Complexity: Large.

#### TASK-026 — Implement bounded signal-adjusted strategy

- Dependencies: TASK-020, TASK-025.
- Covers: FR-018; NFR-010.
- Expected files/components: strategies/signal_adjusted, prediction join.
- Acceptance criteria: a causal as-of join selects the latest same-symbol prediction at or before each decision and records its exact key; adjustment is bounded in ticks and fully configured; zero weight equals baseline decisions/results; signal weight is chosen on validation only; missing score diagnostics work.
- Required tests: UT-STRAT-002, prediction-key and missing-prediction tests.
- Completion evidence: baseline-equivalence and controlled-signal fixture results.
- Complexity: Medium.

#### TASK-027 — Run scenarios and complete simulation report

- Dependencies: TASK-023, TASK-024, TASK-025, TASK-026.
- Covers: FR-016, FR-019, FR-021.
- Expected files/components: simulation runner/manifests, orders/fills/equity outputs, report strategy/sensitivity sections.
- Acceptance criteria: at least three latency and two cost scenarios run for both strategies; metrics include fills, inventory, P&L decomposition, drawdown, turnover and adverse-selection proxy; assumptions/anomalies/limitations prominent; immutable output validates.
- Required tests: full IT-010, E2E-001 simulation/report assertions.
- Completion evidence: reviewed synthetic sensitivity report.
- Complexity: Large.

## Phase 5 — Hardening, evidence and release

### Milestone M5 — Publishable project

#### TASK-028 — Complete security hardening

- Dependencies: TASK-015, TASK-017, TASK-027.
- Covers: NFR-008; SEC-001 through SEC-012.
- Expected files/components: fuzz targets/corpus, sanitizer preset, checked arithmetic, path guards, report escaping, dependency/secret scan configs.
- Acceptance criteria: every security acceptance criterion in 07-security-and-privacy.md passes; no open unaccepted high/critical issue; threat model matches implementation.
- Required tests: SEC-FUZZ-001, SEC-PATH-001 and all security test requirements.
- Completion evidence: security checklist/report with exact tool versions and findings.
- Complexity: Large.

#### TASK-029 — Benchmark, profile and optimise one measured bottleneck

- Dependencies: TASK-015, TASK-028.
- Covers: FR-020; NFR-001, NFR-002, NFR-003.
- Expected files/components: benchmark harness/fixtures, performance notes/profile artefacts, optional ADR for data-structure change.
- Acceptance criteria: parser/filter/book/gzip/writer benchmarks produce environment metadata and digest; peak RSS stability measured; profiler identifies bottleneck; one justified optimisation has before/after medians with unchanged tests/digest; target or revised ADR is satisfied.
- Required tests: PERF-001 through PERF-008 and correctness regression.
- Completion evidence: committed small benchmark summary and reproducible command.
- Complexity: Large.

#### TASK-030 — Finish CI, doctor command and release packaging

- Dependencies: TASK-028, TASK-029.
- Covers: NFR-005, NFR-006, NFR-009, NFR-011.
- Expected files/components: CI workflows, doctor command, release scripts/config, dependency resolution, licence inventory.
- Acceptance criteria: PR/scheduled jobs match testing strategy; clean installed wheel/binary pass synthetic E2E offline; release archives/checksums exclude raw/bulk data; macOS/Linux support recorded.
- Required tests: full CI matrix, release-install smoke and network-disabled E2E.
- Completion evidence: local release candidate and checklist.
- Complexity: Medium.

#### TASK-031 — Execute the official-data study

- Dependencies: TASK-030.
- Covers: G-003 through G-006; FR-011 through FR-020.
- Expected files/components: private/local raw data, versioned public-safe configs, completed run manifests, final report and benchmark summary.
- Acceptance criteria: at least three symbols and three distinct days; dates/symbols justified before test inspection; all parent hashes validate; baselines and scenarios run; result reports unfavourable findings, sensitivity and limitations; raw/bulk data remain uncommitted.
- Required tests: local full-day deep validation, reproduction spot-check, final report validation.
- Completion evidence: final report/manifests and exact reproduction instructions.
- Complexity: Large.

#### TASK-032 — Conduct final documentation and traceability review

- Dependencies: TASK-031.
- Covers: all requirements.
- Expected files/components: README, docs, ADRs, TASKS, traceability matrix and consolidated specification.
- Acceptance criteria: no unresolved contradiction; every requirement has task/test evidence; commands/paths/schemas match implementation; open questions are resolved/deferred explicitly; all links/Mermaid/Markdown pass; definition of done met.
- Required tests: documentation lint, traceability validator and fresh-reviewer walkthrough.
- Completion evidence: signed-off checklist and tagged v0.1.0 candidate.
- Complexity: Medium.

## Milestone outputs

| Milestone | Runnable/testable outcome |
| --- | --- |
| M0 | Both toolchains build and validated configs/fixtures exist |
| M1 | One selected synthetic symbol can be inspected and minimally replayed end to end |
| M2 | Full MVP lifecycle produces validated versioned artefacts |
| M3 | Causal dataset and predictive report are reproducible |
| M4 | Conservative baseline/signal strategy scenarios are reproducible |
| M5 | Security, performance, official-data evidence and release documentation are publishable |
