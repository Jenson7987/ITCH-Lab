# ADR-004 — Conservative historical simulation and evaluation policy

## Status

Accepted for MVP.

## Context

Historical market data does not reveal every hidden order, activity on all venues, market impact of a hypothetical order or the counterfactual response to its presence. A naive backtest that fills whenever a historical price touches a quote would produce misleading results. Time-series model selection can also leak future information.

## Decision

- Treat the simulator as an assumption-bound historical experiment, not an exchange emulator or profitability proof.
- Simulated passive orders become active only after configured submission latency.
- At activation they join behind every known visible order at the same symbol, side and displayed
  price. The initial queue-ahead quantity is the checked sum of those exact references' remaining
  displayed shares.
- Queue position retains those exact ahead references. E/C executions and X/D/U removals reduce
  queue ahead only when their referenced order is known to be ahead. A U replacement removes the
  old reference from ahead and its new reference has new priority behind the simulated order when
  it is at the same price. Adds after activation are also behind and never increase queue ahead.
- Once exact queue ahead is zero, an E/C execution against a later visible reference at the same
  resting side and displayed price is eligible to fill the simulated order. C's separate execution
  price does not change queue eligibility or the simulated fill price. An execution against a
  behind reference while known ahead remains is diagnosed and cannot fill.
- Eligible quantity is used at most once for the one permitted simulated order on that symbol/side
  and is capped by both the observed E/C quantity and simulated remainder. Hidden P trades and Q
  crosses never fill or deplete a displayed simulated queue.
- If eligible displayed flow progresses to a worse same-side price, or the visible opposite book
  crosses the hypothetical limit, any exposed remainder is counterfactually invalidated rather
  than filled. This diagnostic does not consume the inconsistent-event anomaly budget.
- A B message for a match that caused any simulated fill aborts with ERR_BROKEN_SIM_FILL; other B
  messages are recorded without reinstating historical or simulated liquidity.
- Structurally valid but lifecycle-inconsistent queue events are skipped atomically for simulated
  effects, recorded with bounded identifiers and counted against the configured
  max_queue_anomalies budget. Exceeding the budget aborts with ERR_SIMULATION_ANOMALY. Hidden or
  unknown priority is never inferred from such an event.
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
