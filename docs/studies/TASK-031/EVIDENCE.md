# Official-data study evidence

Traceability: TASK-031.

Status: completed on 2026-08-22 under the frozen [protocol](PROTOCOL.md).

The generated [Markdown report](report/report.md) and [HTML report](report/report.html) are the
public-safe final study outputs. They contain aggregate results, canonical configuration snapshots,
calibration data and accessible SVG plots. They contain no official source bytes, transformed row
excerpts, secrets or machine-specific absolute paths.

## Frozen scope

- Symbols: `AAPL`, `MSFT` and `AMZN`.
- Chronological whole-day partitions: train 2019-07-30, validation 2019-10-30 and test 2019-12-30.
- Regular session: half-open 09:30–16:00 `America/New_York`; visible depth 10.
- Official source identities are recorded below. Source files are not part of the repository and
  must be obtained independently under appropriate authorisation.

| Partition | Source basename | Bytes | SHA-256 |
| --- | --- | ---: | --- |
| Train | `07302019.NASDAQ_ITCH50.gz` | 3,662,140,094 | `c65784c48c28735901ae442dc00e215834218a359bc12a139ab4eec209bc2d4a` |
| Validation | `10302019.NASDAQ_ITCH50.gz` | 3,872,931,242 | `0ad86b61a0eb7f1bce2cffca0e08c8658026451c68657ea6b06f61ff3710b999` |
| Test | `12302019.NASDAQ_ITCH50.gz` | 3,524,013,057 | `ef03df46a27e6bda4dead017f84c2e3979df7211f02c7868b51d53fceb99c689` |

Strict full-source inspection examined 844,963,543 wire messages across the three days with zero
parse errors. All requested symbols were present.

## Authenticated lineage

| Stage | Run ID | Manifest SHA-256 | Principal rows |
| --- | --- | --- | ---: |
| Train replay | `20260821T165837.436440000Z-7bb0da7c2f45` | `72dc74772eecaadcd9c57b4af239ee8139e94afbbc35bc312cae957c2973d94a` | 2,824,363 events; 2,232,020 snapshots |
| Validation replay | `20260821T165837.436440000Z-cb17d1ecb6ac` | `e013349ee58ae8ed5d1f53426a267ab816911a9fd607d847183908d02000d4bc` | 2,327,831 events; 1,567,492 snapshots |
| Test replay | `20260821T165837.436841000Z-972055e967e0` | `fdf06dc29b5d4045d5f45d6467d4d0ac80dfd70a396d8382c6f036ef82baf1b1` | 3,187,062 events; 2,316,476 snapshots |
| Conversion | `20260821T180335.592249000Z-8e6d43b3e013` | `150e25a4cc10121ef82646776e28206b8e8db6f2a0a04a3e3a2c90620f8cf046` | 8,339,256 events; 6,115,988 snapshots |
| Dataset | `20260821T180641.067291000Z-bcdb1ae22a29` | `c5d69d774fdfd094540c9393f87106e0675cb5e0e0e7cfd9016950c65bdef113` | 604,054 retained feature/label rows |
| Experiment | `20260821T181625.772904000Z-01c0e689bac1` | `c65bf29bde4b7ce17a3dd819bbdc8daf2ff140130cd4e90385ca12d94c174fb1` | 1,149,636 prediction rows |
| Simulation | `20260822T000247.776720000Z-55a2f298d636` | `119a9d7a0983fa9a755d84b238d6f76101de361543ba5fd3eefff1ecc499ea8e` | 12 cells; 1,366,072 orders; 11,000 fills |

Deep replay validation streamed 14,455,244 event/snapshot records in total. Every child size, hash,
schema, ordering rule and reconstructed final book digest passed. The simulation loader separately
authenticated both parents and all six child artefacts, including 38,244,756 equity rows.

## Predictive and simulation outcome

Histogram gradient boosting had the lowest validation multiclass log loss, `0.9564774963`, and was
selected before the test partition was opened. Its test log loss was `0.9310465562`, compared with
`0.9593591270` for logistic regression and `1.0472804124` for the training-frequency prior. The
complete class, per-symbol, confusion-matrix and calibration evidence is in the final report.

Validation-only economic selection chose a signal weight of 2.0 ticks. Eleven of twelve held-out
strategy/scenario cells had negative marked P&L. The only positive result was the signal-adjusted,
zero-latency, −2,000 microusd/share maker-rebate cell at 61,236,000 microusd. The signal-adjusted
strategy exceeded the paired inventory-aware control in all six cells, but all twelve results had
positive, unfavourable 100 ms adverse selection. These observations are conditional historical
results, not a profitability claim or evidence of live executability.

Exact diagnostic aggregates were:

| Code | Count | Detailed rows retained |
| --- | ---: | ---: |
| `DIAG_COUNTERFACTUAL_CROSS` | 16,474 | 16,474 |
| `DIAG_MISSING_PREDICTION` | 1,188 | 0 |
| `DIAG_QUEUE_EVENT_SKIPPED` | 140 | 140 |
| `DIAG_STALE_PREDICTION` | 417,678 | 0 |

Routine prediction-fallback rows are count-only under the documented bounded record policy. Queue
and counterfactual diagnostics retain detailed records. Aggregate counts and retained-record counts
were independently reconciled during authenticated loading.

## Reproduction spot-check

The test-day replay was forced into a second immutable run,
`20260822T090709.234370000Z-972055e967e0`, then deep-validated against the exact official source.
The validator examined 5,503,538 event/snapshot records and reproduced the original identity,
counts, instruments, final book digests and byte-identical children:

| Child | Records | SHA-256 |
| --- | ---: | --- |
| Events | 3,187,062 | `6ac6f347ca2181fa88073042fdf13e6a9bf988d97f8e130dc85eeb2e2f645546` |
| Snapshots | 2,316,476 | `e1d1118ed3899e189cc09f04e0c0c4502e9138628510c0c76e93b643a9233ca1` |

## Reproduction commands

Place separately authorised source files beneath `data/raw/` with the basenames and hashes above,
then run from the repository root:

```console
./build/release/itchlab inspect --input data/raw/07302019.NASDAQ_ITCH50.gz --all --symbols AAPL,MSFT,AMZN --mode strict --format json
./build/release/itchlab inspect --input data/raw/10302019.NASDAQ_ITCH50.gz --all --symbols AAPL,MSFT,AMZN --mode strict --format json
./build/release/itchlab inspect --input data/raw/12302019.NASDAQ_ITCH50.gz --all --symbols AAPL,MSFT,AMZN --mode strict --format json
./build/release/itchlab replay --config configs/studies/task031/replay-2019-07-30.json --output-root runs
./build/release/itchlab replay --config configs/studies/task031/replay-2019-10-30.json --output-root runs
./build/release/itchlab replay --config configs/studies/task031/replay-2019-12-30.json --output-root runs
./build/release/itchlab validate --run runs/replay/<replay-id> --verify-source data/raw/<source> --deep
.venv/bin/python -m itchlab_research convert --config configs/studies/task031/conversion.json
.venv/bin/python -m itchlab_research build-dataset --config configs/studies/task031/dataset.json
.venv/bin/python -m itchlab_research train --config configs/studies/task031/experiment.json
.venv/bin/python -m itchlab_research simulate --config configs/studies/task031/simulation.json
.venv/bin/python -m itchlab_research report --run-id <simulation-id> --output-format both
```

Immutable IDs include tool/package identity. A rebuild from a later revision can therefore produce
a different run ID even when the scientific config and persisted records are unchanged; compare
authenticated parent identities and child hashes rather than substituting unverified files.

## Completion gates

All checks were run from the repository root after the final implementation and documentation
changes, except that the whitespace-only C++ formatter correction was followed by an exact rebuild
and focused regression under all three already-passing build modes.

| Gate | Result |
| --- | --- |
| Development configure/build/CTest | Pass: 136 passed, one authorised official-data opt-in skipped, zero failed |
| Release configure/build/CTest | Pass: 136 passed, one authorised official-data opt-in skipped, zero failed |
| ASan/UBSan configure/build/CTest | Pass: 136 passed, one authorised official-data opt-in skipped, zero failed |
| Post-format dev/Release/sanitizer replay regression | Pass: 1/1 in each build mode |
| Framing and decoder fuzz targets | Pass: 2/2 with 10,000 mutations configured per target |
| Python tests | Pass: 684 |
| Ruff formatting and lint, including scripts | Pass |
| mypy | Pass: 51 source files |
| Python sdist and wheel production build | Pass |
| C++ `clang-format --dry-run --Werror` | Pass |
| C++ clang-analyzer | Pass: 29 project translation units |
| Secret scan | Pass: zero secrets; 24 reviewed false-positive hash/literal locations |
| Hashed release-lock dependency audit | Pass: no known vulnerabilities |
| Network-disabled synthetic security smoke | Pass: 3 CTests and 2 Python tests |
| Isolated installed release smoke | Pass: complete synthetic inspect-to-report vertical slice and 10 doctor checks |
| Documentation lint | Pass: all 30 Markdown files present in the study revision |

The pinned [performance result](../../performance/TASK-029-performance.md) remains the applicable
release evidence; this scientific execution did not change its fixture or throughput floor.

## Limitations

The study has only one day per chronological partition, so confidence intervals that require at
least five trading days are correctly omitted. Results cover three selected symbols and the
visible Nasdaq book only. They exclude hidden/off-venue liquidity, market impact, price improvement
and live counterfactual participant behaviour. Raw data and bulk run outputs remain ignored local
artefacts.
