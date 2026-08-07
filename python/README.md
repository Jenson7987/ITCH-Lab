# ITCH-Lab research package

This is the Python foundation for the offline ITCH-Lab research platform. It provides version/help
entry points, strict configuration, stable-error and canonical-hashing contracts, plus authenticated
chunked readers for the event-v1 and snapshot-v1 C++ interchange files. The implemented `convert`
command validates completed replay lineage, writes typed Zstandard Parquet in bounded batches and
atomically publishes an immutable conversion manifest. The package also exposes a partition-scoped
causal feature service and deterministic version-1 feature catalogue. Dataset publication, labels,
model and simulation commands are implemented by later tasks.

From the repository root, point `configs/conversion.example.json` at completed replay manifests,
then run:

    python -m itchlab_research convert --config configs/conversion.example.json

All config paths are safe paths relative to the working directory. Degraded replay parents require
an explicit `allow_degraded` setting or `--allow-degraded`; `--format json` keeps stdout
machine-readable and `--quiet` suppresses progress on stderr.
