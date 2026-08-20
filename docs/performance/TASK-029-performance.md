# TASK-029 performance evidence

## Scope and method

This note records the synthetic release benchmark captured on 2026-08-18. The deterministic
fixture recipe emits 1,000,003 valid `itch-length-v1` messages: two Stock Directory records, one
System Event and 100,000 repeated selected/filtered order-lifecycle cycles. At most two selected
orders are live, and every cycle returns the selected AAPL book to its canonical empty state.

The uncompressed fixture is 29,700,096 bytes with SHA-256
`63a4345dae3045260e374a423c9c1668f75b121b1d8a2c811b5845eb17c0c1cc`. Its deterministic gzip
form is 6,068,129 bytes with SHA-256
`da8104baf8f910f8b3c163d5bbb0d4fe6c307ee762ff861db98cb202042e6cd3`. Fixture bytes and generated
metadata are deliberately ignored by Git; the generator is the committed source of truth.

Measurements used ten timed repetitions after one warm-up and report the median and median absolute
deviation (MAD), never the best run. The host was an Apple M2 Pro MacBook Pro running macOS 26.3.1.
The Release binary used Apple Clang 21.0.0.21000101 with `-O3 -DNDEBUG`. The current shell and CMake
toolchain target were x86_64 under Rosetta; these results must not be presented as native arm64
numbers. Peak RSS is process high-water RSS and therefore an upper bound across the requested pass
set.

## Profile and optimisation

The unoptimised PERF-004 median was 6,285,288 messages/s (MAD 54,227 messages/s). An Instruments
16.0 Time Profiler capture of the same Release workload produced 849 resolved samples. Of these,
324 included `OrderBook::apply`, 110 included `operator new`, 102 included
`OrderBook::apply_add`, and 95 included `OrderBook::apply_replace`. This identified allocator churn
in the ordered-map, FIFO-list and order-index nodes as the measured book bottleneck.

The sole optimisation changes those node containers to use a per-book
`std::pmr::unsynchronized_pool_resource`. It preserves the existing ordered maps, FIFO semantics,
domain types and canonical digest. Freed nodes can be reused during a book's lifetime; the pool and
all retained capacity are destroyed with that book. The trade-off is high-water rather than
immediate node-memory return while a book remains alive. No dependency, interchange change or new
architectural layer was introduced.

After optimisation, the directly comparable PERF-004 median was 9,659,384 messages/s (MAD 22,774
messages/s), a 53.7% increase. The final AAPL state digest remained
`47213ce72b18bbb9fb839f064fb00c71d810d21c19e1fe74a9ed61162c0d2a6c`. The Google Benchmark
allocation counter on the 10,003-message reduced fixture fell from 0.902929 to 0.003599
allocations/message. The full post-change run below produced a 9,714,604 messages/s PERF-004
median, comfortably above NFR-003's 1,000,000 messages/s floor; no revised ADR is required.

## PERF-001–008 results

| ID | Release result | Dispersion or memory evidence |
| --- | ---: | --- |
| PERF-001 framing | 20,153,690 messages/s; 598,564,727 bytes/s | MAD 12,964 messages/s |
| PERF-002 mixed decode | 14,909,768 messages/s | MAD 34,389 messages/s |
| PERF-003 directory/filter | 13,580,540 messages/s | MAD 34,852 messages/s |
| PERF-004 parser/book | 9,714,604 messages/s; 0.003599 allocations/message | MAD 43,066 messages/s; unchanged digest |
| PERF-005 gzip pipeline | 1,386,945 messages/s; 8,416,137 compressed bytes/s; 41,192,281 uncompressed bytes/s | MAD 1,357 messages/s; unchanged digest |
| PERF-006 snapshot writer | 7,901,428 records/s; 328 bytes/record | MAD 167,448 records/s |
| PERF-007 binary-to-Parquet | 22,408 records/s; 231,849,984 bytes peak RSS | 120,000 records, zstd, 4,096-row groups |
| PERF-008 large streaming | 3,997,696 bytes C++ plain-run peak RSS; 142,163,968 bytes Python RSS growth | Python traced peak below 128 MiB and RSS growth below 256 MiB |

The gzip process peak RSS was 4,923,392 bytes. PERF-007/008 use the existing authenticated
binary-to-Parquet conversion fixture and assert bounded row groups as well as memory limits. The
synthetic fixture is intentionally churn-heavy and shallow; absolute rates are not predictions for
a full official trading day, a larger live book, native arm64, or storage-constrained hardware.

## Reproduction

Run from the repository root with the locked Python environment installed:

```sh
python -m tests.fixtures.generate_performance
python -m tests.fixtures.generate_performance --check
cmake --preset release
cmake --build --preset release
./build/release/itchlab benchmark \
  --fixture data/fixtures/performance.itch --stage all --repetitions 10 \
  --output benchmark-plain.json --format json
./build/release/itchlab benchmark \
  --fixture data/fixtures/performance.itch.gz --stage all --repetitions 10 \
  --output benchmark-gzip.json --format json
ITCHLAB_BENCHMARK_FIXTURE=data/fixtures/performance.itch \
  ./build/release/itchlab_benchmarks --benchmark_filter=PERF-004
.venv/bin/python -m pytest -s \
  python/tests/test_conversion.py::test_task_017_perf_007_perf_008_large_stream_conversion_throughput_and_memory
```

For a new profile capture on macOS:

```sh
xcrun xctrace record --template 'Time Profiler' --output task029.trace --launch -- \
  ./build/release/itchlab benchmark \
  --fixture data/fixtures/performance.itch --stage book --repetitions 3
```

Trace bundles and full JSON samples are local generated evidence and are not committed because they
contain machine metadata and add no authoritative behaviour beyond this reviewed summary.
