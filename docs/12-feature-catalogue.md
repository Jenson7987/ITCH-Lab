# 12 — Feature catalogue

## Scope

This is the authoritative version-1 feature catalogue for TASK-018. Features are computed for every
qualifying snapshot before TASK-019 applies history filtering, labels, row stride or day partitions.
The runtime catalogue carries the same names, Arrow dtypes, formulae, lookbacks, units, null policies
and owner values in this order.

All features are owned by `itchlab_research.datasets.features`. A current-row feature may use the
qualifying snapshot and the exact normalised event at the same message index. A rolling feature may
use only earlier state plus events whose message index is at or before the row's decision index.

## Row metadata

| Name | Arrow dtype | Meaning |
| --- | --- | --- |
| trading_date | date32[day] | Source trading date |
| symbol | string | Source symbol, never the day-local ID used as a model category |
| symbol_id | uint16 | Day-local replay identity retained for lineage |
| message_index | uint64 | Immutable decision-row key |
| timestamp_ns | uint64 | Nanoseconds since exchange-local midnight |
| qualifying_ordinal | uint64 | Zero-based qualifying-row ordinal within the partition |
| history_complete | bool | Every required event and clock lookback is complete |

## Current-row features

| Name | Dtype | Nullable | Formula/unit | Null policy |
| --- | --- | --- | --- | --- |
| spread_ticks | float64 | No | `(ask_price4_1-bid_price4_1)/tick_size4`; ticks | Never null on a valid qualifying row |
| imbalance_1 | float64 | Yes | `(B(1)-A(1))/(B(1)+A(1))`; ratio | Null only for a zero denominator |
| imbalance_5 | float64 | Yes | `(B(5)-A(5))/(B(5)+A(5))`; ratio | Null only for a zero denominator |
| imbalance_10 | float64 | Yes | `(B(10)-A(10))/(B(10)+A(10))`; ratio | Null only for a zero denominator |
| microprice4 | float64 | No | `(ask_price4_1*B(1)+bid_price4_1*A(1))/(B(1)+A(1))`; Price4 | Never null on a valid qualifying row |
| microprice_displacement_ticks | float64 | No | `(microprice4-(bid_price4_1+ask_price4_1)/2)/tick_size4`; ticks | Never null on a valid qualifying row |
| aggressor_sign | int8 | Yes | Negative of the resting side for an exact E/C trigger; sign | Null when the row was not triggered by observable E/C flow |
| session_progress | float64 | No | Clipped elapsed-session fraction; fraction | Never null |
| session_progress_squared | float64 | No | `session_progress^2`; fraction squared | Never null |

## Qualifying-transition features

For each W in 20, 100 and 500, the feature at ordinal t uses W increments or returns ending at t.
It therefore requires t at least W and is null during earlier warm-up rows. `execution_imbalance_W`
uses eligible E/C executions in `(message_index_(t-W), message_index_t]`. A later B removes its
referenced eligible match from subsequent imbalance values while the original execution remains in
that interval; it never changes an earlier row or displayed execution rates.

| Name pattern | Dtype | Formula/unit | Post-warm-up null policy |
| --- | --- | --- | --- |
| `ofi_W` | float64 | Sum of the documented top-of-book order-flow increments; shares | Never null |
| `ofi_normalised_W` | float64 | `ofi_W / sum(B(1)+A(1))` over the same increment rows; ratio | Null only for a zero denominator |
| `realised_volatility_W` | float64 | Square root of summed squared log-mid returns; unannualised volatility | Never null for positive mids |
| `execution_imbalance_W` | float64 | Signed eligible E/C quantity divided by total eligible E/C quantity; ratio | Zero when no eligible execution remains |

The concrete columns are emitted in W order as:

1. `ofi_20`, `ofi_normalised_20`, `realised_volatility_20`, `execution_imbalance_20`.
2. `ofi_100`, `ofi_normalised_100`, `realised_volatility_100`, `execution_imbalance_100`.
3. `ofi_500`, `ofi_normalised_500`, `realised_volatility_500`, `execution_imbalance_500`.

## Clock-window event rates

Rates count normalised events in `(t-window, t]` and divide by the configured window in seconds.
At an equal timestamp, only events at or before the feature-row message index are eligible. Rate
columns are null until the full clock window lies within the research session. Resting side +1 is
named `bid`; resting side -1 is named `ask`.

| Window | Columns | Included event kinds |
| --- | --- | --- |
| 100 ms | `add_bid_rate_100ms`, `add_ask_rate_100ms` | add |
| 100 ms | `cancel_delete_bid_rate_100ms`, `cancel_delete_ask_rate_100ms` | cancel, delete |
| 100 ms | `execution_bid_rate_100ms`, `execution_ask_rate_100ms` | execute, execute_price |
| 1 s | `add_bid_rate_1s`, `add_ask_rate_1s` | add |
| 1 s | `cancel_delete_bid_rate_1s`, `cancel_delete_ask_rate_1s` | cancel, delete |
| 1 s | `execution_bid_rate_1s`, `execution_ask_rate_1s` | execute, execute_price |

All rate columns are nullable float64 values with unit events/second. Replace, P, Q and B records
are not reclassified into these version-1 categories. Pre-session events warm the visible book but
never enter research windows.

## Finite values and row eligibility

The feature engine rejects non-finite derived values. A qualifying row must be in the configured
half-open session, have `top_n_changed=true`, be in `trading` state and contain valid positive-quantity
best bid and ask values with a positive mid. Missing deeper slots contribute zero to depth sums.
Windows reset for every trading-date/symbol partition.
