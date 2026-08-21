"""TASK-002 strict config-schema and semantic-validation tests."""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import cast

import pytest
from jsonschema import Draft202012Validator

from itchlab_research.config import (
    ConfigKind,
    ConversionConfig,
    ReplayConfig,
    SimulationConfig,
    load_config,
    parse_config,
)
from itchlab_research.errors import ConfigValidationError, ErrorCode

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
VALID_ROOT = REPOSITORY_ROOT / "tests" / "golden" / "configs" / "valid"
INVALID_ROOT = REPOSITORY_ROOT / "tests" / "golden" / "configs" / "invalid"


@pytest.mark.parametrize("kind", ["replay", "conversion", "dataset", "experiment", "simulation"])
def test_task_002_example_and_valid_golden_configs_match(kind: ConfigKind) -> None:
    example = REPOSITORY_ROOT / "configs" / f"{kind}.example.json"
    golden = VALID_ROOT / f"{kind}.json"

    assert example.read_bytes() == golden.read_bytes()
    assert load_config(example, kind) == load_config(golden, kind)


@pytest.mark.parametrize(
    ("kind", "filename", "expected_code"),
    [
        ("replay", "replay-unknown-key.json", ErrorCode.CONFIG_SCHEMA),
        ("dataset", "dataset-overlap.json", ErrorCode.PARTITION),
        ("experiment", "experiment-unsafe-seed.json", ErrorCode.SEED),
        ("simulation", "simulation-inventory.json", ErrorCode.INVENTORY_LIMIT),
    ],
)
def test_task_002_invalid_goldens_fail_with_stable_codes(
    kind: ConfigKind, filename: str, expected_code: ErrorCode
) -> None:
    with pytest.raises(ConfigValidationError) as captured:
        load_config(INVALID_ROOT / filename, kind)

    assert expected_code in {issue.code for issue in captured.value.issues}
    assert list(captured.value.issues) == sorted(captured.value.issues)


def test_ut_cfg_001_unknown_nested_key_fails() -> None:
    document = json.loads((VALID_ROOT / "replay.json").read_text(encoding="utf-8"))
    document["output"]["surprise"] = True

    with pytest.raises(ConfigValidationError) as captured:
        parse_config(json.dumps(document), "replay")

    assert captured.value.issues[0].code is ErrorCode.CONFIG_SCHEMA
    assert captured.value.issues[0].json_pointer == "/output"


@pytest.mark.parametrize(
    ("pointer", "value", "expected_code"),
    [
        ("/input/exchange_timezone", 42, ErrorCode.TIMEZONE),
        ("/input/trading_date", "2024/01/02", ErrorCode.TRADING_DATE),
        ("/selection/session_start_ns", -1, ErrorCode.SESSION_WINDOW),
        ("/output/depth", 0, ErrorCode.DEPTH),
    ],
)
def test_task_030_schema_boundaries_keep_stable_error_codes(
    pointer: str,
    value: object,
    expected_code: ErrorCode,
) -> None:
    document = json.loads((VALID_ROOT / "replay.json").read_text(encoding="utf-8"))
    parent_name, field_name = pointer.strip("/").split("/")
    document[parent_name][field_name] = value

    with pytest.raises(ConfigValidationError) as captured:
        parse_config(json.dumps(document), "replay")

    assert captured.value.issues[0].code is expected_code
    assert captured.value.issues[0].json_pointer == pointer


@pytest.mark.parametrize("kind", ["replay", "conversion", "dataset", "experiment", "simulation"])
def test_ut_cfg_001_every_config_rejects_unknown_root_keys(kind: ConfigKind) -> None:
    document = json.loads((VALID_ROOT / f"{kind}.json").read_text(encoding="utf-8"))
    document["unexpected"] = True

    with pytest.raises(ConfigValidationError) as captured:
        parse_config(json.dumps(document), kind)

    assert captured.value.issues[0].code is ErrorCode.CONFIG_SCHEMA
    assert captured.value.issues[0].json_pointer == ""


@pytest.mark.parametrize(
    ("field", "value", "expected_code"),
    [
        ("replay_manifests", ["../replay-manifest.json"], ErrorCode.INPUT_PATH),
        ("replay_manifests", ["C:\\replay-manifest.json"], ErrorCode.INPUT_PATH),
        ("output_root", ".", ErrorCode.OUTPUT_PATH),
        ("output_root", "runs/work.partial/output", ErrorCode.OUTPUT_PATH),
    ],
)
def test_task_017_conversion_config_rejects_unsafe_paths(
    field: str,
    value: object,
    expected_code: ErrorCode,
) -> None:
    document = json.loads((VALID_ROOT / "conversion.json").read_text(encoding="utf-8"))
    document[field] = value

    with pytest.raises(ConfigValidationError) as captured:
        parse_config(json.dumps(document), "conversion")

    assert expected_code in {issue.code for issue in captured.value.issues}


def test_task_017_conversion_config_materialises_degraded_default() -> None:
    document = json.loads((VALID_ROOT / "conversion.json").read_text(encoding="utf-8"))
    del document["allow_degraded"]

    config = cast(ConversionConfig, parse_config(json.dumps(document), "conversion"))

    assert config.allow_degraded is False


def test_ut_cfg_001_overlapping_dates_fail() -> None:
    with pytest.raises(ConfigValidationError) as captured:
        load_config(INVALID_ROOT / "dataset-overlap.json", "dataset")

    assert any(issue.code is ErrorCode.PARTITION for issue in captured.value.issues)


@pytest.mark.parametrize(
    "document",
    [
        '{"schema_version": 1, "schema_version": 1}',
        '{"seed": NaN}',
        '{"seed": Infinity}',
        '"not-an-object"',
    ],
)
def test_task_002_non_ijson_input_fails_before_schema_use(document: str) -> None:
    with pytest.raises(ConfigValidationError) as captured:
        parse_config(document, "experiment")

    assert captured.value.issues == (captured.value.issues[0],)
    assert captured.value.issues[0].code is ErrorCode.CONFIG_SCHEMA


@pytest.mark.parametrize("kind", ["experiment", "simulation"])
def test_task_002_safe_integer_seed_boundary(kind: ConfigKind) -> None:
    document = json.loads((VALID_ROOT / f"{kind}.json").read_text(encoding="utf-8"))
    document["seed"] = 9_007_199_254_740_991

    assert parse_config(json.dumps(document), kind).seed == 9_007_199_254_740_991

    document["seed"] += 1
    with pytest.raises(ConfigValidationError) as captured:
        parse_config(json.dumps(document), kind)

    assert ErrorCode.SEED in {issue.code for issue in captured.value.issues}


def test_task_002_config_models_are_immutable() -> None:
    config = cast(ReplayConfig, load_config(VALID_ROOT / "replay.json", "replay"))

    with pytest.raises(FrozenInstanceError):
        config.schema_version = 2  # type: ignore[misc]


def test_task_002_schema_documents_are_valid_draft_2020_12() -> None:
    for schema_path in sorted((REPOSITORY_ROOT / "schemas").glob("*-config.schema.json")):
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)


def test_task_002_root_and_packaged_schemas_are_identical() -> None:
    packaged_root = REPOSITORY_ROOT / "python" / "src" / "itchlab_research" / "_schemas"
    for root_schema in sorted((REPOSITORY_ROOT / "schemas").glob("*-config.schema.json")):
        assert root_schema.read_bytes() == (packaged_root / root_schema.name).read_bytes()


def test_task_002_tick_map_must_exactly_match_symbols() -> None:
    document = json.loads((VALID_ROOT / "dataset.json").read_text(encoding="utf-8"))
    del document["tick_size4_by_symbol"]["AMZN"]

    with pytest.raises(ConfigValidationError) as captured:
        parse_config(json.dumps(document), "dataset")

    assert captured.value.issues[0].json_pointer == "/tick_size4_by_symbol"


@pytest.mark.parametrize(
    "locator",
    [
        "../conversion-manifest.json",
        "/tmp/conversion-manifest.json",
        "runs/conversion.partial/conversion-manifest.json",
        "runs//conversion-manifest.json",
    ],
)
def test_task_019_dataset_conversion_locators_must_be_safe_relative_paths(
    locator: str,
) -> None:
    document = json.loads((VALID_ROOT / "dataset.json").read_text(encoding="utf-8"))
    document["conversion_manifests"][0] = locator

    with pytest.raises(ConfigValidationError) as captured:
        parse_config(json.dumps(document), "dataset")

    assert captured.value.issues[0].code is ErrorCode.INPUT_PATH
    assert captured.value.issues[0].json_pointer == "/conversion_manifests/0"


@pytest.mark.parametrize(
    "locator",
    [
        "../dataset-manifest.json",
        "/tmp/dataset-manifest.json",
        "runs/experiment.partial/dataset-manifest.json",
        "runs//dataset-manifest.json",
    ],
)
def test_task_020_experiment_dataset_locator_must_be_a_safe_relative_path(
    locator: str,
) -> None:
    document = json.loads((VALID_ROOT / "experiment.json").read_text(encoding="utf-8"))
    document["dataset_manifest"] = locator

    with pytest.raises(ConfigValidationError) as captured:
        parse_config(json.dumps(document), "experiment")

    assert captured.value.issues[0].code is ErrorCode.INPUT_PATH
    assert captured.value.issues[0].json_pointer == "/dataset_manifest"


def test_task_002_signal_strategy_requires_prediction_manifest() -> None:
    document = json.loads((VALID_ROOT / "simulation.json").read_text(encoding="utf-8"))
    document["prediction_manifest"] = None

    with pytest.raises(ConfigValidationError) as captured:
        parse_config(json.dumps(document), "simulation")

    assert captured.value.issues[0].code is ErrorCode.CONFIG_SCHEMA


@pytest.mark.parametrize("value", [-1, 9_007_199_254_740_992])
def test_task_023_queue_anomaly_budget_has_safe_integer_bounds(value: int) -> None:
    document = json.loads((VALID_ROOT / "simulation.json").read_text(encoding="utf-8"))
    document["execution"]["max_queue_anomalies"] = value

    with pytest.raises(ConfigValidationError) as captured:
        parse_config(json.dumps(document), "simulation")

    assert captured.value.issues[0].code is ErrorCode.QUEUE_STATE
    assert captured.value.issues[0].json_pointer == "/execution/max_queue_anomalies"


def test_task_023_queue_anomaly_budget_is_materialised_in_simulation_config() -> None:
    document = json.loads((VALID_ROOT / "simulation.json").read_text(encoding="utf-8"))
    document["execution"]["max_queue_anomalies"] = 7

    config = cast(SimulationConfig, parse_config(json.dumps(document), "simulation"))

    assert config.execution.max_queue_anomalies == 7
