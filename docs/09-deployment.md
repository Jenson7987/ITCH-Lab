# 09 — Development, release and operation

## Deployment model

ITCH-Lab is not a continuously running service. Deployment means:

- Building a local C++ command-line binary.
- Installing the local Python research package in an isolated environment.
- Publishing source, configuration examples, documentation and checksums as a versioned release.
- Reproducing a named research report from authorised local data.

No production server, domain, database or cloud account is required.

## Environments

| Environment | Purpose | Data | Build |
| --- | --- | --- | --- |
| Development | Fast implementation and debugging | Synthetic fixtures only by default | Debug with assertions |
| Validation (“staging”) | Full release-path verification | Local official sample files plus synthetic | Release and sanitizer builds |
| Published release (“production”) | Reproducible portfolio artefact | No raw data packaged; report/manifests only | Tagged release source and optional binaries |

Environment differences must not change research semantics. Scientific parameters live in configs, not environment variables.

## Supported release platforms

TASK-030 fixes the supported native release targets to current macOS ARM64 and Ubuntu x86-64.
GitHub Actions uses `macos-15` and `ubuntu-24.04`, asserts `arm64` and `x86_64` respectively before
building, and runs both the native test suite and installed release smoke on each platform. The
release builder validates the compiled executable with the host `file` tool and refuses an archive
whose architecture does not match `macos-arm64` or `linux-x86_64`. Other platforms may build from
source but are not release-tested MVP targets.

## Local development environment

Required:

- Git.
- CMake 3.25+ and Ninja recommended.
- Apple Clang 15+/Clang 16+/GCC 13+.
- Python 3.11+ with venv.
- zlib development headers.

Recommended setup:

    cmake --preset dev
    cmake --build --preset dev
    ctest --preset dev

    python3 -m venv .venv
    source .venv/bin/activate
    python -m pip install --upgrade "pip>=25,<26"
    python -m pip install --require-hashes -r python/requirements-dev.lock
    python -m pip install --no-deps -e ./python
    python -m pytest python/tests

CMake resolves platform zlib with find_package and other C++ dependencies with FetchContent at immutable revisions. Python direct dependencies and hashed pip-tools development/release lock files are committed.

## Required services

None at runtime.

Optional development services:

- GitHub Actions for CI.
- A source-control host for repository/release publication.

The project must remain usable without those services after checkout and dependency installation.

## Environment variables

| Variable | Secret | Default | Rules |
| --- | --- | --- | --- |
| ITCHLAB_DATA_DIR | No | ./data | `doctor` checks the existing `derived/` child; it may be absolute locally |
| ITCHLAB_RUNS_DIR | No | ./runs | C++ replay default and existing root checked by `doctor`; unsafe broad/symlink roots are rejected |
| CMAKE_BUILD_PARALLEL_LEVEL | No | tool default | Positive integer when set |
| NO_COLOR | No | unset | Any value disables colour |

There is no .env requirement. Example environment files must not be introduced unless future secrets justify them.

## Secret management

No MVP secret exists. CI may use platform-provided repository tokens for release publication, scoped to minimum permissions and unavailable to untrusted forks. They must never reach application configs or logs.

Any future market-data credential or signing key requires:

- A new ADR/threat-model update.
- Environment or OS secret-store use.
- Redaction tests.
- Rotation and revocation instructions.

## Build process

### C++ presets

| Preset | Purpose | Required properties |
| --- | --- | --- |
| dev | Daily work | Debug symbols, assertions, warnings |
| release | Benchmarks/releases | Optimisation, NDEBUG, reproducible flags where supported |
| sanitizers | Safety checks | ASan and UBSan, debug symbols |
| coverage | Coverage report | Instrumentation, no misleading optimisation |
| fuzz | Maintained parser security corpus | Clang, ASan/UBSan and libFuzzer where available; CI requires libFuzzer |

Build steps:

1. Validate toolchain versions.
2. Configure with checked-in preset.
3. Resolve pinned dependencies.
4. Compile with warnings as errors for project code.
5. Run unit/integration/contract tests.
6. Record compiler/version/flags in release metadata.

The dedicated security workflow runs on pull requests, pushes to `main`, manual dispatch and a
weekly schedule. `scripts/security/task028-security.sh` is the shared local/CI entry point. CI must
configure the fuzz preset with `ITCHLAB_FUZZ_ENGINE=libfuzzer`; local Apple Clang may use the
deterministic sanitizer corpus fallback because that toolchain does not ship a libFuzzer runtime.
The network-disabled smoke fails rather than silently running without macOS sandbox or Linux
network-namespace isolation.

`.github/workflows/ci.yml` supplies the complementary pull-request, main-branch, manual and weekly
jobs. It pins third-party actions to full commit hashes and covers documentation contracts, C++ and
Python formatting/static checks, unit/integration/contract suites, tiered Python branch coverage,
C++ compiler coverage, both supported native platforms, installed release E2E and the release
PERF-004 catastrophic-regression threshold. `.github/workflows/security.yml` remains responsible
for the sanitizer, real-libFuzzer, static-analysis, dependency, secret and network-isolation gates.
No workflow downloads or stores official market data.

### Python build

1. Create clean venv.
2. Install from locked/constrained dependencies.
3. Run Ruff, mypy and pytest.
4. Build wheel and source distribution.
5. Install the built wheel into a second clean venv.
6. Resolve the hashed release lock into a local wheelhouse, then install with `--no-index` and
   `--require-hashes`.
7. Run the synthetic E2E using only the installed wheel and native archive while network access is
   denied by the operating system.

## Release/deployment process

1. Select a clean commit on the main branch and complete the release checklist below.
2. Run `python scripts/release/build_release.py --output-root <new-directory>` independently on
   macOS ARM64 and Ubuntu x86-64.
3. Verify `SHA256SUMS` and review `RELEASE-METADATA.json`; a publishable candidate must record a
   clean tree and `publishable: true`.
4. Run `scripts/release/task030-release-smoke.sh` on both supported platforms.
5. Tag using semantic versioning, beginning at v0.1.0 for the MVP.
6. Publish the source archive, native archives, Python wheel/source distribution, release metadata
   and checksums without rebuilding them.
7. Publish release notes with schema versions, supported platforms, limitations and migration
   notes, and retain the prior release and its documentation.

The builder stages into `<output-root>.partial` and atomically renames it only after all children
and archive paths validate. The final output root must not already exist, may not be broad or a
symlink, and may not be beneath `data/raw`, `data/derived` or `runs`. It produces deterministic
source/native tarballs, a universal Python wheel, Python source distribution, release metadata and
SHA-256 checksums. Source inventory comes from Git-tracked and explicit untracked candidate files,
but raw/bulk/run paths, `.DS_Store`, symlinks, traversal and absolute archive members are excluded
or rejected. A dirty worktree fails unless `--allow-dirty-candidate` is explicit; that mode records
the candidate as non-publishable and exists only for local pre-commit verification.

Installation from a release must not execute research automatically.

## Data acquisition

- Documentation links to the official source landing page.
- The application does not automatically download data in the MVP.
- User records the trading date and optional expected SHA-256 in config.
- First replay computes the source SHA-256.
- Raw data stays beneath the user-selected data directory and is gitignored.

## Schema migrations

There is no database migration.

Binary/manifest migration rules:

1. Readers check magic and schema version.
2. A layout-breaking change adds a new schema version.
3. A migration command reads an old completed artefact, validates it and writes a new run; it never edits in place.
4. Parent identity and migration tool revision are recorded.
5. Golden fixtures for retained versions run in CI.
6. Unsupported versions fail with a specific recovery message.

Config schema changes:

- Breaking changes increment schema version.
- Example configs are updated in the same release.
- Silent reinterpretation of an old field is prohibited.

## Rollback strategy

Code rollback:

- Install/check out the previous signed/tagged release.
- Use its documented dependency resolution.
- Do not force it to read a newer unsupported schema.

Research rollback:

- Completed run directories are immutable and remain available.
- A corrected run receives a new identity.
- Published reports are never silently replaced; mark the old result superseded and link the correction.

Release rollback:

- Withdraw the affected release asset if unsafe.
- Publish an advisory explaining affected versions/data and remediation.
- Keep checksums/version history for auditability.

## Backups

| Data | Backup recommendation |
| --- | --- |
| Source/documentation/config | Git remote plus local clone |
| Raw authorised market data | User-managed backup if costly to obtain; never bundled |
| Derived bulk data | Reproducible; optional user backup |
| Completed manifests/reports | Versioned release or durable user storage |
| Benchmark/profile evidence | Commit small summaries; store large traces externally/local |

The application does not implement automatic backup. A manifest alone is not a backup of raw data.

## Monitoring and health checks

No background monitoring is needed.

Command health checks:

    itchlab --version
    itchlab inspect --input tests/fixtures/synthetic_minimal.itch --all
    itchlab validate --file tests/golden/interchange/synthetic_events_v1.ilb --deep
    python -m itchlab_research doctor --binary /path/to/itchlab

Recommended doctor output:

- Python/dependency versions.
- Ability to import required packages.
- Presence/version of C++ binary.
- Supported schema versions.
- Writable configured run/data-derived directories.
- Network is neither required nor tested.

`doctor` requires existing `ITCHLAB_RUNS_DIR` and `<ITCHLAB_DATA_DIR>/derived` directories. It
rejects symlink or filesystem-root output locations, uses and removes a bounded write probe, checks
the exact native/Python semantic version match and accumulates dependency/schema failures. Human
and schema-version-1 JSON output are available; exit 7 means an unhealthy installation. The command
does not create roots, read market data or initiate network access.

Long-run observability comes from progress logs, terminal status and immutable manifests.

## Logging

- Default logs are human-readable stderr.
- --log-format jsonl supports automation.
- The MVP has no project-owned log-file or rotation service; users may capture stderr explicitly.
- Completed manifest summaries are the durable operational record.
- Debug payload dumps are opt-in, capped and never part of a release package.

## Release health criteria

A release is healthy when:

- Synthetic inspect→replay→convert→dataset→train→simulate→report succeeds from installed artefacts.
- All produced manifests validate.
- Supported platform builds pass.
- No critical/high unaccepted dependency issue exists.
- Performance smoke is within the recorded catastrophic-regression threshold.
- Documentation links and reproduction commands work.

The local TASK-030 candidate command is:

    ITCHLAB_ALLOW_DIRTY_RELEASE_CANDIDATE=1 \
        ITCHLAB_RELEASE_PYTHON=.venv/bin/python \
        scripts/release/task030-release-smoke.sh

The dirty-candidate switch is omitted for the clean CI/release run. This command is intentionally
local: it builds and checks artefacts beneath a bounded temporary directory and publishes nothing.

## Release checklist

TASK-032 local sign-off and remaining release-owner actions are recorded in the
[v0.1.0 final review](release/v0.1.0-review.md). The checklist remains a release-time control: a
local dirty-candidate result does not satisfy the clean commit or tag steps.

- [ ] Working tree clean and release commit identified.
- [ ] Product requirements and ADRs reflect implementation.
- [ ] C++ format, build, unit, integration and contract tests pass.
- [ ] ASan/UBSan and fuzz CI budgets pass.
- [ ] Python Ruff, mypy, pytest and coverage pass.
- [ ] Synthetic E2E and network-disabled run pass.
- [ ] Accessibility/report checks pass.
- [ ] Dependency, licence and secret scans pass.
- [ ] Binary/manifest schema versions documented.
- [ ] Migration compatibility tested.
- [x] Release benchmarks captured with environment metadata (TASK-029 performance note).
- [ ] Raw/bulk data absent from Git and archives.
- [ ] Public manifests contain no absolute user paths.
- [ ] Limitations and historical/simulated labels are present.
- [ ] Source, Python and optional binary packages built and checksummed.
- [ ] Clean-environment installation and doctor check pass.
- [ ] Release notes and rollback target prepared.

## Incident-recovery considerations

### Corrupted/tampered input or output

1. Stop downstream work.
2. Preserve manifest/log diagnostics without distributing raw data.
3. Compare expected/actual hashes and reacquire authorised source if needed.
4. Re-run into a new identity.

### Parser safety defect

1. Withdraw affected release if exploitable.
2. Add minimal synthetic/fuzz regression case.
3. Patch, run sanitizers/fuzzing and publish advisory.
4. Reproduce published outputs because corruption may have affected state.

### Research leakage or simulator error

1. Mark affected report superseded; do not delete history silently.
2. Identify impacted requirements/runs.
3. Add failing test before correction.
4. Produce new dataset/experiment/simulation identities.
5. Publish corrected report with impact comparison.

### Compromised dependency

1. Determine affected release and whether dependency executes at build/runtime.
2. Pin/remove/update dependency.
3. Rebuild from clean environment.
4. Rotate publication credentials if exposure is possible.
5. Publish new checksums and advisory.

### Disk-full/interruption

1. Command must leave only partial paths.
2. Verify source/unrelated files unchanged.
3. Remove exact partial run after inspection.
4. Free space and start a fresh run; automatic resume is unavailable.
