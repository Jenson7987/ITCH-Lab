# 11 — Requirements traceability

This matrix is authoritative for requirement-to-design, implementation and verification coverage. Test IDs are defined in 08-testing-strategy.md; descriptive suite names identify required tests that receive stable IDs during implementation.

## Functional requirements

| Requirement ID | Design document | Implementation task | Test coverage |
| --- | --- | --- | --- |
| FR-001 | 02 UF-001; 05 inspect | TASK-004, TASK-007 | IT-001, IT-002, CLI inspect contract |
| FR-002 | 03 ItchDecoder; 05 decoder | TASK-005, TASK-009 | UT-DEC-001, UT-DEC-002, per-type fixtures |
| FR-003 | 02 UF-002; 03 InstrumentDirectory/SessionState | TASK-004, TASK-007, TASK-011 | TASK-011 directory unit and multi-symbol/session/filter CLI/integration tests |
| FR-004 | 03 OrderBook; 04 transient entities | TASK-006, TASK-010 | UT-BOOK-001, UT-BOOK-002, UT-BOOK-003, UT-BOOK-004, IT-003 |
| FR-005 | 03 OrderBook; 04 PriceLevel/Snapshot | TASK-006, TASK-010 | IT-003, book aggregation/order property tests |
| FR-006 | 02 UF-002/UF-006; 03 errors | TASK-002 (catalogue/config), TASK-012 (runtime policy) | UT-CFG-001, TASK-012 stage-aware policy/budget tests, E2E-003 |
| FR-007 | 04 NormalisedEvent/event v1; 05 replay | TASK-007 (provisional diagnostic slice), TASK-013 (production event writer) | TASK-007 CLI/golden integration, UT-OUT-001 independent binary golden, TASK-013 mixed replay ordering, CT-BIN-001 |
| FR-008 | 04 BookSnapshot/snapshot v1 | TASK-007 (provisional diagnostic slice), TASK-011, TASK-014 (production snapshots) | TASK-007 CLI/golden integration, TASK-011 halt/resume and trading-state-gating integration, UT-OUT-002, IT-004 |
| FR-009 | 04 ReplayRun; 03 ManifestBuilder | TASK-002 (config contract), TASK-014 (manifest) | UT-CFG-001, CT-JSON-001, IT-004 |
| FR-010 | 03 Python conversion; 05 convert | TASK-016, TASK-017 | CT-BIN-001, IT-006, IT-007 |
| FR-011 | 04 DatasetRun; 05 build-dataset | TASK-018 | UT-FEAT-001, hand-calculated features |
| FR-012 | 02 UF-003; 05 dataset config | TASK-019 | UT-LABEL-001, IT-008, partition properties |
| FR-013 | 02 UF-004; 05 train | TASK-020 | UT-MODEL-001, IT-009, metric hand cases |
| FR-014 | 02 UF-005/state diagram; 04 SimulatedOrder | TASK-022 | UT-SIM-001, UT-SIM-003, state properties |
| FR-015 | ADR-004; 04 Fill/SimulatedOrder | TASK-023 | UT-SIM-002, queue properties, IT-010 |
| FR-016 | 02 UF-005; 04 SimulationRun | TASK-022, TASK-024, TASK-027 | UT-SIM-001/004, scenario-grid assertions |
| FR-017 | ADR-004; 05 simulate | TASK-025 | UT-STRAT-001, calibration-boundary tests |
| FR-018 | ADR-004; 05 simulate | TASK-026 | UT-STRAT-002, prediction-key tests |
| FR-019 | 02 UF-004/UF-005; 06 UI-009 | TASK-021, TASK-027, TASK-031 | IT-011, E2E-001, report-content checks |
| FR-020 | 03 performance; 05 benchmark | TASK-029 | PERF-001 through PERF-008 |
| FR-021 | 03 file contracts; 04 run entities | TASK-002 (canonical hashes), TASK-014, TASK-017, TASK-027 | Canonical-hash and identity/hash/idempotency contract tests |
| FR-022 | 03 ArtefactValidator; 05 validate | TASK-015 | IT-012, CT-BIN-001, tamper/version tests |

## Non-functional requirements

| Requirement ID | Design document | Implementation task | Test coverage |
| --- | --- | --- | --- |
| NFR-001 | ADR-003; 03 state/performance | TASK-006, TASK-013, TASK-029 | TASK-013 byte-for-byte event golden/source-order test, repeated-run byte/digest tests, E2E-001 |
| NFR-002 | 03 performance/scalability | TASK-004, TASK-016, TASK-017, TASK-029 | PERF-005/007/008, large-stream memory test |
| NFR-003 | 03 performance; 09 release criteria | TASK-029 | PERF-004 plus platform benchmark report |
| NFR-004 | 03 errors/file contracts | TASK-012, TASK-013, TASK-014, TASK-017 | TASK-013/TASK-014 injected writer failures, atomic publication tests, E2E-004 process cancellation/clean-rerun test, IT-005 |
| NFR-005 | ADR-002; 09 build | TASK-001, TASK-016, TASK-030 | macOS/Linux build, CT-BIN-001 |
| NFR-006 | 03 state/file contracts; 09 release | TASK-001, TASK-002, TASK-014, TASK-020, TASK-030 | Canonical-hash, TASK-014 identity/build-lineage, clean-install and E2E-001 |
| NFR-007 | 03 logging; 06 progress | TASK-007, TASK-012 | TASK-012 rate-limit and non-TTY JSONL/stderr/quiet tests |
| NFR-008 | 07 SEC-001/002/012 | TASK-004, TASK-005, TASK-028 | UT-DEC-002, SEC-FUZZ-001, sanitizers |
| NFR-009 | 08 coverage/CI | TASK-008, TASK-028, TASK-030 | Coverage gates and CI matrix |
| NFR-010 | ADR-004; 02 UF-003–005 | TASK-018–020, TASK-023–027, TASK-031 | UT-FEAT-001, UT-MODEL-001, simulator properties |
| NFR-011 | 06 accessibility | TASK-007, TASK-021, TASK-030 | NO_COLOR/TERM, HTML accessibility and plot tests |
| NFR-012 | ADR-001–004; AGENTS.md | TASK-001, TASK-032 | API docs review, docs/traceability lint |

## Security and privacy requirements

| Requirement ID | Design document | Implementation task | Test coverage |
| --- | --- | --- | --- |
| SEC-001 | 07 binary validation | TASK-004, TASK-005, TASK-009, TASK-028 | UT-DEC-002, SEC-FUZZ-001, ASan/UBSan |
| SEC-002 | 07 checked arithmetic | TASK-002, TASK-004, TASK-010, TASK-024, TASK-028 | Integer/quantity/cash boundary tests |
| SEC-003 | 07 filesystem writes; 09 incidents | TASK-014, TASK-028 | SEC-PATH-001, symlink/alias/cancellation tests |
| SEC-004 | 07 integrity/provenance | TASK-013–017, TASK-028 | TASK-013 embedded-hash golden and partial-only tests, TASK-014 child-hash verification/IT-004, IT-012, hash-tamper tests |
| SEC-005 | 07 runtime network | TASK-030 | Network-disabled E2E-001 and dependency review |
| SEC-006 | 07 serialisation | TASK-016, TASK-020, TASK-028 | No-pickle contract and malicious artefact rejection |
| SEC-007 | 07 secrets | TASK-028, TASK-030 | Secret scan and config-schema inspection |
| SEC-008 | 07 dependencies; 09 release | TASK-001, TASK-028, TASK-030 | Dependency audit/licence inventory |
| SEC-009 | 07 report escaping | TASK-021, TASK-028 | HTML/Markdown injection fixtures |
| SEC-010 | 07 logging restrictions | TASK-014, TASK-021, TASK-028 | TASK-014 recursive absolute-path assertions and payload-log assertions |
| SEC-011 | 07 misuse/data handling | TASK-003, TASK-021, TASK-027, TASK-031 | Git/archive data check and report wording tests |
| SEC-012 | 07 resource exhaustion | TASK-004, TASK-012, TASK-017, TASK-028 | PERF-008, oversized-frame, disk/cancel tests |

## Coverage review checklist

- [ ] Every requirement row has at least one accepted design location.
- [ ] Every requirement row has at least one implementation task.
- [ ] Every requirement row has at least one executable test or review gate.
- [ ] Completed TASKS.md items link their actual test output/commit evidence.
- [ ] Requirement changes update this matrix in the same commit.
- [ ] No test ID refers only to inaccessible raw market data.
