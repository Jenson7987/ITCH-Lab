# ADR-004 — Conservative historical simulation and evaluation policy

## Status

Accepted for MVP.

## Context

Historical market data does not reveal every hidden order, activity on all venues, market impact of a hypothetical order or the counterfactual response to its presence. A naive backtest that fills whenever a historical price touches a quote would produce misleading results. Time-series model selection can also leak future information.

## Decision

- Treat the simulator as an assumption-bound historical experiment, not an exchange emulator or profitability proof.
- Simulated passive orders become active only after configured submission latency.
- At activation they join behind known visible orders at that price.
- Known ahead-order lifecycle events update the queue; unknown/hidden priority is never assumed favourable.
- Cancellation remains exposed until cancellation latency elapses.
- Fills require eligible observed execution flow and cannot exceed remaining order/event quantity.
- Apply explicit signed fees/rebates, inventory limits and terminal liquidation.
- Compare at least three latency and two cost settings.
- Use complete chronological day partitions. Fit preprocessing/models/calibration on training/validation only and evaluate the frozen choice on test.
- Report negative results, anomaly counts and all limitations prominently.

## Alternatives considered

- Touch fills: rejected as materially optimistic.
- Pro-rata queue depletion without order identity: simpler but discards available level-3 information.
- Full matching-engine counterfactual: historical feed cannot reveal how other participants would react.
- Random train/test split: violates temporal causality.
- Repeated test-set tuning: destroys the meaning of held-out evaluation.
- Live paper trading: adds time, operational dependencies and different evidence; deferred.

## Consequences

Positive:

- Results are harder to inflate accidentally.
- Simulator assumptions become interview/review material.
- Prediction and execution performance are separated.
- Level-3 reconstruction has a concrete research use.

Negative:

- Fill counts may be low.
- Results remain sensitive to unobservable liquidity and counterfactual behaviour.
- Exact queue tracking is implementation-heavy.
- The simulator may show no profitable result; that is acceptable.

## Conditions that justify revisiting

- A better validated fill model is supported by a primary research source and sensitivity tests.
- Live/paper execution data becomes available with explicit authorisation.
- Multi-venue data materially changes queue and execution interpretation.
- Any change receives a new ADR and causes new simulation-run identities rather than rewriting prior results.
