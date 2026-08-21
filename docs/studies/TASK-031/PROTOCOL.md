# TASK-031 frozen official-data study protocol

Status: frozen on 2026-08-21 before test-partition research outcomes were inspected.

## Purpose and non-claims

This protocol executes the version-1 offline research pipeline on official Nasdaq TotalView-ITCH
5.0 sample files. It evaluates causal predictive baselines and a conservative historical
market-making simulation. It is not a live-trading system, trading advice or evidence of
profitability.

The protocol changes no scientific definition, file schema, model family, simulator assumption or
cross-language boundary. It uses the accepted version-1 contracts and defaults.

## Frozen data selection

| Partition | Trading date | Official source basename | Verified size (bytes) | Verified SHA-256 |
| --- | --- | --- | ---: | --- |
| Train | 2019-07-30 | `07302019.NASDAQ_ITCH50.gz` | 3,662,140,094 | `c65784c48c28735901ae442dc00e215834218a359bc12a139ab4eec209bc2d4a` |
| Validation | 2019-10-30 | `10302019.NASDAQ_ITCH50.gz` | 3,872,931,242 | `0ad86b61a0eb7f1bce2cffca0e08c8658026451c68657ea6b06f61ff3710b999` |
| Test | 2019-12-30 | `12302019.NASDAQ_ITCH50.gz` | 3,524,013,057 | `ef03df46a27e6bda4dead017f84c2e3979df7211f02c7868b51d53fceb99c689` |

The three dates are distinct full-day files listed in Nasdaq's official sample directory and are
spaced across five months. This gives chronological train, validation and one-shot test partitions
without random row splitting. Their order was chosen before model, label or simulation outcomes
were inspected. The selected symbols are `AAPL`, `MSFT` and `AMZN`: three liquid, actively quoted
large-cap Nasdaq instruments that exercise pooled multi-symbol processing. They were selected
before per-symbol research outcomes were inspected.

Before this freeze, preflight was limited to storage, official directory metadata, the existing
December source hash, outer framing and aggregate wire message-type discovery needed to establish
whether strict decoding could read the source. No labels, model metrics, strategy selection or
simulation results were inspected.

Official directory: <https://emi.nasdaq.com/ITCH/Nasdaq%20ITCH/>.

## Frozen replay and dataset choices

- Exchange timezone: `America/New_York`.
- Half-open session: 09:30:00 through 16:00:00 local exchange time
  (`34200000000000` through `57600000000000` nanoseconds since midnight).
- Trading-state gating: required.
- Visible depth: 10 levels; unchanged trade snapshots enabled.
- Validation: strict, zero skipped messages, invariant check after every selected mutation.
- Feature depths: 1, 5 and 10.
- Event windows: 20, 100 and 500 qualifying transitions.
- Clock windows: 100 ms and 1 s.
- Primary label horizon: 100 events; secondary horizons: 20 and 500 events.
- Flat threshold: zero ticks; qualifying-row stride: 10.
- Tick size: 100 Price4 units for every selected symbol.

Known ITCH 5.0 message types outside the visible-book MVP are accepted only after exact wire length,
common-header and timestamp validation. They are counted but do not mutate books or emit events or
snapshots. Arbitrary unknown types remain fatal in strict mode.

## Frozen predictive and simulation choices

- Predictive candidates: training-frequency prior, pooled multinomial logistic regression and
  pooled histogram gradient boosting with the grids in the version-1 experiment contract.
- Preprocessing, selection metric and tie-breaks: unchanged version-1 defaults.
- Seed: 7987.
- Signal strategy: signal-adjusted Avellaneda–Stoikov with validation-only model-family and signal
  weight selection; inventory-aware Avellaneda–Stoikov is the paired control.
- Decision interval: 100 ms; maximum prediction age: 500 ms.
- Order quantity: 100 shares; inventory limit: 1,000 shares; gamma: 0.1.
- Volatility window: 60 seconds; risk horizon: 10 seconds; maximum signal: 2 ticks.
- Passive-only execution, conservative known-order queue policy and zero queue-anomaly budget.
- Terminal inventory: cross the visible spread.
- Frozen test sensitivity grid: submission/cancellation latency of 0, 100 microseconds and
  1 millisecond, crossed with maker fee/rebate of −2,000 and 3,000 microusd per share. Taker cost is
  fixed at 3,000 microusd per share. Both strategies run in every scenario.

The test partition is evaluated only after validation has selected the candidate and signal weight.
All scenario results, including zero-fill, negative and underperforming results, remain in the final
report.

## Integrity and publication rules

The two initially missing source hashes were filled into the frozen replay configs immediately
after acquisition and before inspection or replay. Recording an observed source identity did not
change a scientific choice. Each source matched the official directory size and must pass full
strict inspection and be bound into its replay manifest. Every completed replay receives deep
validation with source-hash verification; downstream manifests and child hashes must authenticate
before use.

Raw `.itch`/`.itch.gz` inputs, binary replay children, Parquet datasets, predictions and bulk
simulation outputs remain under ignored local roots and are never committed. Because permission to
republish source excerpts has not been confirmed, the public evidence contains no raw or
transformed row excerpts. It is limited to public-safe configs, hashes, manifests stripped of
machine-specific paths, aggregate report artefacts, deterministic reproduction instructions and
the existing synthetic fixtures. This resolves OQ-002 conservatively.

The final study evidence will reference the pinned performance result in
`docs/performance/TASK-029-performance.md`; TASK-031 does not rerun optimisation or change its
release throughput floor.

## Execution gates

Run from the repository root with a clean release build. Exact generated run IDs and authenticated
manifest paths are recorded in the final evidence index.

```console
./build/release/itchlab inspect --input data/raw/07302019.NASDAQ_ITCH50.gz --all --symbols AAPL,MSFT,AMZN --mode strict --format json
./build/release/itchlab inspect --input data/raw/10302019.NASDAQ_ITCH50.gz --all --symbols AAPL,MSFT,AMZN --mode strict --format json
./build/release/itchlab inspect --input data/raw/12302019.NASDAQ_ITCH50.gz --all --symbols AAPL,MSFT,AMZN --mode strict --format json
./build/release/itchlab replay --config configs/studies/task031/replay-2019-07-30.json --output-root runs --format json --quiet
./build/release/itchlab replay --config configs/studies/task031/replay-2019-10-30.json --output-root runs --format json --quiet
./build/release/itchlab replay --config configs/studies/task031/replay-2019-12-30.json --output-root runs --format json --quiet
```

Conversion, dataset, experiment and simulation configs will be added with authenticated parent
locators once their immutable IDs exist. Their parameter values must match this protocol exactly.
