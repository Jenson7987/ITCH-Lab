"""Causal research-dataset transformations and metadata."""

from itchlab_research.datasets.features import (
    build_feature_batches,
    feature_catalogue,
    feature_catalogue_document,
    feature_schema,
)
from itchlab_research.datasets.labels import (
    build_label_batches,
    label_column,
    label_horizons,
    label_schema,
)
from itchlab_research.datasets.models import (
    DatasetProgress,
    DatasetResult,
    FeatureDefinition,
    FeaturePartitionContext,
)
from itchlab_research.datasets.service import build_dataset
from itchlab_research.datasets.splits import (
    PartitionJoinCounts,
    dataset_schema,
    join_feature_label_batches,
    partition_mapping,
)
from itchlab_research.errors import (
    DatasetBuildError,
    FeatureComputationError,
    LabelComputationError,
)

__all__ = [
    "DatasetBuildError",
    "DatasetProgress",
    "DatasetResult",
    "FeatureComputationError",
    "FeatureDefinition",
    "FeaturePartitionContext",
    "LabelComputationError",
    "PartitionJoinCounts",
    "build_feature_batches",
    "build_dataset",
    "build_label_batches",
    "dataset_schema",
    "feature_catalogue",
    "feature_catalogue_document",
    "feature_schema",
    "join_feature_label_batches",
    "label_column",
    "label_horizons",
    "label_schema",
    "partition_mapping",
]
