# ITCH-Lab research package

`itchlab-research` is the Python layer of the offline ITCH-Lab platform. It consumes authenticated
event-v1 and snapshot-v1 artefacts produced by the C++ replay core and provides:

- bounded conversion to typed, partitioned Parquet;
- causal feature and future-label construction with chronological whole-day splits;
- prior, multinomial logistic-regression and histogram-gradient-boosting baselines;
- validation-frozen model and signal-weight selection;
- conservative queue-, latency-, cost- and inventory-aware historical simulation; and
- deterministic Markdown/HTML reports with static SVG plots and reproduction configs.

Every stage validates its completed parents, writes to a partial run directory and publishes an
immutable manifest last. Predictive reproduction retrains from recorded lineage; no command loads
an arbitrary pickle or joblib model object.

## Commands

From the repository root, after installing the development environment:

```sh
python -m itchlab_research --help
python -m itchlab_research convert --config configs/conversion.example.json
python -m itchlab_research build-dataset --config configs/dataset.example.json
python -m itchlab_research train --config configs/experiment.example.json
python -m itchlab_research simulate --config configs/simulation.example.json
python -m itchlab_research report --run-id <simulation-id> --output-format both
```

The example parent-manifest locators are placeholders and must be replaced with safe relative paths
to completed local runs. Degraded replay parents require an explicit `allow_degraded` setting or
`--allow-degraded`. Use `--format json` for machine-readable stdout, `--quiet` to suppress progress
on stderr and `--force-new-run` to retain another immutable run with the same content identity.

## Installation health

The `doctor` command checks Python 3.11+, required dependencies, packaged schemas, the matching
native binary and existing writable run/data roots. It does not read market data or use the
network:

```sh
python -m itchlab_research doctor --binary /path/to/itchlab --format json
```

## Simulation scope

The simulator tracks exact known visible queue ahead and permits fills only from eligible observed
E/C execution flow. Equal-time source messages precede effective simulated actions. Hidden P trades
and Q crosses never fill displayed simulated orders, and breaking a match that caused a fill aborts
the scenario rather than inventing liquidity reinstatement. Missing or stale predictions use a
recorded zero-signal fallback; malformed or mis-keyed predictions fail validation.

These are historical experiments, not live execution or profitability claims. See the repository
[README](../README.md) and [simulation policy](../docs/decisions/ADR-004-conservative-simulation-policy.md)
for the complete architecture, setup and limitations.
