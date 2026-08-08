"""TASK-021 predictive report integration, security and accessibility tests."""

from __future__ import annotations

import copy
import hashlib
import json
import xml.etree.ElementTree as et
from dataclasses import replace
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, cast

import pytest

import itchlab_research.reporting.renderers as renderers
import itchlab_research.reporting.service as reporting_service
from itchlab_research.datasets import build_dataset
from itchlab_research.errors import ErrorCode, ReportGenerationError
from itchlab_research.models import (
    load_completed_experiment,
    load_partitioned_dataset,
    train_baselines,
)
from itchlab_research.reporting import generate_report
from itchlab_research.reporting.models import ReportEvidence
from test_dataset import _config as dataset_config
from test_models import _experiment_config


def _completed_experiment(
    tmp_path: Path,
    dataset_conversion_factory: Any,
    *,
    symbol: str = "AAPL",
) -> str:
    conversion_manifest = dataset_conversion_factory(symbol=symbol)
    config = dataset_config(tmp_path, conversion_manifest)
    if symbol != "AAPL":
        config = replace(
            config,
            symbols=(symbol,),
            tick_size4_by_symbol=((symbol, 100),),
        )
    dataset = build_dataset(config, base_directory=tmp_path)
    experiment_config = _experiment_config(tmp_path, dataset.manifest_path)
    loaded = load_partitioned_dataset(experiment_config, base_directory=tmp_path)
    result = train_baselines(loaded, experiment_config, base_directory=tmp_path)
    return result.experiment_id


def _hash_bundle(directory: Path) -> dict[str, str]:
    return {
        path.relative_to(directory).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


class _AccessibilityParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.heading_levels: list[int] = []
        self.tables = 0
        self.captions = 0
        self.header_scopes: list[str | None] = []
        self.images: list[dict[str, str]] = []
        self.links: list[str] = []
        self.figures = 0
        self.figure_captions = 0
        self.plot_summaries = 0
        self.scripts = 0
        self.event_handlers = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {name: value or "" for name, value in attrs}
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self.heading_levels.append(int(tag[1]))
        if tag == "table":
            self.tables += 1
        elif tag == "caption":
            self.captions += 1
        elif tag == "th":
            self.header_scopes.append(values.get("scope"))
        elif tag == "img":
            self.images.append(values)
        elif tag == "a":
            self.links.append(values.get("href", ""))
        elif tag == "figure":
            self.figures += 1
        elif tag == "figcaption":
            self.figure_captions += 1
        elif tag == "script":
            self.scripts += 1
        if "plot-summary" in values.get("class", "").split():
            self.plot_summaries += 1
        self.event_handlers += sum(name.casefold().startswith("on") for name, _value in attrs)


def _assert_accessible_html(path: Path) -> None:
    parser = _AccessibilityParser()
    parser.feed(path.read_text(encoding="utf-8"))
    assert parser.heading_levels[0] == 1
    assert all(
        following <= previous + 1
        for previous, following in zip(
            parser.heading_levels, parser.heading_levels[1:], strict=False
        )
    )
    assert parser.tables >= 10
    assert parser.captions == parser.tables
    assert parser.header_scopes
    assert set(parser.header_scopes) <= {"col", "row"}
    assert len(parser.images) == 6
    assert all(item.get("alt") and item.get("src") for item in parser.images)
    assert parser.figures == parser.figure_captions == parser.plot_summaries == 6
    assert parser.links
    assert all(
        value and not value.startswith(("/", "http:", "https:", "javascript:"))
        for value in parser.links
    )
    assert parser.scripts == 0
    assert parser.event_handlers == 0


def _assert_accessible_svg(path: Path) -> None:
    source = path.read_text(encoding="utf-8")
    root = et.fromstring(source)
    namespace = {"svg": "http://www.w3.org/2000/svg"}
    assert root.attrib["role"] == "img"
    assert root.find("svg:title", namespace) is not None
    assert root.find("svg:desc", namespace) is not None
    text = " ".join(item.text or "" for item in root.findall(".//svg:text", namespace))
    assert "Mean predicted probability (unitless)" in text
    assert "Observed frequency (unitless)" in text
    assert all(value in source for value in ("#0072B2", "#E69F00", "#009E73"))
    assert "stroke-dasharray" in source
    assert "<circle" in source and "<rect" in source and "<polygon" in source


def test_it_011_generates_reproducible_accessible_predictive_report(
    tmp_path: Path,
    dataset_conversion_factory: Any,
) -> None:
    experiment_id = _completed_experiment(tmp_path, dataset_conversion_factory)

    first = generate_report(experiment_id, output_format="both", base_directory=tmp_path)
    first_hashes = _hash_bundle(first.output_directory)
    reused = generate_report(experiment_id, output_format="both", base_directory=tmp_path)
    markdown_only = generate_report(experiment_id, base_directory=tmp_path)
    html_only = generate_report(experiment_id, output_format="html", base_directory=tmp_path)

    assert first.status == "completed"
    assert first.reused is False
    assert reused.reused is True
    assert reused.output_directory == first.output_directory
    assert _hash_bundle(reused.output_directory) == first_hashes
    assert markdown_only.output_directory.name == "markdown"
    assert "report.md" in markdown_only.artefacts
    assert "report.html" not in markdown_only.artefacts
    assert html_only.output_directory.name == "html"
    assert "report.html" in html_only.artefacts
    assert "report.md" not in html_only.artefacts
    assert {"report.md", "report.html", "plot-data/calibration.json"} <= set(first.artefacts)
    assert len(list((first.output_directory / "plots").glob("*.svg"))) == 6
    assert len(list((first.output_directory / "configs").glob("replay-*.json"))) == 3

    markdown = (first.output_directory / "report.md").read_text(encoding="utf-8")
    for heading in (
        "# Predictive research report",
        "## Limitations and non-claims",
        "## Data and code lineage",
        "## Dataset and chronological splits",
        "## Feature definitions",
        "## Models and validation selection",
        "## Validation and test metrics",
        "## Negative results and interpretation",
        "## Calibration",
        "## Reproduction",
    ):
        assert heading in markdown
    assert "spread\\_ticks" in markdown
    assert "Training-frequency prior" in markdown
    assert "Multinomial logistic regression" in markdown
    assert "Histogram gradient boosting" in markdown
    assert "Every declared candidate" in markdown
    assert "Failed candidates are retained" in markdown
    assert "fewer\\_than\\_five\\_trading\\_days" in markdown
    assert "synthetic" in markdown.casefold()
    assert "hidden liquidity" in markdown
    assert "off-venue" in markdown
    assert "no execution simulation" in markdown.casefold()
    assert "no profitability guarantee" in markdown.casefold()
    assert "python -m itchlab_research report --run-id" in markdown
    assert "--output-format both" in markdown
    assert "configs/experiment.json" in markdown
    assert "plot-data/calibration.json" in markdown

    plot_data = json.loads(
        (first.output_directory / "plot-data" / "calibration.json").read_text(encoding="utf-8")
    )
    assert plot_data["experiment_id"] == experiment_id
    assert [item["partition"] for item in plot_data["partitions"]] == [
        "validation",
        "test",
    ]
    assert all(len(item["models"]) == 3 for item in plot_data["partitions"])

    _assert_accessible_html(first.output_directory / "report.html")
    for path in (first.output_directory / "plots").glob("*.svg"):
        _assert_accessible_svg(path)

    for path in first.output_directory.rglob("*"):
        if path.is_file():
            assert str(tmp_path) not in path.read_text(encoding="utf-8")

    authenticated = load_completed_experiment(experiment_id, base_directory=tmp_path)
    validation = copy.deepcopy(authenticated.validation_metrics)
    validation["models"][1]["candidate_evaluations"][0] = {
        "parameters": {"C": 0.01, "penalty": "l2", "solver": "lbfgs", "max_iter": 2000},
        "status": "failed",
        "error_code": "ERR_MODEL_TRAINING",
        "reason": "fit_or_prediction_failed",
    }
    failed_experiment = replace(authenticated, validation_metrics=validation)
    failure_evidence = ReportEvidence(
        experiment=failed_experiment,
        conversions=(),
        replays=(),
        output_format="markdown",
        output_locator=f"runs/report/{experiment_id}/markdown",
    )
    assert "failed" in {str(row[2]) for row in renderers._candidate_rows(failure_evidence)}
    assert "remain visible" in renderers._negative_summary(failure_evidence)

    report_path = first.output_directory / "report.md"
    original_report = report_path.read_bytes()
    report_path.write_bytes(original_report + b"tampered")
    with pytest.raises(ReportGenerationError) as inconsistent_report:
        generate_report(experiment_id, output_format="both", base_directory=tmp_path)
    assert inconsistent_report.value.code is ErrorCode.HASH_MISMATCH
    assert report_path.read_bytes() == original_report + b"tampered"
    report_path.write_bytes(original_report)

    metrics_path = authenticated.manifest_path.parent / "metrics-test.json"
    original_bundle = _hash_bundle(first.output_directory)
    metrics_path.write_bytes(metrics_path.read_bytes() + b"tampered")
    with pytest.raises(ReportGenerationError) as captured:
        generate_report(experiment_id, output_format="both", base_directory=tmp_path)
    assert captured.value.code is ErrorCode.HASH_MISMATCH
    assert _hash_bundle(first.output_directory) == original_bundle


def test_task_021_escapes_markdown_and_html_injected_symbols(
    tmp_path: Path,
    dataset_conversion_factory: Any,
) -> None:
    symbol = "<x>|[a]"
    experiment_id = _completed_experiment(
        tmp_path,
        dataset_conversion_factory,
        symbol=symbol,
    )

    result = generate_report(experiment_id, output_format="both", base_directory=tmp_path)
    markdown = (result.output_directory / "report.md").read_text(encoding="utf-8")
    html_document = (result.output_directory / "report.html").read_text(encoding="utf-8")

    assert symbol not in markdown
    assert "&lt;x&gt;\\|\\[a\\]" in markdown
    assert symbol not in html_document
    assert "&lt;x&gt;|[a]" in html_document
    assert "<x>" not in html_document
    assert "<script" not in html_document.casefold()
    _assert_accessible_html(result.output_directory / "report.html")


def test_task_021_lineage_tamper_and_write_failure_cannot_publish(
    tmp_path: Path,
    dataset_conversion_factory: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    experiment_id = _completed_experiment(tmp_path, dataset_conversion_factory)
    authenticated = load_completed_experiment(experiment_id, base_directory=tmp_path)
    dataset_document = authenticated.dataset.manifest
    conversion_locator = Path(dataset_document["config"]["conversion_manifests"][0])
    conversion_path = tmp_path / conversion_locator
    original_conversion = conversion_path.read_bytes()
    conversion_path.write_bytes(original_conversion + b"tampered")

    with pytest.raises(ReportGenerationError) as lineage_failure:
        generate_report(experiment_id, output_format="markdown", base_directory=tmp_path)
    assert lineage_failure.value.code is ErrorCode.HASH_MISMATCH
    assert not (tmp_path / "runs" / "report" / experiment_id).exists()

    conversion_path.write_bytes(original_conversion)
    calls = 0
    original_write = reporting_service._write_bundle_file

    def fail_second_write(path: Path, content: bytes) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected report write failure")
        original_write(path, content)

    monkeypatch.setattr(reporting_service, "_write_bundle_file", fail_second_write)
    with pytest.raises(ReportGenerationError) as write_failure:
        generate_report(experiment_id, output_format="markdown", base_directory=tmp_path)
    assert write_failure.value.code is ErrorCode.DISK_WRITE
    assert write_failure.value.partial_exists is True
    parent = tmp_path / "runs" / "report" / experiment_id
    assert not (parent / "markdown").exists()
    assert (parent / "markdown.partial").is_dir()
    assert not (parent / ".markdown.lock").exists()


def test_task_021_rejects_invalid_run_id_and_output_format(tmp_path: Path) -> None:
    with pytest.raises(ReportGenerationError) as invalid_id:
        generate_report("../experiment", base_directory=tmp_path)
    assert invalid_id.value.code is ErrorCode.INPUT_PATH

    run_id = "20260808T120000.000000000Z-a1b2c3d4e5f6"
    with pytest.raises(ReportGenerationError) as invalid_format:
        generate_report(run_id, output_format=cast(Any, "pdf"), base_directory=tmp_path)
    assert invalid_format.value.code is ErrorCode.CONFIG_SCHEMA
