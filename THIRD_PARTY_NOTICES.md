# Third-party notices

ITCH-Lab depends on third-party software. The authoritative versions and hashes for the Python
runtime are in `python/requirements-release.lock`; C++ revisions are pinned in `CMakeLists.txt`.
This inventory records the licences reviewed for release packaging.

## Runtime and packaged dependencies

| Dependency | Reviewed version | Licence |
| --- | --- | --- |
| zlib | Platform SDK library (1.2.12 on the review host) | zlib |
| nlohmann/json | 3.12.0, commit `55f93686c01528224f448c19128836e7df245f72` | MIT |
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

Python wheels installed alongside ITCH-Lab retain their own complete licence and notice files.
The native release archive includes this notice because nlohmann/json is compiled into the binary;
zlib remains a platform dependency. Downstream distributors remain responsible for retaining the
complete notices supplied by their platform and Python dependencies.

## Development-only dependencies

| Dependency | Reviewed version | Licence |
| --- | --- | --- |
| Catch2 | 3.8.1, commit `2b60af89e23d28eefc081bc930831ee9d45ea58b` | Boost-1.0 |
| coverage.py | 7.15.4 | Apache-2.0 |
| gcovr | 8.6 | BSD-3-Clause |
| Google Benchmark | 1.9.4, commit `eddb0241389718a23a42db6af5f0164b6e0139af` | Apache-2.0 |
| setuptools | 83.0.0 | MIT |
| wheel | 0.47.0 | MIT |

This file is an inventory, not a replacement for any dependency's licence text and not a grant of
rights in ITCH-Lab itself.
