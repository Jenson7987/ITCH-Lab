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
- Validation: strict, zero skipped messages, invariant check every 10,000 selected mutations and at
  finalisation.
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
- Passive-only execution, conservative known-order queue policy and a 3,000 queue-anomaly budget
  per scenario/day, as justified by the pre-simulation data-quality amendment below.
- Terminal inventory: cross the visible spread.
- Frozen test sensitivity grid: submission/cancellation latency of 0, 100 microseconds and
  1 millisecond, crossed with maker fee/rebate of −2,000 and 3,000 microusd per share. Taker cost is
  fixed at 3,000 microusd per share. Both strategies run in every scenario.

The test partition is evaluated only after validation has selected the candidate and signal weight.
All scenario results, including zero-fill, negative and underperforming results, remain in the final
report.

### Execution-feasibility amendment

The initial protocol used the example cadence of one full book-invariant scan after every selected
mutation. On 2026-08-21, three parallel unpublished replay attempts were cancelled cleanly after
about seven minutes when their last emitted event timestamps were only 09:30:39–09:32:30. The
observed cost projected to many hours per day because each scan traverses the complete live book.
No completed replay, label, model metric or simulation outcome existed or was inspected.

The cadence was therefore frozen at every 10,000 selected mutations before restarting. Individual
book operations retain their checked atomic domain validation; arbitrary decode/book errors remain
fatal; every book receives a mandatory full final invariant check; completed children receive
streamed deep validation. This operational cadence change affects no emitted record, feature,
label, model, selection rule or scenario assumption.

A second set of unpublished attempts at that cadence was cancelled cleanly after profiling showed
that production replay also computed the canonical full-book digest after every selected event.
Neither event-v1 nor snapshot-v1 serialises that intermediate diagnostic field. The production
sinks now explicitly opt out of it, while diagnostic sinks retain the existing per-event digest and
every instrument still receives the same mandatory canonical final digest recorded in the replay
manifest. Existing diagnostic comparisons and independent event/snapshot binary goldens cover this
compatibility boundary; no schema, scientific definition or persisted record changed.

The first dataset attempt then stopped before publication when a source match number occurred in
two adjacent E/C messages. Inspection of the authenticated Parquet rows showed equal timestamps and
quantities on opposite resting sides, consistent with the two visible legs of one execution. The
[Nasdaq ITCH FAQ](https://classic.nasdaqtrader.com/Content/TechnicalSupport/FAQs/ITCH_FAQ.pdf)
defines one match number per execution transaction. Feature state was corrected to retain every E/C
contribution in a match group and to remove the whole group on a later B; no row from the failed
partial dataset was inspected and no model or simulation outcome existed.

The first simulation attempt retained the initially frozen zero anomaly budget and stopped before
publication at MSFT message index 15,694,105 with `execution_behind_known_ahead`. A source-only FIFO
preflight, performed without model predictions, P&L or simulation output, found valid executions of
later visible references while older same-price references remained:

| Partition day | AAPL | MSFT | AMZN | Total |
| --- | ---: | ---: | ---: | ---: |
| 2019-07-30 train | 931 | 661 | 489 | 2,081 |
| 2019-10-30 validation | 685 | 792 | 275 | 1,752 |
| 2019-12-30 test | 759 | 1,589 | 402 | 2,750 |

These events expose queue-priority information that the visible feed does not explain; they are not
safe fill evidence for a hypothetical order. The existing conservative policy already skips their
simulated effect, records `DIAG_QUEUE_EVENT_SKIPPED`, and aborts above an explicit budget. Before
any simulation outcome existed, the study budget was therefore frozen at 3,000 per scenario/day:
the largest preflight total plus 250 events of headroom. A count above that fixed bound remains
fatal and every accepted anomaly remains visible in the report.

Profiling the failed attempt also showed two repeated full-book scans in activation and
counterfactual-cross checks. The queue model now uses an exact symbol/side/price index and lazy
best-price heaps for those same lookups. Unit regressions preserve source-order queue membership,
marketability and invalidation semantics; no strategy, fill rule or persisted schema changed.

A later unpublished full-grid attempt was stopped before test access after its validation-only
selection remained CPU-bound for 26 minutes. Inspection found that every quote decision rebuilt
and scanned the complete historical simulated-order tuple twice even though the order state machine
already owned an exact symbol/side occupancy index. The runner now uses that existing index for the
same non-terminal order lookup. Full-day scenario children are also written in bounded Parquet row
groups and released one scenario at a time instead of retaining the complete grid in memory. These
execution-lifecycle changes preserve row order, schemas, metrics and scenario order; focused
publication, ordering and slot-release regressions cover the boundary.

The next unpublished full-grid attempt completed all twelve test computations and child Parquet
files but stopped before manifest publication because routine missing/stale-prediction diagnostic
rows exceeded the fixed JSON size bound. Aggregate results in that partial run were inspected, so
it is not reused as final evidence. The publication representation now keeps exact counts for both
fallback codes without duplicating their expected per-decision rows; queue anomalies and every
other diagnostic retain detailed records and reconciled record counts. This resource-bound change
does not alter predictions, strategy decisions, execution, accounting, scenario metrics or any
frozen scientific choice. The final run uses the resulting new package-content identity and starts
from the authenticated parents again.

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
