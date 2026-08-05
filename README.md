# ITCH-Lab

ITCH-Lab is an offline quantitative-research platform that decodes Nasdaq TotalView-ITCH 5.0 binary market data, reconstructs selected instruments' level-3 visible order books, produces reproducible research datasets, and evaluates short-horizon signals and inventory-aware market-making strategies under explicitly documented queue, latency and cost assumptions.

## Status

Specification complete. The reproducible C++/Python foundation, bounded plain/gzip framed-input
layer, stateless full-MVP decoder, deterministic minimal add/delete level-3 book and first
inspect/replay command slice are implemented. Replay currently writes explicitly provisional JSONL
diagnostics for one synthetic symbol and applies only the initial A/D lifecycle; production
interchange, manifests, the full lifecycle, research and simulation remain planned.

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
| Research package | Python 3.11+, NumPy, Polars, scikit-learn, PyArrow | Confirmed requirement for Python; libraries are recommendations |
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

The repository structure is being implemented incrementally according to `TASKS.md`. The current
foundation provides buildable version/help CLIs, fixed C++ domain/error types, strict validated
configuration contracts with canonical cross-language hashes, and bounded streaming of verified
`itch-length-v1` plain/gzip sources. It decodes S/R/H/A/F/E/C/X/D/U/P/Q/B with exact length,
big-endian and timestamp validation, applies the initial add/delete events to a deterministic
level-3 book, and exposes bounded inspect plus one-symbol diagnostic replay commands. Production
binary replay artefacts and manifests, the full order lifecycle, research and simulation commands
are implemented by later tasks.

## Local setup

### Prerequisites

- macOS 13+ on Apple Silicon or a current x86-64 Linux distribution.
- CMake 3.25 or later.
- A C++20 compiler: Apple Clang 15+, Clang 16+, or GCC 13+.
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
| ITCHLAB_DATA_DIR | No | ./data | Root for raw and derived market data |
| ITCHLAB_RUNS_DIR | No | ./runs | Root for immutable experiment-run outputs |
| ITCHLAB_LOG_LEVEL | No | info | debug, info, warning or error |
| CMAKE_BUILD_PARALLEL_LEVEL | No | tool default | Parallel C++ build jobs |

Command-line arguments override environment variables; environment variables override defaults. Config files must not contain machine-specific absolute paths.

## Development commands

    cmake --preset dev
    cmake --build --preset dev
    cmake --preset release
    cmake --build --preset release
    ./build/dev/itchlab --help
    python -m itchlab_research --help

The implemented synthetic vertical slice can be exercised without licensed market data:

    ./build/dev/itchlab inspect \
        --input tests/fixtures/synthetic_minimal.itch \
        --all \
        --symbols AAPL
    ./build/dev/itchlab replay \
        --config configs/replay.diagnostic.example.json \
        --output-root runs/task-007-example

The replay command above requires a fresh output root and writes deterministic
`diagnostic-events.jsonl` and `diagnostic-snapshots.jsonl`. These files are labelled provisional;
they are not `events.ilb`, `snapshots.ilb` or a completed replay manifest.

Planned research workflow (implemented by later tasks):

    python -m itchlab_research convert --config configs/dataset.example.json
    python -m itchlab_research train --config configs/experiment.example.json
    python -m itchlab_research simulate --config configs/simulation.example.json
    python -m itchlab_research report --run-id <run-id>

## Testing and quality commands

The first synthetic inspect/replay slice has a clean-checkout smoke command. It configures and
builds the development preset, verifies the fixed fixture corpus, checks repeatable golden output
and confirms that a corrupt gzip source cannot publish final diagnostic files:

    ./scripts/ci/task008-smoke.sh

    ctest --preset dev
    cmake --preset sanitizers
    cmake --build --preset sanitizers
    ctest --preset sanitizers
    cmake --preset coverage
    cmake --build --preset coverage
    ctest --preset coverage
    python -m pytest python/tests
    python -m ruff check python
    python -m ruff format --check python
    python -m mypy python/src
    python -m build --no-isolation python

The release benchmark command is added by `TASK-029`:

    ./build/release/itchlab benchmark --fixture data/fixtures/performance.itch

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
