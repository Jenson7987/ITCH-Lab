# ITCH-Lab

ITCH-Lab is an offline quantitative-research platform that decodes Nasdaq TotalView-ITCH 5.0 binary market data, reconstructs selected instruments' level-3 visible order books, produces reproducible research datasets, and evaluates short-horizon signals and inventory-aware market-making strategies under explicitly documented queue, latency and cost assumptions.

## Status

The v0.1.0 MVP is implemented through the official-data study. The C++ pipeline provides bounded
plain/gzip framing, the complete MVP decoder, deterministic selected-symbol level-3 replay,
versioned event/snapshot writers, immutable manifests, artefact validation and release benchmarks.
The Python package provides authenticated conversion, causal datasets and labels, frozen predictive
baselines, conservative queue-aware simulation, and immutable accessible reports. Security,
coverage, deterministic packaging and offline installed-environment gates are included in CI.

TASK-031's public-safe evidence records the completed three-symbol, three-day study. TASK-032's
local documentation and traceability review is complete except for the final release-owner commit
and `v0.1.0` tag evidence; the task deliberately remains open until that evidence exists.

Classification legend used throughout the documentation:

- **Confirmed requirement**: accepted by the project owner through selection of the ITCH-Lab concept.
- **Assumption**: believed to be true for planning but not yet confirmed.
- **Recommendation**: a concrete technical choice proposed for the initial implementation.
- **Deferred decision**: deliberately postponed until evidence or a later milestone justifies it.

## Technology stack

| Area | Choice | Classification |
| --- | --- | --- |
| Performance core | C++20, CMake, Clang/GCC | Confirmed requirement |
| Compression | zlib-compatible streaming reader | Recommendation |
| C++ testing | Catch2 and Google Benchmark | Recommendation |
| Research package | Python 3.11+, PyArrow, NumPy and scikit-learn | Confirmed requirement for Python; libraries are recommendations |
| Python quality | pytest, Ruff, mypy | Recommendation |
| Configuration | Version-controlled JSON validated against JSON Schema | Recommendation |
| Data storage | Raw ITCH files, versioned binary interchange files, Parquet research tables, JSON manifests | Recommendation |
| Automation | GitHub Actions | Recommendation |
| Deployment | Local macOS/Linux command-line application | Assumption |

## Repository structure

    .
    ├── AGENTS.md
    ├── CLAUDE.md
    ├── CMakeLists.txt
    ├── CMakePresets.json
    ├── TASKS.md
    ├── configs/
    ├── cpp/
    │   ├── apps/itchlab/
    │   ├── include/itchlab/
    │   └── src/
    ├── data/
    │   ├── raw/          # ignored
    │   ├── derived/      # ignored
    │   └── fixtures/     # small synthetic fixtures only
    ├── docs/
    ├── python/
    │   ├── pyproject.toml
    │   ├── requirements-dev.lock
    │   ├── requirements-release.lock
    │   ├── src/itchlab_research/
    │   └── tests/
    ├── runs/             # ignored except documented example output
    ├── schemas/
    └── tests/

The repository implements the MVP described by `TASKS.md`. It provides buildable version/help
CLIs, fixed C++ domain/error types, strict validated
configuration contracts with canonical cross-language hashes, and bounded streaming of verified
`itch-length-v1` plain/gzip sources. It domain-decodes S/R/H/A/F/E/C/X/D/U/P/Q/B and structurally
validates the spec-known Y/L/V/W/K/I/N/J/h types with exact length, big-endian header and timestamp
checks. The latter are counted and intentionally ignored by the visible-book MVP; arbitrary unknown
types remain errors. Replay applies the full visible lifecycle to deterministic per-symbol level-3
books and exposes bounded inspect plus multi-symbol replay. Replay has
stage-aware strict/permissive error policy, stable error counts, degraded disclosure, rate-limited
human or JSONL progress and graceful SIGINT exit 130 with retained partial artefacts. The C++
writers produce deterministic event-v1 and snapshot-v1 headers, dictionaries and records. Replay
binds them to verified source/config/executable hashes and atomically publishes a completed,
private-path-free manifest. The read-only `validate` command checks completed replay directories or
standalone interchange files in shallow or streamed deep mode. Authenticated Python event-v1 and
snapshot-v1 readers expose typed, validated records in bounded chunks. The Python `convert` command
now validates replay lineage and child hashes, preserves integer/null semantics in typed Parquet,
and atomically publishes a conversion manifest with complete lineage and child hashes. Feature
construction now has a partition-scoped PyArrow service with an exact catalogue, causal rolling
state and explicit warm-up nulls. The Python `build-dataset` command independently computes bounded
future labels, enforces immutable-key joins and chronological whole-day splits, applies explicit
history/tail/ordinal filtering, and atomically publishes joined Parquet with a validated frozen
dataset manifest. The Python `train` command authenticates that frozen dataset, fits the required
prior/logistic/histogram-gradient-boosting baselines with training-only preprocessing, selects on
validation log loss, evaluates test rows once, and publishes predictions, metrics, calibration and
safe diagnostics without serialising executable model objects. The Python package also exposes the
validated simulated-order state machine, deterministic market-first latency scheduler and
exact-known visible queue/fill model plus checked accounting/risk/liquidation primitives; the
causal calibrated inventory-aware baseline and validation-frozen bounded signal strategy are
available as strategy primitives. The Python `simulate` command authenticates that complete
lineage, selects only on validation, runs the fixed latency/cost grid for both strategies and
publishes immutable orders, fills, liquidations, equity, metrics and diagnostics. The Python
`report` command authenticates a completed experiment or simulation and its full lineage before
atomically publishing deterministic Markdown and/or HTML,
static SVG calibration plots, text summaries and relative reproduction commands.

## Local setup

### Prerequisites

- macOS 13+ on Apple Silicon or a current x86-64 Linux distribution.
- CMake 3.25 or later.
- A C++20 compiler: Apple Clang 15+, Clang 16+, or GCC 13+.
- clang-tidy for the consolidated security gate; set `ITCHLAB_CLANG_TIDY` to its exact path when it
  is not on `PATH`.
- zlib development headers and library.
- Python 3.11 or later.
- Git.
- Enough local storage for the chosen ITCH files. Full-day compressed samples can be several gigabytes each.

### Bootstrap

After cloning the repository, run the following from its root:

    cd itch-lab
    cmake --preset dev
    cmake --build --preset dev
    python3 -m venv .venv
    source .venv/bin/activate
    python -m pip install --require-hashes -r python/requirements-dev.lock
    python -m pip install --no-build-isolation --no-deps -e ./python

Raw market data must be downloaded directly from the authorised source and placed beneath data/raw. It must not be committed.

## Environment variables

No secrets are required for the MVP.

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| ITCHLAB_DATA_DIR | No | ./data | Data root used by `doctor`; it checks the existing `derived/` child |
| ITCHLAB_RUNS_DIR | No | ./runs | C++ replay default and existing run root checked by `doctor` |
| CMAKE_BUILD_PARALLEL_LEVEL | No | tool default | Parallel C++ build jobs |

Command-specific options and versioned configs define research inputs and outputs. Config files
must not contain machine-specific absolute paths in publishable evidence.

## Development commands

    cmake --preset dev
    cmake --build --preset dev
    cmake --preset release
    cmake --build --preset release
    ./build/dev/itchlab --help
    python -m itchlab_research --help
    python -m itchlab_research doctor --binary ./build/dev/itchlab

The implemented synthetic vertical slice can be exercised without licensed market data:

    ./build/dev/itchlab inspect \
        --input tests/fixtures/synthetic_minimal.itch \
        --all \
        --symbols AAPL
    ./build/dev/itchlab replay \
        --config configs/replay.diagnostic.example.json \
        --output-root runs/task-015-example
    ./build/dev/itchlab validate \
        --run runs/task-015-example/replay/<replay-id> \
        --verify-source tests/fixtures/synthetic_minimal.itch \
        --deep
    ./build/dev/itchlab validate \
        --file tests/golden/interchange/synthetic_events_v1.ilb \
        --deep
    python -m itchlab_research convert \
        --config configs/conversion.example.json
    python -m itchlab_research build-dataset \
        --config configs/dataset.example.json
    python -m itchlab_research train \
        --config configs/experiment.example.json
    python -m itchlab_research report \
        --run-id <experiment-id> \
        --output-format both

The replay command accepts one or more configured symbols, applies selected pre-session events to
warm each book, and limits snapshots to the configured half-open session and optional tradable-state
filter. It writes `events.ilb`, `snapshots.ilb` and `replay-manifest.json` beneath
`<output-root>/replay/<replay-id>/`. The command verifies source and executable bytes, records exact
lineage/build metadata, reuses an already verified identical run by default and supports an
explicit immutable `--force-new-run`. Failed or cancelled work remains in a `.partial` staging
directory and never receives a completed manifest.

`itchlab validate` defaults to shallow validation. For a completed replay it checks the strict
manifest schema and lineage, child sizes and SHA-256 hashes, declared record counts, binary headers,
symbol dictionaries and cross-file identity. For a standalone event-v1 or snapshot-v1 file it
checks the supported schema, declared size/count and reports the computed SHA-256. Add `--deep` to
stream every record and verify source ordering, validity flags, canonical null/depth encodings and,
for a replay, reconstructed final book counts and digests. `--verify-source <path>` also hashes the
exact stored source bytes against the recorded source identity. Use `--format json` for the stable
result envelope. The command never repairs an artefact; invalid paths exit 3 and validation,
tampering, partial-status or unsupported-version failures exit 7.

Before running `convert`, set `replay_manifests` in the conversion config to one or more completed
replay manifests. The command writes Zstandard Parquet beneath
`<output_root>/conversion/<conversion-id>/`, partitioned by trading date and URI-encoded symbol,
and publishes `conversion-manifest.json` only after schema, order, row-count and hash validation.
Degraded replay parents require `allow_degraded`; cancellation or write failure retains only a
`.partial` run. Use `--format json` for a stable machine-readable result, or `--force-new-run` to
create another immutable timestamped directory for the same content identity.

Before running `build-dataset`, set safe relative `conversion_manifests`, the exact selected symbols
and tick sizes, feature/label settings and non-overlapping chronological day lists in
`configs/dataset.example.json`. The command revalidates all conversion and replay lineage, writes
joined Zstandard Parquet beneath `runs/dataset/<dataset-id>/`, and publishes a strict manifest only
after row-drop, label-availability, class and split counts reconcile. Cancellation or write failure
retains only a `.partial` run. Use `--format json` for stable output, or `--force-new-run` for another
immutable timestamped directory with the same identity.

Before running `train`, set `dataset_manifest` in `configs/experiment.example.json` to a completed
dataset manifest. The command authenticates every dataset child, writes beneath
`runs/experiment/<experiment-id>/`, and publishes `experiment-manifest.json` only after validation
and test predictions, metrics and safe diagnostics are complete. It never reads pickle/joblib model
objects; reproduction retrains from the recorded config, seed, parent hash and package-content
digest. A matching completed run is revalidated and reused unless `--force-new-run` is supplied.

Set both manifest locators in `configs/simulation.example.json`, leave `signal_weight_ticks` null
for validation-only selection, then run `simulate`. It runs the fixed 3×2 test grid for the
inventory-only control and selected signal strategy; a distinct configured execution cell is
retained. Completed output is beneath `runs/simulation/<simulation-id>/` and is revalidated/reused
unless `--force-new-run` is supplied.

Run `report` with the completed experiment or simulation ID. The command reauthenticates the full
lineage, then writes an immutable bundle beneath
`runs/report/<run-id>/<markdown|html|both>/`. The bundle includes the requested report form and
canonical config snapshots; reports with an experiment parent also include machine-readable
predictive calibration data and accessible static SVG plots. A
byte-identical completed bundle is reused; an inconsistent completed bundle or retained partial
bundle is never overwritten. Report reproduction commands and links use relative paths, and no
runtime download is attempted.

    python -m itchlab_research simulate --config configs/simulation.example.json
    python -m itchlab_research report --run-id <simulation-id> --output-format both

## Official-data study

TASK-031 executed the complete frozen pipeline for AAPL, MSFT and AMZN over chronological train,
validation and test sample days. The [study evidence](docs/studies/TASK-031/EVIDENCE.md) records exact
source identities, authenticated run lineage, full-day validation and a byte-identical reproduction
spot-check. The generated [Markdown report](docs/studies/TASK-031/report/report.md) and
[HTML report](docs/studies/TASK-031/report/report.html) retain all predictive and conservative
simulation outcomes, including the predominantly negative P&L results.

Official source files and bulk run outputs are not distributed. Reproduction requires separately
authorised files matching the recorded basenames, sizes and SHA-256 values; the application never
downloads them at runtime.

## Testing and quality commands

The first synthetic inspect/replay slice has a clean-checkout smoke command. It configures and
builds the development preset, verifies the fixed fixture corpus, checks repeatable binary replay
output and confirms that a corrupt gzip source cannot publish a completed replay directory:

    ./scripts/ci/task008-smoke.sh

    ctest --preset dev
    cmake --preset sanitizers
    cmake --build --preset sanitizers
    ctest --preset sanitizers
    cmake --preset coverage
    cmake --build --preset coverage
    ctest --preset coverage
    cmake --preset fuzz
    cmake --build --preset fuzz
    ctest --preset fuzz -R SEC-FUZZ-001
    python -m pytest python/tests
    python -m ruff check python
    python -m ruff format --check python
    python -m mypy python/src
    python -m build --no-isolation python
    python scripts/ci/check_docs.py
    python scripts/ci/check_traceability.py
    python -m coverage erase
    python -m coverage run --branch --source=itchlab_research -m pytest python/tests
    python -m coverage json -o build/python-coverage.json
    python scripts/ci/check_coverage.py build/python-coverage.json
    ./scripts/ci/cpp-coverage.sh

The consolidated TASK-028 gate additionally runs clang static analysis, the reviewed secret
baseline, the hashed dependency audit and a fail-closed network-isolated synthetic smoke:

    ./scripts/security/task028-security.sh

Apple Clang distributions without libFuzzer use the deterministic ASan/UBSan corpus driver for
local fuzz checks. The dedicated security CI job requires real libFuzzer and fails configuration
if its runtime is missing.

Build a local release candidate into a new, narrow output directory:

    python scripts/release/build_release.py \
        --output-root /tmp/itchlab-release-0.1.0

The builder requires a clean worktree for a publishable candidate, checks the three public version
declarations, builds and installs the native binary from a fresh Release tree, builds the Python
wheel/source distribution, creates a deterministic source archive, validates every archive member
and writes `SHA256SUMS` plus `RELEASE-METADATA.json`. It produces only the current native platform:
`macos-arm64` or `linux-x86_64`. Raw data, bulk derived data, run output, symlinks, `.DS_Store` files
and unsafe archive paths are rejected or excluded. `--allow-dirty-candidate` is for explicit local
diagnostics only and records `publishable: false`; it must not be published.

The full release smoke prepares a hashed dependency wheelhouse, installs the built wheel and native
archive into clean temporary locations without an index, then disables network access and runs the
synthetic inspect→replay→validate→convert→dataset→train→simulate→report path:

    ./scripts/release/task030-release-smoke.sh

When validating an uncommitted local change, set
`ITCHLAB_ALLOW_DIRTY_RELEASE_CANDIDATE=1`; CI and publishable release checks do not set it. The smoke
removes its bounded temporary workspace on exit and does not publish, stage, commit or upload any
artefact.

Generate the deterministic, untracked performance fixture and run the release benchmark:

    python -m tests.fixtures.generate_performance
    cmake --preset release
    cmake --build --preset release
    ./build/release/itchlab benchmark \
        --fixture data/fixtures/performance.itch \
        --stage all \
        --output benchmark.json

The fixture bytes remain outside Git; the recipe, expected hash and measured PERF-001–008 results
are documented in [the TASK-029 performance note](docs/performance/TASK-029-performance.md).

## Documentation

- [Project overview](docs/00-project-overview.md)
- [Product requirements](docs/01-product-requirements.md)
- [User flows](docs/02-user-flows.md)
- [System architecture](docs/03-system-architecture.md)
- [Data model](docs/04-data-model.md)
- [Module and command contracts](docs/05-api-contracts.md)
- [Command-line interface specification](docs/06-frontend-specification.md)
- [Security and privacy](docs/07-security-and-privacy.md)
- [Testing strategy](docs/08-testing-strategy.md)
- [Deployment and releases](docs/09-deployment.md)
- [Implementation plan](docs/10-implementation-plan.md)
- [Traceability matrix](docs/11-traceability.md)
- [Feature catalogue](docs/12-feature-catalogue.md)
- [v0.1.0 final review](docs/release/v0.1.0-review.md)
- [Architecture decisions](docs/decisions/)
- [Consolidated specification](FULL_PROJECT_SPECIFICATION.md)

## Authoritative-source order

When documents conflict, use this order:

1. Accepted architecture decision records.
2. Product requirements.
3. System architecture, data model and contracts.
4. Testing strategy and implementation plan.
5. README, TASKS and consolidated specification.

Do not silently resolve a conflict in code. Update the authoritative documents and add or amend an ADR first.
