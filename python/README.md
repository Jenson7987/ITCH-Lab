# ITCH-Lab research package

This is the Python research package for the offline ITCH-Lab platform. It provides version/help
entry points, strict configuration, stable-error and canonical-hashing contracts, plus authenticated
chunked readers for the event-v1 and snapshot-v1 C++ interchange files. The implemented `convert`
command validates completed replay lineage, writes typed Zstandard Parquet in bounded batches and
atomically publishes an immutable conversion manifest. The package also exposes a partition-scoped
causal feature service, deterministic version-1 feature catalogue and frozen label/dataset
publication. The `train` command authenticates that dataset, runs the required NumPy/scikit-learn
baselines with training-only preprocessing, and atomically publishes predictions, validation/test
metrics and safe diagnostics. The `simulate` command authenticates conversion Parquet and all
parent manifests, calibrates on training only, selects model family and signal weight on validation
only, then publishes the fixed latency/cost test grid for both strategies as immutable orders,
passive fills, liquidations, equity, metrics and diagnostics. The `report` command accepts a
completed predictive experiment or simulation and publishes deterministic Markdown and/or HTML
with static SVG calibration plots, adjacent text
summaries, canonical config snapshots and relative reproduction commands. The immutable
simulated-order lifecycle, deterministic integer-nanosecond latency scheduler and exact-known
visible queue/partial-fill model are implemented alongside checked signed-microusd accounting,
per-symbol inventory enforcement and explicit visible-spread terminal liquidation. Training-only
intensity calibration, the causal tick-rounded inventory-aware baseline and its bounded
validation-frozen signal adjustment are also available. Equal-time
source messages precede effective actions, preserving conservative fill-before-cancel races. Hidden
P and cross Q events never fill displayed simulated orders, and a broken E/C match used for a fill
aborts rather than inventing reinstatement.

The `doctor` command is intentionally lightweight and imports research dependencies only while
checking them. It validates the installed package, packaged schemas, matching native binary and
existing writable roots without reading market data or using the network:

    python -m itchlab_research doctor --binary /path/to/itchlab --format json

From the repository root, point `configs/conversion.example.json` at completed replay manifests,
then run:

    python -m itchlab_research convert --config configs/conversion.example.json
    python -m itchlab_research build-dataset --config configs/dataset.example.json
    python -m itchlab_research train --config configs/experiment.example.json
    python -m itchlab_research simulate --config configs/simulation.example.json
    python -m itchlab_research report --run-id <simulation-id> --output-format both

All config paths are safe paths relative to the working directory. Degraded replay parents require
an explicit `allow_degraded` setting or `--allow-degraded`; `--format json` keeps stdout
machine-readable and `--quiet` suppresses progress on stderr.
Matching completed dataset and experiment identities are revalidated and reused; use
`--force-new-run` to retain another immutable run. Predictive reproduction retrains from recorded
lineage and never loads a pickle/joblib model object.
Report bundles are written beneath `runs/report/<run-id>/<markdown|html|both>/`. Existing
byte-identical bundles are reused, while inconsistent completed or partial bundles fail without
overwrite. Reports are static, contain no scripts and do not download data.

The signal strategy selects its model family from validation log loss only, joins exact
day/symbol/model prediction keys causally, treats only missing/stale predictions as zero signal and
clips the configured reservation-price adjustment before reusing the baseline tick, passivity and
inventory constraints. Signal weight zero bypasses prediction consumption and reproduces the
baseline economic decision.
