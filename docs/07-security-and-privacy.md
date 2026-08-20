# 07 — Security and privacy

## Scope and security posture

ITCH-Lab is a local, offline research application. It has no accounts, listening ports, cloud service, payment path, personal-data workflow or live order connection. The realistic MVP risks are malformed binary input, unsafe filesystem behaviour, memory errors in C++, dependency compromise, unsafe model artefacts and misleading research output.

Enterprise controls such as OAuth, role-based access control, web application firewalls, multi-region failover and central SIEM integration are unnecessary for this MVP.

## Assets

| Asset | Security property |
| --- | --- |
| Developer/reviewer workstation | No arbitrary code execution, memory corruption or destructive writes |
| Raw market data | Integrity, lawful handling and no accidental redistribution |
| Derived datasets/manifests | Integrity, provenance and immutability |
| Research conclusions | Reproducibility and resistance to leakage/manipulation |
| Repository/release artefacts | Dependency and build integrity |
| Local paths/environment | No unnecessary disclosure in published outputs |

Raw public market data is not treated as personal data, but it may be licensed. No API key or user credential is needed.

## Threat model

Potential threat sources:

- A corrupted or deliberately malformed ITCH/gzip file.
- A malicious config, manifest, interchange file or report field.
- An accidental path that overwrites source or unrelated local data.
- A malicious repository/dependency change.
- An untrusted Python model artefact using executable serialisation.
- A researcher, including the project owner, unintentionally introducing leakage or optimistic fills.
- A third party misreading a historical simulation as trading advice or live performance.

Out-of-scope adversaries:

- Remote attackers against a running service, because no service exists.
- Users bypassing one another's permissions, because the MVP is single-user and uses OS filesystem controls.
- Exchange-network interception, because no live network receiver exists.

## Security requirements

| ID | Classification | Requirement | Acceptance criteria |
| --- | --- | --- | --- |
| SEC-001 | Confirmed requirement | Validate every binary boundary before access. | Known messages require exact payload length; frames have a hard maximum; six-byte and other integer reads operate only on validated spans; fuzz/sanitizer corpus causes no crash or out-of-bounds access. |
| SEC-002 | Recommendation | Detect integer overflow and invalid domain arithmetic. | Length, offset, quantity, cash and record-size operations use checked arithmetic; overflows return stable errors; dedicated boundary tests cover maximum values. |
| SEC-003 | Recommendation | Constrain filesystem writes to explicit run roots. | Output paths are resolved; source/output aliasing is rejected; final directories are never recursively replaced; writers use unique partial paths and atomic publication; tests cover traversal-like config paths and symlinks. |
| SEC-004 | Recommendation | Preserve artefact integrity and provenance. | Completed manifests contain SHA-256 for parents and children; downstream commands verify expected hashes; mismatch is fatal by default; MD5 from a source may be recorded but is not treated as a security checksum. |
| SEC-005 | Confirmed requirement | Perform no runtime network communication. | Normal execution succeeds with network disabled; no telemetry, automatic data download, model download or remote logging occurs; network attempts fail CI policy tests where practical. |
| SEC-006 | Recommendation | Avoid executable/untrusted serialisation. | Required interchange uses explicit binary, JSON and Parquet formats; no command loads pickle/joblib from an arbitrary path; NumPy loading uses allow_pickle=False. |
| SEC-007 | Recommendation | Keep secrets out of code/config/logs. | MVP config schemas contain no credential fields; secret-pattern scanning runs in CI; future credentials, if ever introduced, must use environment/OS secret storage and receive an ADR. |
| SEC-008 | Recommendation | Pin, review and scan dependencies. | C++ and Python dependency constraints are committed; licences are recorded; automated vulnerability scanning runs in CI; critical/high findings block release unless a time-bounded documented exception exists. |
| SEC-009 | Recommendation | Escape generated report content. | Symbols, paths and config text are treated as data; HTML generation escapes special characters; no raw HTML from data is trusted; tests cover script/Markdown injection strings. |
| SEC-010 | Recommendation | Minimise logs and publishable metadata. | Normal logs omit raw payloads; publishable manifests use relative paths/basenames; debug hex dumps are opt-in and capped; tests assert absolute home/workspace paths are absent from example reports. |
| SEC-011 | Confirmed requirement | Mark data and research limitations honestly. | Raw files are gitignored and not packaged; synthetic fixtures are labelled; reports state historical/simulated status, fill assumptions, venue limitations and absence of profitability guarantees. |
| SEC-012 | Recommendation | Resist resource-exhaustion inputs. | Parsing is streaming; allocations are capped; unsupported huge frames fail; progress/cancellation remain responsive; disk-space/write failures cannot publish completed artefacts. |

## Threat analysis

| Threat | Likelihood | Impact | Controls |
| --- | --- | --- | --- |
| Truncated/oversized frame causes memory fault | Medium | High | SEC-001, SEC-002, sanitizers, fuzzing |
| Decompression bomb or enormous legitimate source fills disk/memory | Medium | Medium | Streaming decompression, no raw expansion, bounded buffers, space checks, cancellation |
| Malicious output path overwrites source/unrelated file | Low–medium | High | SEC-003, run-root ownership, no broad recursive deletion |
| Derived file tampering changes results | Medium | High | SHA-256 lineage, completed manifests, validator |
| Pickled model executes code | Low | High | SEC-006; retrain rather than load arbitrary model |
| Dependency compromise | Low | High | Version constraints, minimal dependencies, scanners, review |
| HTML report injection via symbol/path | Low | Medium | SEC-009 |
| Local path or payload disclosure in public repository | Medium | Medium | Gitignore, relative manifests, log minimisation |
| Look-ahead leakage creates false result | High without controls | High | Isolated feature/label stages, whole-day splits, leakage tests |
| Optimistic fill model creates false P&L | High without controls | High | Queue/latency state machine, sensitivity, limitations |
| User mistakes research tool for live-trading system | Medium | Medium | No broker integration; explicit wording and non-goals |

## Authentication and authorisation

Not applicable to the MVP:

- There is no login or identity store.
- There is no remote caller.
- The operating system controls file access.
- Personas in product requirements are documentation roles, not permission-bearing accounts.

If a remote or multi-user surface is proposed, authentication, object-level authorisation, session management, rate limiting and audit logging require a new threat model and ADR before implementation.

## Input-validation threats

### Binary feed

- Validate outer length before reading payload.
- Validate exact known-type length before reading fields.
- Decode integers with explicit functions, not reinterpret_cast to packed structs.
- Check timestamp is within one day.
- Check Price4 and quantity semantics before book mutation.
- Apply mutations atomically so a failed event cannot leave half-updated state.
- Unknown types may be skipped only when the trusted outer frame length bounds them.

### JSON config and manifests

- Reject unknown properties.
- Validate syntax with JSON Schema and cross-field semantics in domain constructors.
- Reject NaN/infinity where numeric libraries permit them.
- Never accept a command name, shell fragment or Python expression from config.
- Canonicalise for hashing without executing content.

### Parquet/interchange

- Verify parent manifest and content hash before reading.
- Bound row-group/batch sizes.
- Enforce expected columns/dtypes and reject extras where schema version requires.
- Do not trust embedded file paths or metadata as executable references.

## Injection risks

- Shell injection: application code must use library APIs, not construct shell command strings from paths/config.
- HTML injection: escape all data-derived strings; scripts are not required for the static report.
- Formula injection: CSV is not a primary output. Diagnostic CSV export, if later added, must escape cells beginning with =, +, - or @.
- SQL injection: not applicable; no database.
- Code/model injection: no eval, exec, arbitrary plugin loading or untrusted pickle.

## Local-file and “upload” risks

There is no upload endpoint. Local files are still untrusted.

- Resolve input/output paths independently.
- Reject input/output identity using file identity where available, not string comparison alone.
- Never extract arbitrary archives supplied by the feed.
- gzip input is streamed; embedded filenames are ignored.
- Temporary names are generated inside the selected run root.
- Cleanup lists exact run-owned partial files; it does not accept a broad glob or root path.

## Secret management

The MVP has no secrets. Environment variables listed in README configure paths/log levels only. If future data access needs credentials:

1. Do not commit them or store them in run manifests.
2. Use environment variables or OS credential storage.
3. Redact them from command lines/logs.
4. Add secret-scanning tests and an ADR.

## Encryption

- In transit: not applicable because runtime networking is prohibited.
- At rest: rely on operating-system full-disk encryption and filesystem permissions; application-level encryption is not warranted for public/sample market data.
- Integrity hashes are not encryption or authentication and must not be described as such.
- Published release checksums allow corruption detection but do not replace signed releases. Release signing is deferred.

## Logging restrictions

Normal logs may include:

- Relative/basename path.
- Message index, source offset, timestamp, type, symbol and order/match identifiers.
- Counts, durations and stable error codes.

Normal logs must not include:

- Entire raw messages or large hex dumps.
- Environment-variable dumps.
- User home directory in publishable artefacts.
- Source signed URLs, credentials or access tokens.
- Full per-message traces during ordinary runs.

## Data minimisation, retention and deletion

- Process only configured symbols for derived research output.
- Retain no personal data.
- Do not copy raw files into run directories.
- Do not package raw or bulk derived data with repository releases.
- No automatic deletion policy is necessary for local user-managed data.
- An explicit cleanup command is post-MVP; until then, deletion is manual and documented.
- A report should include hashes and public source landing pages, not redistribution of input bytes.

## Dependency risks

- Prefer standard library and a small dependency set.
- Pin FetchContent/vcpkg references to immutable versions or commits.
- Constrain Python direct dependencies and commit a resolved environment for releases.
- Run pip-audit or equivalent and a C/C++ dependency scanner where supported.
- Review licences for compatibility with public portfolio distribution.
- Avoid packages that download executables/models during import.
- CI actions are pinned to full commit SHAs for release hardening.

## Abuse and misuse cases

| Case | MVP response |
| --- | --- |
| User tries to connect to a broker | No interface exists; request is outside documented scope |
| User presents simulated P&L as live | Report labels every result historical/simulated and includes assumptions |
| User tunes on the test set repeatedly | Run lineage records configs; exploratory reruns are labelled and cannot replace original result |
| User supplies an arbitrary model file | Loading is rejected; reproduce by retraining from config |
| User points output at repository/root/source | Preflight rejects unsafe alias/broad destination |
| User attempts permissive-mode publication | Output is marked degraded; downstream rejects unless explicitly overridden and disclosed |

## Security acceptance criteria

Release is blocked unless:

1. AddressSanitizer and UndefinedBehaviourSanitizer tests pass on maintained fixtures.
2. Fuzz corpus runs for the configured CI budget without a crash.
3. Path-safety tests confirm source and unrelated sentinels are unchanged after success, failure and cancellation.
4. Binary/manifest hash tampering is detected.
5. No required workflow loads executable serialisation.
6. Network-disabled synthetic end-to-end execution passes.
7. Dependency and secret scans have no unaccepted high/critical finding.
8. Generated HTML passes injection fixtures.
9. Example/public manifests contain no workspace-specific absolute paths.
10. Report includes the prescribed simulation/research limitations.

## TASK-028 threat review and completion evidence

Review date: 2026-08-17. The implemented system still matches the local, single-user and offline
threat model above. Source/config/manifest/interchange/Parquet/report fields remain the trust
boundaries; no account, credential, database, listener, broker, telemetry or runtime-download
surface exists. The review found no unaccepted high- or critical-severity issue and introduced no
security exception. Hosted CI execution is observational evidence supplied by the next workflow
run; the equivalent gates below passed locally before TASK-028 was closed.

| Acceptance criterion | Completion evidence |
| --- | --- |
| 1. ASan/UBSan | The complete sanitizer preset executed all 128 locally runnable CTest cases without a finding; the opt-in authorised official-data case was the single documented skip. |
| 2. Fuzz budget | `SEC-FUZZ-001` ran 10,000 mutations for each framing and decoder target over committed synthetic boundary seeds without a crash. Apple Clang lacks libFuzzer, so the preset uses the deterministic ASan/UBSan corpus driver; the same budget also passed locally with LLVM 22.1.8 real libFuzzer, which CI explicitly requires. |
| 3. Path safety | `SEC-PATH-001`, TASK-012 cancellation and Python conversion tests cover aliases, symlink roots/staged files and traversal-like paths. Source and unrelated sentinel bytes remain unchanged after success, failure and cancellation. |
| 4. Hash tampering | IT-004 and IT-012 authenticate completed parent/child lineage and reject modified manifests/interchange before deep record use. |
| 5. Safe serialisation | The repository security-policy test rejects project imports of pickle/joblib/dill/marshal, `eval`/`exec` and NumPy loads without literal `allow_pickle=False`; reproduction retrains instead of loading executable models. |
| 6. Network disabled | The synthetic C++ replay/validation and Python simulation smoke passed inside a macOS sandbox that denied socket creation. Linux CI uses a distinct network namespace and fails closed when isolation cannot be established. |
| 7. Dependency/secrets | `pip-audit` found no known vulnerability in the hashed release lock. `detect-secrets` found zero real secrets; 17 reviewed entropy findings are fixed hashes and partition/path test literals recorded as false positives in the baseline. |
| 8. Report injection | Markdown and HTML tests cover `<script>` and Markdown link/table metacharacters and require escaped output with no trusted raw data HTML. |
| 9. Private paths | Policy tests scan public configs and minimal golden manifests for the repository and home paths; existing manifest/report recursive assertions also pass. |
| 10. Honest limitations | Predictive and simulation report tests require historical/simulated status, fill/latency/venue assumptions and the absence of profitability guarantees. |

Local evidence toolchain:

- CMake 3.27.6; Apple Clang/ASan/UBSan 21.0.0; LLVM/libFuzzer and clang-tidy 22.1.8,
  with `clang-analyzer-*` over all 28 project translation units and compiler warnings as errors.
- Python 3.11.5; pytest 9.1.1 (613 tests); Ruff 0.16.1; mypy 2.3.0.
- detect-secrets 1.5.0 and pip-audit 2.10.1. Scanners run without secret verification network
  calls; the vulnerability audit reads the pinned release requirements.

The committed dependency inventory reviewed for public distribution is:

| Dependency | Locked/reviewed version | Licence |
| --- | --- | --- |
| zlib | 1.2.12 C++ SDK library on the review host | zlib |
| nlohmann/json | 3.12.0 / commit `55f93686c01528224f448c19128836e7df245f72` | MIT |
| Catch2 (test only) | 3.8.1 / commit `2b60af89e23d28eefc081bc930831ee9d45ea58b` | Boost-1.0 |
| Google Benchmark (benchmark only) | 1.9.4 / commit `eddb0241389718a23a42db6af5f0164b6e0139af` | Apache-2.0 |
| attrs | 26.1.0 | MIT |
| joblib | 1.5.3 | BSD-3-Clause |
| jsonschema | 4.26.0 | MIT |
| jsonschema-specifications | 2025.9.1 | MIT |
| narwhals | 2.24.0 | MIT |
| NumPy | 2.4.6 | BSD-3-Clause plus bundled permissive notices |
| packaging | 26.2 | Apache-2.0 or BSD-2-Clause |
| PyArrow | 23.0.1 | Apache-2.0 |
| referencing | 0.37.0 | MIT |
| rfc8785 | 0.1.4 | Apache-2.0 |
| rpds-py | 2026.6.3 | MIT |
| scikit-learn | 1.9.0 | BSD-3-Clause |
| SciPy | 1.17.1 | BSD-3-Clause plus bundled binary notices |
| threadpoolctl | 3.6.0 | BSD-3-Clause |
| typing-extensions | 4.16.0 | PSF-2.0 |
| wheel | 0.47.0 | MIT |
| setuptools | 83.0.0 | MIT |

Python versions and hashes are authoritative in `python/requirements-release.lock`; the table is
the human review record. No listed licence blocks the intended public source/binary distribution,
provided bundled notices are retained.

## Security testing requirements

- Unit tests for all validation boundaries and checked arithmetic.
- Property tests for arbitrary legal order lifecycles and simulator state.
- libFuzzer/AFL-compatible targets for framing and message decoding.
- Sanitizer CI build and a longer scheduled/local fuzz run.
- Static analysis with compiler warnings-as-errors and clang-tidy security/correctness checks.
- Dependency and secret scanning.
- Malicious config/manifest/path fixtures.
- HTML/Markdown escaping tests.
- Failure-injection tests for short reads, disk errors and cancellation.

Penetration testing and web dynamic scanners are not relevant until a network surface exists.
