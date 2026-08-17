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
- A changed or suppressed desired quote requests cancellation. The symbol/side slot is not reused
  at the cancellation-effective instant; replacement submission waits for the first later strategy
  decision after the slot is free. This avoids assuming an unobserved instantaneous refresh.
- Fills require eligible observed execution flow and cannot exceed remaining order/event quantity.
- Apply explicit signed fees/rebates, per-symbol inventory limits and terminal liquidation. Quote
  eligibility uses projected inventory after a complete fill, and fill accounting enforces the
  same inclusive limit before mutation.
- Keep cash, fee, inventory value and P&L components in checked signed 64-bit microusd. Mark open
  inventory at the latest causal visible two-sided midpoint using exact `mid2` arithmetic; do not
  use floating-point money.
- At session end expire every open simulated order, sell long inventory at the last valid visible
  bid and buy short inventory at the last valid visible ask. Charge the configured signed taker
  fee, report liquidation slippage separately and fail a non-flat scenario when the required
  visible quote is absent or crossed.
- Compare the Cartesian grid of symmetric 0, 100,000 and 1,000,000 nanosecond
  submission/cancellation latency with −2,000 and +3,000 microusd/share maker cost for both the
  inventory-only and selected signal strategies; terminal liquidation uses 3,000 microusd/share
  taker cost. Also run a configured execution scenario when it is economically distinct from that
  grid.
- Use complete chronological day partitions. Fit preprocessing/models/calibration on training/validation only and evaluate the frozen choice on test.
- Select the model family by validation log loss, then select signal weight from 0, 0.5, 1 and 2
  ticks using validation-day P&L under the fixed 100,000 nanosecond/−2,000 maker and +3,000
  terminal-taker microusd/share scenario. A configured numeric weight is an assertion and must
  equal that independently selected value; null requests automatic selection. Test events remain
  unopened until both choices are frozen.
- Define turnover as absolute gross notional across passive fills and terminal liquidations. Define
  maximum drawdown over chronologically concatenated marked equity. Define the 100 ms
  adverse-selection proxy as `side × (fill_mid2 − future_mid2) × quantity × 50`, using the first
  valid midpoint at or after the horizon and reporting unavailable marks plus coverage explicitly.
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
