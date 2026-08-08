# ITCH-Lab research package

This is the Python foundation for the offline ITCH-Lab research platform. It provides version/help
entry points, strict configuration, stable-error and canonical-hashing contracts, plus authenticated
chunked readers for the event-v1 and snapshot-v1 C++ interchange files. The implemented `convert`
command validates completed replay lineage, writes typed Zstandard Parquet in bounded batches and
atomically publishes an immutable conversion manifest. The package also exposes a partition-scoped
causal feature service, deterministic version-1 feature catalogue and frozen label/dataset
publication. The `train` command authenticates that dataset, runs the required NumPy/scikit-learn
baselines with training-only preprocessing, and atomically publishes predictions, validation/test
metrics and safe diagnostics. The `report` command authenticates a completed predictive experiment
and all upstream manifests, then publishes deterministic Markdown and/or HTML with static SVG
calibration plots, adjacent text
summaries, canonical config snapshots and relative reproduction commands. Simulation commands are
implemented by later tasks.

From the repository root, point `configs/conversion.example.json` at completed replay manifests,
then run:

    python -m itchlab_research convert --config configs/conversion.example.json
    python -m itchlab_research build-dataset --config configs/dataset.example.json
    python -m itchlab_research train --config configs/experiment.example.json
    python -m itchlab_research report --run-id <experiment-id> --output-format both

All config paths are safe paths relative to the working directory. Degraded replay parents require
an explicit `allow_degraded` setting or `--allow-degraded`; `--format json` keeps stdout
machine-readable and `--quiet` suppresses progress on stderr.
Matching completed dataset and experiment identities are revalidated and reused; use
`--force-new-run` to retain another immutable run. Predictive reproduction retrains from recorded
lineage and never loads a pickle/joblib model object.
Report bundles are written beneath `runs/report/<experiment-id>/<markdown|html|both>/`. Existing
byte-identical bundles are reused, while inconsistent completed or partial bundles fail without
overwrite. Reports are static, contain no scripts and do not download data.
