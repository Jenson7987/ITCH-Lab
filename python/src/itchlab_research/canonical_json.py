"""RFC 8785 canonical config bytes and SHA-256 integrity/identity hashes."""

from __future__ import annotations

import copy
import hashlib
from dataclasses import dataclass
from typing import Any, cast

import rfc8785

from itchlab_research.config import (
    Config,
    ConversionConfig,
    DatasetConfig,
    ExperimentConfig,
    ReplayConfig,
    SimulationConfig,
)


@dataclass(frozen=True, slots=True)
class ConfigHashes:
    """Lowercase full-config and locator-free identity-config SHA-256 values."""

    config_sha256: str
    identity_config_sha256: str


def config_document(config: Config) -> dict[str, Any]:
    """Return the complete effective JSON-compatible config document."""
    if isinstance(config, ReplayConfig):
        return {
            "schema_version": config.schema_version,
            "input": {
                "path": config.input.path,
                "sha256": config.input.sha256,
                "trading_date": config.input.trading_date,
                "exchange_timezone": config.input.exchange_timezone,
            },
            "selection": {
                "symbols": list(config.selection.symbols),
                "session_start_ns": config.selection.session_start_ns,
                "session_end_ns": config.selection.session_end_ns,
                "require_trading_state": config.selection.require_trading_state,
            },
            "output": {
                "depth": config.output.depth,
                "emit_unchanged_trade_snapshots": (config.output.emit_unchanged_trade_snapshots),
            },
            "validation": {
                "mode": config.validation.mode,
                "max_skipped_messages": config.validation.max_skipped_messages,
                "invariant_interval": config.validation.invariant_interval,
            },
        }
    if isinstance(config, ConversionConfig):
        return {
            "schema_version": config.schema_version,
            "replay_manifests": list(config.replay_manifests),
            "output_root": config.output_root,
            "parquet": {
                "compression": config.parquet.compression,
                "row_group_size": config.parquet.row_group_size,
                "partition_keys": list(config.parquet.partition_keys),
            },
            "allow_degraded": config.allow_degraded,
        }
    if isinstance(config, DatasetConfig):
        return {
            "schema_version": config.schema_version,
            "conversion_manifests": list(config.conversion_manifests),
            "symbols": list(config.symbols),
            "tick_size4_by_symbol": dict(config.tick_size4_by_symbol),
            "features": {
                "depth_levels": list(config.features.depth_levels),
                "event_windows": list(config.features.event_windows),
                "clock_windows_ns": list(config.features.clock_windows_ns),
            },
            "labels": {
                "primary_event_horizon": config.labels.primary_event_horizon,
                "secondary_event_horizons": list(config.labels.secondary_event_horizons),
                "flat_threshold_ticks": config.labels.flat_threshold_ticks,
            },
            "sampling": {"row_stride": config.sampling.row_stride},
            "partitions": {
                "train_dates": list(config.partitions.train_dates),
                "validation_dates": list(config.partitions.validation_dates),
                "test_dates": list(config.partitions.test_dates),
            },
        }
    if isinstance(config, ExperimentConfig):
        return {
            "schema_version": config.schema_version,
            "dataset_manifest": config.dataset_manifest,
            "models": {
                "prior": {"enabled": config.models.prior.enabled},
                "logistic_regression": {
                    "c_values": list(config.models.logistic_regression.c_values),
                    "penalty": config.models.logistic_regression.penalty,
                    "solver": config.models.logistic_regression.solver,
                    "max_iter": config.models.logistic_regression.max_iter,
                },
                "hist_gradient_boosting": {
                    "learning_rates": list(config.models.hist_gradient_boosting.learning_rates),
                    "max_leaf_nodes": list(config.models.hist_gradient_boosting.max_leaf_nodes),
                    "l2_regularization": list(
                        config.models.hist_gradient_boosting.l2_regularization
                    ),
                    "max_iter": config.models.hist_gradient_boosting.max_iter,
                },
            },
            "preprocessing": {
                "continuous_imputation": config.preprocessing.continuous_imputation,
                "standardise_logistic": config.preprocessing.standardise_logistic,
                "standardise_hist_gradient_boosting": (
                    config.preprocessing.standardise_hist_gradient_boosting
                ),
                "unknown_symbol": config.preprocessing.unknown_symbol,
            },
            "selection_metric": config.selection_metric,
            "seed": config.seed,
        }
    if isinstance(config, SimulationConfig):
        return {
            "schema_version": config.schema_version,
            "dataset_manifest": config.dataset_manifest,
            "prediction_manifest": config.prediction_manifest,
            "strategy": {
                "name": config.strategy.name,
                "decision_interval_ns": config.strategy.decision_interval_ns,
                "max_prediction_age_ns": config.strategy.max_prediction_age_ns,
                "order_quantity": config.strategy.order_quantity,
                "inventory_limit": config.strategy.inventory_limit,
                "gamma": config.strategy.gamma,
                "volatility_window_ns": config.strategy.volatility_window_ns,
                "risk_horizon_seconds": config.strategy.risk_horizon_seconds,
                "signal_weight_ticks": config.strategy.signal_weight_ticks,
                "max_signal_ticks": config.strategy.max_signal_ticks,
            },
            "execution": {
                "passive_only": config.execution.passive_only,
                "submission_latency_ns": config.execution.submission_latency_ns,
                "cancellation_latency_ns": config.execution.cancellation_latency_ns,
                "maker_fee_microusd_per_share": (config.execution.maker_fee_microusd_per_share),
                "taker_fee_microusd_per_share": (config.execution.taker_fee_microusd_per_share),
                "queue_policy": config.execution.queue_policy,
                "max_queue_anomalies": config.execution.max_queue_anomalies,
                "terminal_liquidation": config.execution.terminal_liquidation,
            },
            "seed": config.seed,
        }
    raise TypeError(f"Unsupported config type: {type(config).__name__}")


def identity_config_document(config: Config) -> dict[str, Any]:
    """Return the semantic config projection used as a stage-identity input."""
    document = copy.deepcopy(config_document(config))
    if isinstance(config, ReplayConfig):
        input_config = cast(dict[str, Any], document["input"])
        del input_config["path"]
        del input_config["sha256"]
    elif isinstance(config, ConversionConfig):
        del document["replay_manifests"]
        del document["output_root"]
    elif isinstance(config, DatasetConfig):
        del document["conversion_manifests"]
    elif isinstance(config, ExperimentConfig):
        del document["dataset_manifest"]
    elif isinstance(config, SimulationConfig):
        del document["dataset_manifest"]
        del document["prediction_manifest"]
    return document


def canonical_json_bytes(value: Any) -> bytes:
    """Canonicalise an I-JSON-compatible value using RFC 8785."""
    return rfc8785.dumps(value)


def config_hashes(config: Config) -> ConfigHashes:
    """Hash the full and locator-free canonical effective config."""
    full = hashlib.sha256(canonical_json_bytes(config_document(config))).hexdigest()
    identity = hashlib.sha256(canonical_json_bytes(identity_config_document(config))).hexdigest()
    return ConfigHashes(config_sha256=full, identity_config_sha256=identity)


__all__ = [
    "ConfigHashes",
    "canonical_json_bytes",
    "config_document",
    "config_hashes",
    "identity_config_document",
]
