# 00 — Project overview

## Executive summary

ITCH-Lab will provide a reproducible local pipeline for studying event-level equity-market microstructure. A C++20 core will safely decode Nasdaq TotalView-ITCH 5.0 data and reconstruct full visible order state for selected instruments. A Python research package will transform normalised events and book snapshots into leakage-controlled datasets, train transparent baselines, and run a queue-aware historical execution simulation. The output is an evidence-led technical report, not a claim that a strategy would be profitable in live trading.

## Problem being solved

Most personal trading projects use daily candles, assume fills at observed prices, randomly split time-series data, and report backtests that cannot survive transaction costs. They demonstrate little about real market infrastructure.

ITCH-Lab instead addresses three linked engineering questions:

1. Can a large, untrusted binary exchange feed be decoded and replayed correctly and efficiently?
2. Does order-flow imbalance contain out-of-sample information about short-horizon mid-price direction?
3. If predictive information exists, does it survive conservative assumptions about queue position, latency, fees and inventory risk?

## Target users

The roles below are local personas, not authenticated application accounts.

| Persona | Need |
| --- | --- |
| Systems developer | Implement, test, profile and optimise the decoder and order book |
| Quantitative researcher | Create leakage-controlled datasets, models and simulations |
| Technical reviewer | Reproduce a run and inspect assumptions, evidence and limitations |

**Assumption:** one person may perform all three roles during the MVP.

## Value proposition

- Uses a documented, real exchange-feed format rather than simplified price candles.
- Connects low-level systems work to statistics and quantitative research.
- Makes correctness, causality, simulation assumptions and negative results visible.
- Produces repeatable artefacts that an interviewer or reviewer can inspect.

## Product goals

| ID | Goal | Classification |
| --- | --- | --- |
| G-001 | Correctly decode the MVP subset of ITCH 5.0 messages from full-day files | Confirmed requirement |
| G-002 | Reconstruct selected instruments' visible level-3 order books deterministically | Confirmed requirement |
| G-003 | Measure throughput, memory use and important correctness invariants | Confirmed requirement |
| G-004 | Test order-flow signals with chronological validation and explicit baselines | Confirmed requirement |
| G-005 | Evaluate inventory-aware quoting with queue, latency and cost sensitivity | Confirmed requirement |
| G-006 | Make every published result reproducible from a config, source hashes and code revision | Recommendation |
| G-007 | Remain implementable by one developer in approximately eight focused weeks | Assumption |

## Non-goals

- Live connectivity to Nasdaq or any broker.
- Sending, recommending or automatically executing real orders.
- Reproducing Nasdaq's matching engine or hidden-liquidity behaviour.
- Predicting long-term equity returns.
- Building a web dashboard, user-account system or hosted SaaS product.
- Claiming production-grade high-frequency trading latency.
- Supporting every ITCH venue or historical protocol revision in the MVP.
- Redistributing raw market data.

## Core terminology

| Term | Meaning in this project |
| --- | --- |
| ITCH | Nasdaq's outbound binary market-data protocol |
| Message index | Project-assigned, zero-based order of decoded messages in a source file |
| Source offset | Zero-based byte offset in the uncompressed framed stream |
| Level 3 | Individual visible orders, including their order identifiers and remaining quantities |
| Level 2 | Quantity aggregated by price level |
| Top N | The best N bid and ask levels, default 10 |
| Price4 | Unsigned integer price with four implied decimal places |
| Book mutation | An add, execution, cancellation, deletion or replacement affecting visible orders |
| Event time | Progress measured in qualifying market events rather than wall-clock duration |
| Queue ahead | Visible quantity/orders assumed ahead of a simulated passive order |
| Run | One immutable execution of a versioned configuration against identified input data |
| Strict mode | Stop on malformed or inconsistent input |
| Permissive mode | Record a bounded error and skip a safely skippable message |

## MVP boundary

The MVP includes:

- Offline reading of official length-framed, gzip-compressed or uncompressed ITCH 5.0 sample files.
- System event, stock directory, trading action, add, execute, execute-with-price, cancel, delete, replace, trade, cross-trade and broken-trade message support.
- Full visible order tracking for a configured list of symbols.
- Top-10 level snapshots and normalised lifecycle events.
- A documented versioned binary interchange format plus a conversion to Parquet.
- Regular-session research on at least three liquid symbols across at least three distinct trading days.
- Three-class future mid-price direction labels at one primary event-time horizon, with secondary horizons allowed.
- Naive, logistic-regression and histogram-gradient-boosting baselines.
- Chronological day-level train, validation and test partitions.
- A historical passive-order simulator with explicit latency, fees, partial fills, queue position and inventory.
- An Avellaneda–Stoikov-inspired baseline and one signal-adjusted variant.
- Machine-readable run manifests and a Markdown/HTML research report.

## Post-MVP possibilities

All items below are **deferred decisions**:

- Live SoupBinTCP or MoldUDP64 receivers.
- OUCH-compatible order-entry simulation.
- Additional venues or protocols.
- Hardware timestamping, kernel bypass, FPGA work or lock-free multi-threading.
- GPU models, DeepLOB-style architectures or online learning.
- Cross-asset features, options data or alternative data.
- Interactive visualisation or hosted result explorer.
- Cloud object storage and distributed replay.

## Success criteria

The MVP succeeds when:

1. A clean checkout can run the synthetic end-to-end fixture using documented commands.
2. The C++ decoder supports every message type listed in the MVP and rejects malformed lengths without out-of-bounds access.
3. Replaying the same input and config twice produces identical derived-data hashes.
4. Book invariants pass throughout the committed synthetic fixtures and selected official sample runs.
5. Research partitions are separated by complete trading days, with no future-derived feature entering training data.
6. The report compares all required baselines on untouched test data and distinguishes predictive metrics from simulated trading metrics.
7. Simulation results include sensitivity to at least three latency settings and two fee/cost settings.
8. The report lists limitations, including hidden liquidity, other venues, stale/cancel latency and historical-regime dependence.
9. CI runs formatting, static checks, unit tests, integration tests and sanitizer tests.
10. The repository contains measured performance results and a profile-guided explanation of at least one optimisation.

## Assumptions

- The primary development machine is an Apple M2 Pro MacBook, with Linux CI.
- The developer can obtain the selected data directly from Nasdaq and has enough storage.
- Official sample files use the verified `itch-length-v1` contract in ADR-005: positive two-byte
  big-endian payload lengths and clean EOF only after a complete payload.
- Research uses public sample data and does not require confidential or personal information.
- Three or more full trading days are sufficient to demonstrate method, but not to support a deployable trading claim.
- The project owner prefers a technically rigorous portfolio project over a polished consumer interface.

## Constraints

- Raw data can exceed several gigabytes per day and must be streamed.
- The parser handles untrusted binary input and is written in a memory-unsafe language.
- Nasdaq timestamps are nanoseconds since local midnight and do not by themselves contain the trading date or timezone.
- ITCH describes Nasdaq-visible activity only; it does not reveal hidden orders or all activity on other venues.
- Historical replay cannot establish the market impact of hypothetical orders.
- The implementation must remain understandable to one developer and avoid premature distributed architecture.

## Open questions

| ID | Question | Current treatment |
| --- | --- | --- |
| OQ-001 | Which exact sample dates and symbols will be used in the published report? | Freeze at the start of TASK-031 after storage/data-quality preflight and before inspecting test results |
| OQ-002 | Does the official source's current licence permit publishing small transformed excerpts? | Publish synthetic fixtures only until confirmed |
| OQ-003 | What measured throughput target is realistic on the development machine? | Establish baseline in TASK-029; recommended release target is at least 1 million uncompressed messages/second |

## Primary references

- [Nasdaq TotalView-ITCH 5.0 specification](https://www.nasdaqtrader.com/content/technicalsupport/specifications/dataproducts/NQTVITCHSpecification.pdf)
- [Nasdaq ITCH sample-data directory](https://emi.nasdaq.com/ITCH/Nasdaq%20ITCH/)
- [Avellaneda and Stoikov, High-frequency trading in a limit order book](https://people.orie.cornell.edu/sfs33/LimitOrderBook.pdf)
