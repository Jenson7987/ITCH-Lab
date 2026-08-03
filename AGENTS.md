# ITCH-Lab agent instructions

## Purpose

Build an offline C++20/Python research platform that safely replays Nasdaq ITCH 5.0 data, reconstructs selected visible level-3 books, creates causal datasets and evaluates conservative historical market-making simulations. It is not a live-trading system.

## Authoritative documentation

Read the relevant files before editing:

1. Accepted ADRs in docs/decisions/.
2. docs/01-product-requirements.md.
3. docs/03-system-architecture.md, docs/04-data-model.md and docs/05-api-contracts.md.
4. docs/08-testing-strategy.md and docs/10-implementation-plan.md.
5. TASKS.md for current execution state.

If documents conflict, stop and update the authoritative decision before code.

## Working rules

- Work on exactly one current TASKS.md item unless explicitly asked otherwise.
- Inspect existing code, tests and interfaces before adding an abstraction.
- Do not change architecture, schemas, scientific definitions or simulator assumptions silently.
- Do not edit unrelated files or reformat unrelated code.
- Preserve completed artefact immutability and compatibility rules.
- Never add live exchange/broker connectivity, telemetry, runtime downloads or credential handling without an approved ADR.
- Never commit raw/bulk market data, run outputs, secrets or machine-specific paths.
- Never use random row splits, future-derived features, test-set tuning or immediate-fill assumptions.

## Technology and boundaries

- C++20 core with CMake; explicit endian decoding and checked bounds.
- Python 3.11+ research package with typed, chunked data processing.
- Cross-language boundary is versioned files, not per-event bindings or a network API.
- Core prices are Price4 integers; simulation cash uses checked integer microusd.
- No packed-host-struct serialisation, eval/exec or required pickle/joblib loading.
- Single-threaded deterministic replay is the MVP default.

## Coding conventions

- Prefer small domain types and explicit ownership.
- Expected input/domain errors use stable typed results; top-level CLI translates them.
- Unknown config keys fail.
- Public modules have concise contract documentation.
- Complex algorithms cite a paper/specification or include a derivation.
- Optimise only after profiling and preserve deterministic state digests.
- Use British English in user-facing text and documentation.

## Security rules

- Treat raw, config, manifest, interchange and Parquet inputs as untrusted.
- Validate length/version/type before access or allocation.
- Mutations must be atomic on error.
- Write only beneath an explicit run root using partial files and atomic publication.
- Reject source/output aliasing and unsafe broad paths.
- Escape report content and omit absolute paths/raw payloads from publishable output.
- Run relevant sanitizers/fuzz/path tests for boundary changes.

## Commands

    cmake --preset dev
    cmake --build --preset dev
    ctest --preset dev
    cmake --build --preset sanitizers
    ctest --preset sanitizers
    python -m pytest python/tests
    python -m ruff check python
    python -m ruff format --check python
    python -m mypy python/src

Run only commands relevant to the active task, then the required integration checks.

## Documentation updates

- Update requirement, contract, ADR and traceability documents in the same change as public behaviour.
- Record new assumptions and limitations.
- Do not duplicate authoritative detail into TASKS.md.
- Update TASKS.md only after tests and completion evidence exist.

## Definition of done

A task is complete only when acceptance criteria and required tests pass; format/static checks pass; security/performance checks relevant to the change pass; documentation and traceability are current; and no unrelated change remains.
