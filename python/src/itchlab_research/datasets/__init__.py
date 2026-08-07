"""Causal research-dataset transformations and metadata."""

from itchlab_research.datasets.features import (
    build_feature_batches,
    feature_catalogue,
    feature_catalogue_document,
    feature_schema,
)
from itchlab_research.datasets.models import FeatureDefinition, FeaturePartitionContext
from itchlab_research.errors import FeatureComputationError

__all__ = [
    "FeatureComputationError",
    "FeatureDefinition",
    "FeaturePartitionContext",
    "build_feature_batches",
    "feature_catalogue",
    "feature_catalogue_document",
    "feature_schema",
]
