"""TASK-017 conversion-manifest JSON contract tests."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

from itchlab_research.config import ConversionConfig, ConversionParquetConfig
from itchlab_research.conversion import convert_replays

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_ROOT = REPOSITORY_ROOT / "schemas"
PACKAGED_ROOT = REPOSITORY_ROOT / "python" / "src" / "itchlab_research" / "_schemas"


def _validator() -> Draft202012Validator:
    config = json.loads((SCHEMA_ROOT / "conversion-config.schema.json").read_text(encoding="utf-8"))
    manifest = json.loads(
        (SCHEMA_ROOT / "conversion-manifest.schema.json").read_text(encoding="utf-8")
    )
    registry = Registry().with_resources(
        [
            (config["$id"], Resource.from_contents(config)),
            (manifest["$id"], Resource.from_contents(manifest)),
        ]
    )
    return Draft202012Validator(
        manifest,
        registry=registry,
        format_checker=FormatChecker(),
    )


def test_task_017_completed_conversion_manifest_validates_and_unknown_key_fails(
    tmp_path: Path,
    replay_factory: Any,
) -> None:
    parent = replay_factory()
    config = ConversionConfig(
        schema_version=1,
        replay_manifests=(parent.relative_to(tmp_path).as_posix(),),
        output_root="output",
        parquet=ConversionParquetConfig("zstd", 64, ("trading_date", "symbol")),
        allow_degraded=False,
    )
    result = convert_replays(config, base_directory=tmp_path)
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))

    validator = _validator()
    validator.validate(manifest)
    unknown = copy.deepcopy(manifest)
    unknown["unexpected"] = True
    assert list(validator.iter_errors(unknown))

    changed_path = copy.deepcopy(manifest)
    changed_path["artefacts"][0]["path"] = "/private/output.parquet"
    assert list(validator.iter_errors(changed_path))


def test_task_017_conversion_schemas_are_valid_and_packaged_identically() -> None:
    for name in ("conversion-config.schema.json", "conversion-manifest.schema.json"):
        root = SCHEMA_ROOT / name
        schema = json.loads(root.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        assert root.read_bytes() == (PACKAGED_ROOT / name).read_bytes()
