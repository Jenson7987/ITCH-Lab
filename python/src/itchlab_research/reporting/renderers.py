"""Deterministic escaped Markdown, HTML and SVG report renderers."""

from __future__ import annotations

import html
import json
import math
import shlex
from collections.abc import Mapping, Sequence
from pathlib import PurePosixPath
from typing import Any, Final, cast

from itchlab_research.reporting.models import ReportEvidence, SimulationReportEvidence

_MODEL_ORDER: Final = ("prior", "logistic_regression", "hist_gradient_boosting")
_MODEL_LABELS: Final = {
    "prior": "Training-frequency prior",
    "logistic_regression": "Multinomial logistic regression",
    "hist_gradient_boosting": "Histogram gradient boosting",
}
_CLASS_ORDER: Final = ("down", "flat", "up")
_CLASS_COLOURS: Final = {
    "down": "#0072B2",
    "flat": "#E69F00",
    "up": "#009E73",
}
_CLASS_DASHES: Final = {"down": "", "flat": "8 4", "up": "2 3"}
_METRIC_KEYS: Final = (
    "multiclass_log_loss",
    "balanced_accuracy",
    "macro_f1",
)
_METRIC_LABELS: Final = {
    "multiclass_log_loss": "Multiclass log loss",
    "balanced_accuracy": "Balanced accuracy",
    "macro_f1": "Macro F1",
}


def _markdown_text(value: object) -> str:
    text = html.escape(str(value), quote=False).replace("\\", "\\\\")
    for character in ("`", "*", "_", "{", "}", "[", "]", "(", ")", "#", "+", "!", "|"):
        text = text.replace(character, f"\\{character}")
    return text.replace("\r", " ").replace("\n", " ")


def _number(value: object, *, digits: int = 6) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Report numbers must be finite")
        return f"{value:.{digits}f}"
    return str(value)


def _compact_json(value: object) -> str:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    )


def _markdown_table(headers: Sequence[str], rows: Sequence[Sequence[object]]) -> str:
    lines = [
        "| " + " | ".join(_markdown_text(header) for header in headers) + " |",
        "| " + " | ".join("---" for _header in headers) + " |",
    ]
    lines.extend("| " + " | ".join(_markdown_text(value) for value in row) + " |" for row in rows)
    return "\n".join(lines)


def _html_table(
    caption: str,
    headers: Sequence[str],
    rows: Sequence[Sequence[object]],
    *,
    first_column_row_header: bool = False,
) -> str:
    header = "".join(f'<th scope="col">{html.escape(value)}</th>' for value in headers)
    body_rows: list[str] = []
    for row in rows:
        cells: list[str] = []
        for index, value in enumerate(row):
            tag = "th" if first_column_row_header and index == 0 else "td"
            scope = ' scope="row"' if tag == "th" else ""
            cells.append(f"<{tag}{scope}>{html.escape(str(value))}</{tag}>")
        body_rows.append("<tr>" + "".join(cells) + "</tr>")
    return (
        '<div class="table-scroll"><table>'
        f"<caption>{html.escape(caption)}</caption>"
        f"<thead><tr>{header}</tr></thead><tbody>{''.join(body_rows)}</tbody>"
        "</table></div>"
    )


def _metric_models(document: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    models = cast(list[dict[str, Any]], document["models"])
    by_name = {cast(str, item["model_name"]): item for item in models}
    return tuple(by_name[name] for name in _MODEL_ORDER)


def _class_counts(model: Mapping[str, Any]) -> dict[str, int]:
    distribution = cast(dict[str, Any], model["class_distribution"])
    return {
        cast(str, item["name"]): cast(int, item["count"])
        for item in cast(list[dict[str, Any]], distribution["classes"])
    }


def _aggregate_metric_rows(document: Mapping[str, Any]) -> list[list[object]]:
    rows: list[list[object]] = []
    for model in _metric_models(document):
        counts = _class_counts(model)
        metrics = cast(dict[str, Any], model["metrics"])
        rows.append(
            [
                _MODEL_LABELS[cast(str, model["model_name"])],
                cast(dict[str, Any], model["class_distribution"])["rows"],
                counts["down"],
                counts["flat"],
                counts["up"],
                _number(metrics["multiclass_log_loss"]),
                _number(metrics["balanced_accuracy"]),
                _number(metrics["macro_f1"]),
            ]
        )
    return rows


def _per_symbol_rows(document: Mapping[str, Any]) -> list[list[object]]:
    rows: list[list[object]] = []
    for model in _metric_models(document):
        for symbol in cast(list[dict[str, Any]], model["by_symbol"]):
            counts = {
                cast(str, item["name"]): cast(int, item["count"])
                for item in cast(
                    list[dict[str, Any]],
                    cast(dict[str, Any], symbol["class_distribution"])["classes"],
                )
            }
            metrics = cast(dict[str, Any], symbol["metrics"])
            rows.append(
                [
                    _MODEL_LABELS[cast(str, model["model_name"])],
                    symbol["symbol"],
                    cast(dict[str, Any], symbol["class_distribution"])["rows"],
                    counts["down"],
                    counts["flat"],
                    counts["up"],
                    _number(metrics["multiclass_log_loss"]),
                    _number(metrics["balanced_accuracy"]),
                    _number(metrics["macro_f1"]),
                ]
            )
    return rows


def _confidence_text(model: Mapping[str, Any]) -> str:
    confidence = cast(dict[str, Any], model["confidence_intervals"])
    if confidence["status"] == "omitted":
        return f"Omitted over {confidence['trading_days']} test day(s): {confidence['reason']}."
    intervals = cast(dict[str, dict[str, Any]], confidence["intervals"])
    values = []
    for name in _METRIC_KEYS:
        interval = intervals[name]
        values.append(
            f"{_METRIC_LABELS[name]} [{_number(interval['lower'])}, {_number(interval['upper'])}]"
        )
    return "; ".join(values) + "."


def _candidate_rows(evidence: ReportEvidence) -> list[list[object]]:
    rows: list[list[object]] = []
    selected = {
        cast(str, item["model_name"]): cast(dict[str, Any], item["selected_parameters"])
        for item in cast(list[dict[str, Any]], evidence.experiment.manifest["selection"]["models"])
    }
    for model in _metric_models(evidence.experiment.validation_metrics):
        name = cast(str, model["model_name"])
        if name == "prior":
            selection = next(
                item
                for item in cast(
                    list[dict[str, Any]], evidence.experiment.manifest["selection"]["models"]
                )
                if item["model_name"] == "prior"
            )
            rows.append(
                [
                    _MODEL_LABELS[name],
                    _compact_json(selection["selected_parameters"]),
                    "completed",
                    _number(selection["validation_log_loss"]),
                    "Training-frequency baseline",
                ]
            )
            continue
        for candidate in cast(list[dict[str, Any]], model["candidate_evaluations"]):
            status = cast(str, candidate["status"])
            rows.append(
                [
                    _MODEL_LABELS[name],
                    _compact_json(candidate["parameters"]),
                    status,
                    (
                        _number(candidate["validation_log_loss"])
                        if status == "completed"
                        else "not available"
                    ),
                    (
                        "Selected"
                        if status == "completed" and candidate["parameters"] == selected[name]
                        else candidate.get("reason", "")
                    ),
                ]
            )
    return rows


def _comparison_rows(evidence: ReportEvidence) -> list[list[object]]:
    models = {
        cast(str, item["model_name"]): item
        for item in _metric_models(evidence.experiment.test_metrics)
    }
    prior_metrics = cast(dict[str, float], models["prior"]["metrics"])
    rows: list[list[object]] = []
    for name in _MODEL_ORDER[1:]:
        metrics = cast(dict[str, float], models[name]["metrics"])
        for metric in _METRIC_KEYS:
            delta = float(metrics[metric]) - float(prior_metrics[metric])
            improved = delta < 0 if metric == "multiclass_log_loss" else delta > 0
            unchanged = delta == 0
            rows.append(
                [
                    _MODEL_LABELS[name],
                    _METRIC_LABELS[metric],
                    _number(prior_metrics[metric]),
                    _number(metrics[metric]),
                    _number(delta),
                    "improved" if improved else "unchanged" if unchanged else "did not improve",
                ]
            )
    return rows


def _negative_summary(evidence: ReportEvidence) -> str:
    comparisons = _comparison_rows(evidence)
    non_improving = [row for row in comparisons if row[-1] != "improved"]
    failed = [row for row in _candidate_rows(evidence) if row[2] == "failed"]
    statements: list[str] = []
    if non_improving:
        statements.append(
            f"{len(non_improving)} of {len(comparisons)} learned-model test comparisons did not "
            "improve on the training-frequency prior; every comparison remains in the table."
        )
    else:
        statements.append(
            "All learned-model test metrics improved on the prior in this run, but predictive "
            "improvement alone is not evidence of executable or profitable performance."
        )
    if failed:
        statements.append(
            f"{len(failed)} declared validation candidate(s) failed and remain visible with their "
            "recorded safe reason."
        )
    else:
        statements.append("No declared validation candidate failed in this run.")
    return " ".join(statements)


def _calibration_summary(model: Mapping[str, Any]) -> str:
    parts: list[str] = []
    calibration = cast(dict[str, Any], model["calibration"])
    for class_entry in cast(list[dict[str, Any]], calibration["classes"]):
        name = cast(str, class_entry["name"])
        bins = cast(list[dict[str, Any]], class_entry["bins"])
        populated = [item for item in bins if cast(int, item["count"]) > 0]
        gaps = [
            abs(float(item["observed_frequency"]) - float(item["mean_probability"]))
            for item in populated
        ]
        largest = max(gaps, default=0.0)
        parts.append(
            f"{name}: {len(populated)}/{len(bins)} populated bins, largest absolute gap "
            f"{largest:.4f}"
        )
    return "; ".join(parts) + "."


def _calibration_plot_data(evidence: ReportEvidence) -> dict[str, Any]:
    partitions: list[dict[str, Any]] = []
    for partition, document in (
        ("validation", evidence.experiment.validation_metrics),
        ("test", evidence.experiment.test_metrics),
    ):
        partitions.append(
            {
                "partition": partition,
                "models": [
                    {
                        "model_name": model["model_name"],
                        "calibration": model["calibration"],
                        "text_summary": _calibration_summary(model),
                    }
                    for model in _metric_models(document)
                ],
            }
        )
    return {
        "schema_version": 1,
        "experiment_id": evidence.experiment.experiment_id,
        "class_order": list(_CLASS_ORDER),
        "partitions": partitions,
    }


def _point_marker(class_name: str, x: float, y: float) -> str:
    colour = _CLASS_COLOURS[class_name]
    if class_name == "down":
        return f'<circle cx="{x:.2f}" cy="{y:.2f}" r="4" fill="{colour}"/>'
    if class_name == "flat":
        return f'<rect x="{x - 4:.2f}" y="{y - 4:.2f}" width="8" height="8" fill="{colour}"/>'
    points = f"{x:.2f},{y - 5:.2f} {x - 5:.2f},{y + 4:.2f} {x + 5:.2f},{y + 4:.2f}"
    return f'<polygon points="{points}" fill="{colour}"/>'


def _calibration_svg(partition: str, model: Mapping[str, Any]) -> str:
    left = 82.0
    right = 690.0
    top = 56.0
    bottom = 420.0
    plot_width = right - left
    plot_height = bottom - top
    model_name = cast(str, model["model_name"])
    label = _MODEL_LABELS[model_name]
    title = f"{partition.capitalize()} calibration — {label}"
    description = _calibration_summary(model)
    elements = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="720" height="500" '
        'viewBox="0 0 720 500" role="img" aria-labelledby="plot-title plot-desc">',
        f'<title id="plot-title">{html.escape(title)}</title>',
        f'<desc id="plot-desc">{html.escape(description)}</desc>',
        '<rect width="720" height="500" fill="#FFFFFF"/>',
        f'<text x="360" y="28" text-anchor="middle" font-family="sans-serif" '
        f'font-size="18" font-weight="bold">{html.escape(title)}</text>',
    ]
    for tick in range(6):
        value = tick / 5
        x = left + value * plot_width
        y = bottom - value * plot_height
        elements.extend(
            [
                f'<line x1="{x:.2f}" y1="{top:.2f}" x2="{x:.2f}" y2="{bottom:.2f}" '
                'stroke="#D9D9D9" stroke-width="1"/>',
                f'<line x1="{left:.2f}" y1="{y:.2f}" x2="{right:.2f}" y2="{y:.2f}" '
                'stroke="#D9D9D9" stroke-width="1"/>',
                f'<text x="{x:.2f}" y="442" text-anchor="middle" font-family="sans-serif" '
                f'font-size="12">{value:.1f}</text>',
                f'<text x="68" y="{y + 4:.2f}" text-anchor="end" font-family="sans-serif" '
                f'font-size="12">{value:.1f}</text>',
            ]
        )
    elements.extend(
        [
            f'<line x1="{left:.2f}" y1="{bottom:.2f}" x2="{right:.2f}" y2="{top:.2f}" '
            'stroke="#555555" stroke-width="2" stroke-dasharray="5 5"/>',
            f'<line x1="{left:.2f}" y1="{bottom:.2f}" x2="{right:.2f}" y2="{bottom:.2f}" '
            'stroke="#111111" stroke-width="2"/>',
            f'<line x1="{left:.2f}" y1="{top:.2f}" x2="{left:.2f}" y2="{bottom:.2f}" '
            'stroke="#111111" stroke-width="2"/>',
            '<text x="386" y="478" text-anchor="middle" font-family="sans-serif" '
            'font-size="14">Mean predicted probability (unitless)</text>',
            '<text x="20" y="238" text-anchor="middle" font-family="sans-serif" '
            'font-size="14" transform="rotate(-90 20 238)">Observed frequency (unitless)</text>',
        ]
    )
    calibration = cast(dict[str, Any], model["calibration"])
    for series_index, class_entry in enumerate(cast(list[dict[str, Any]], calibration["classes"])):
        class_name = cast(str, class_entry["name"])
        points: list[tuple[float, float]] = []
        for item in cast(list[dict[str, Any]], class_entry["bins"]):
            if cast(int, item["count"]) == 0:
                continue
            probability = float(item["mean_probability"])
            observed = float(item["observed_frequency"])
            points.append(
                (
                    left + probability * plot_width,
                    bottom - observed * plot_height,
                )
            )
        if points:
            path = " ".join(
                ("M" if index == 0 else "L") + f" {x:.2f} {y:.2f}"
                for index, (x, y) in enumerate(points)
            )
            dash = _CLASS_DASHES[class_name]
            dash_attribute = f' stroke-dasharray="{dash}"' if dash else ""
            elements.append(
                f'<path d="{path}" fill="none" stroke="{_CLASS_COLOURS[class_name]}" '
                f'stroke-width="3"{dash_attribute}/>'
            )
            elements.extend(_point_marker(class_name, x, y) for x, y in points)
        legend_y = 72 + series_index * 24
        elements.extend(
            [
                f'<line x1="520" y1="{legend_y}" x2="555" y2="{legend_y}" '
                f'stroke="{_CLASS_COLOURS[class_name]}" stroke-width="3" '
                + (
                    f'stroke-dasharray="{_CLASS_DASHES[class_name]}"/>'
                    if _CLASS_DASHES[class_name]
                    else "/>"
                ),
                _point_marker(class_name, 538, float(legend_y)),
                f'<text x="565" y="{legend_y + 4}" font-family="sans-serif" '
                f'font-size="13">{html.escape(class_name)}</text>',
            ]
        )
    elements.append("</svg>\n")
    return "".join(elements)


def _dataset_split_rows(evidence: ReportEvidence) -> list[list[object]]:
    dataset = evidence.experiment.dataset.manifest
    counts = {
        cast(str, item["partition"]): item
        for item in cast(list[dict[str, Any]], dataset["counts"]["by_partition"])
    }
    partitions = cast(dict[str, list[str]], dataset["partitions"])
    rows: list[list[object]] = []
    for partition in ("train", "validation", "test"):
        row_counts = cast(dict[str, int], counts[partition]["rows"])
        classes = cast(dict[str, int], counts[partition]["classes"])
        rows.append(
            [
                partition,
                ", ".join(partitions[f"{partition}_dates"]),
                row_counts["qualifying_rows"],
                row_counts["dropped_incomplete_history"],
                row_counts["dropped_unavailable_primary_label"],
                row_counts["dropped_by_row_stride"],
                row_counts["retained_rows"],
                classes["down"],
                classes["flat"],
                classes["up"],
            ]
        )
    return rows


def _lineage_rows(evidence: ReportEvidence) -> list[list[object]]:
    experiment = evidence.experiment
    rows: list[list[object]] = [
        [
            "experiment",
            experiment.experiment_id,
            experiment.manifest_sha256,
            experiment.manifest["config_sha256"],
            experiment.manifest["identity_sha256"],
            experiment.manifest["status"],
        ],
        [
            "dataset",
            experiment.dataset.dataset_id,
            experiment.dataset.manifest_sha256,
            experiment.dataset.manifest["config_sha256"],
            experiment.dataset.manifest["identity_sha256"],
            experiment.dataset.manifest["status"],
        ],
    ]
    rows.extend(
        [
            item.kind,
            item.run_id,
            item.sha256,
            item.document["config_sha256"],
            item.document["identity_sha256"],
            item.document["status"],
        ]
        for item in (*evidence.conversions, *evidence.replays)
    )
    return rows


def _source_rows(evidence: ReportEvidence) -> list[list[object]]:
    rows: list[list[object]] = []
    for item in evidence.replays:
        source = cast(dict[str, Any], item.document["source"])
        rows.append(
            [
                source["trading_date"],
                source["canonical_name"],
                source["sha256"],
                source["size_bytes"],
                item.run_id,
                item.document["code_revision"],
                "yes" if item.document["publishable"] else "no",
            ]
        )
    return rows


def _tool_rows(evidence: ReportEvidence) -> list[list[object]]:
    rows: list[list[object]] = []
    experiment_tool = cast(dict[str, Any], evidence.experiment.manifest["tool"])
    dataset_tool = cast(dict[str, Any], evidence.experiment.dataset.manifest["tool"])
    for stage, tool in (("experiment", experiment_tool), ("dataset", dataset_tool)):
        rows.append(
            [
                stage,
                tool["application_version"],
                tool["sha256"],
                tool["python_version"],
                tool["pyarrow_version"],
            ]
        )
    for item in evidence.conversions:
        tool = cast(dict[str, Any], item.document["tool"])
        rows.append(
            [
                f"conversion {item.run_id}",
                tool["application_version"],
                tool["sha256"],
                tool["python_version"],
                tool["pyarrow_version"],
            ]
        )
    return rows


def _feature_rows(evidence: ReportEvidence) -> list[list[object]]:
    features = cast(
        list[dict[str, Any]],
        evidence.experiment.dataset.manifest["feature_catalogue"]["features"],
    )
    return [
        [
            item["name"],
            item["dtype"],
            "yes" if item["nullable"] else "no",
            item["formula"],
            f"{item['lookback']['kind']}:{item['lookback']['value']}",
            item["unit"],
            item["null_policy"],
        ]
        for item in features
    ]


def _selected_model_rows(evidence: ReportEvidence) -> list[list[object]]:
    return [
        [
            _MODEL_LABELS[cast(str, item["model_name"])],
            item["status"],
            _compact_json(item["selected_parameters"]),
            _number(item["validation_log_loss"]),
        ]
        for item in cast(list[dict[str, Any]], evidence.experiment.manifest["selection"]["models"])
    ]


def _confusion_rows(model: Mapping[str, Any]) -> list[list[object]]:
    matrix = cast(dict[str, Any], cast(dict[str, Any], model["metrics"])["confusion_matrix"])
    rows = cast(list[list[int]], matrix["rows_true_columns_predicted"])
    return [[name, *values] for name, values in zip(_CLASS_ORDER, rows, strict=True)]


def _synthetic_notice(evidence: ReportEvidence) -> str:
    names = [
        cast(str, cast(dict[str, Any], item.document["source"])["canonical_name"])
        for item in evidence.replays
    ]
    if names and all("synthetic" in name.casefold() for name in names):
        return (
            "All recorded source basenames are explicitly labelled synthetic. These inputs are "
            "test fixtures and must not be presented as real market events."
        )
    return (
        "Source identities are recorded without redistributing input bytes. The reviewer remains "
        "responsible for confirming source authorisation and whether an input is synthetic."
    )


def _warnings(evidence: ReportEvidence) -> tuple[str, ...]:
    values = list(cast(list[str], evidence.experiment.manifest["warnings"]))
    if any(item.document["status"] == "degraded" for item in evidence.conversions):
        values.append("At least one conversion lineage entry is degraded.")
    if any(item.document["status"] == "degraded" for item in evidence.replays):
        values.append("At least one replay lineage entry is degraded.")
    if any(not cast(bool, item.document["publishable"]) for item in evidence.replays):
        values.append("At least one replay is marked non-publishable.")
    if any(
        cast(str, item.document["code_revision"]).startswith("unknown")
        or cast(str, item.document["code_revision"]).endswith("+dirty")
        for item in evidence.replays
    ):
        values.append("At least one replay has an unknown or dirty recorded Git revision.")
    return tuple(dict.fromkeys(values))


def report_warnings(evidence: ReportEvidence) -> tuple[str, ...]:
    """Return deterministic prominent warnings for the command result and report."""
    return _warnings(evidence)


def _config_files(evidence: ReportEvidence) -> dict[str, bytes]:
    documents: dict[str, Mapping[str, Any]] = {
        "configs/experiment.json": cast(dict[str, Any], evidence.experiment.manifest["config"]),
        "configs/dataset.json": cast(
            dict[str, Any], evidence.experiment.dataset.manifest["config"]
        ),
    }
    for item in evidence.conversions:
        documents[f"configs/conversion-{item.run_id}.json"] = cast(
            dict[str, Any], item.document["config"]
        )
    for item in evidence.replays:
        documents[f"configs/replay-{item.run_id}.json"] = cast(
            dict[str, Any], item.document["config"]
        )
    return {
        path: (
            json.dumps(
                document,
                indent=2,
                sort_keys=True,
                ensure_ascii=False,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
        for path, document in documents.items()
    }


def _reproduction_commands(evidence: ReportEvidence) -> list[str]:
    prefix = evidence.output_locator
    commands: list[str] = []
    for item in evidence.replays:
        config = f"{prefix}/configs/replay-{item.run_id}.json"
        commands.append(
            f"./build/release/itchlab replay --config {shlex.quote(config)} --output-root runs"
        )
        run_directory = PurePosixPath(item.locator).parent.as_posix()
        commands.append(
            f"./build/release/itchlab validate --run {shlex.quote(run_directory)} --deep"
        )
    for item in evidence.conversions:
        config = f"{prefix}/configs/conversion-{item.run_id}.json"
        commands.append(f"python -m itchlab_research convert --config {shlex.quote(config)}")
    commands.extend(
        [
            "python -m itchlab_research build-dataset --config "
            + shlex.quote(f"{prefix}/configs/dataset.json"),
            "python -m itchlab_research train --config "
            + shlex.quote(f"{prefix}/configs/experiment.json"),
            "python -m itchlab_research report --run-id "
            + shlex.quote(evidence.experiment.experiment_id)
            + " --output-format "
            + evidence.output_format,
        ]
    )
    return commands


def _markdown_report(evidence: ReportEvidence) -> str:
    warnings = _warnings(evidence)
    selection = cast(dict[str, Any], evidence.experiment.manifest["selection"])
    lines = [
        "# Predictive research report",
        "",
        f"Experiment `{_markdown_text(evidence.experiment.experiment_id)}`. ",
        "",
        "This is an offline historical predictive-research report. It is not a live-trading "
        "system, trading advice, an execution simulation or evidence of profitability.",
        "",
        "## Scope and status",
        "",
        "The report covers the frozen predictive experiment only: authenticated lineage, "
        "chronological partitions, features, model selection, validation/test metrics and "
        "calibration. Test data were evaluated once after validation selection.",
        "",
        f"{_synthetic_notice(evidence)}",
        "",
        "## Limitations and non-claims",
        "",
        "- No execution simulation, fill model, latency/cost sensitivity, inventory or P&L is "
        "included in TASK-021.",
        "- Nasdaq visible order-book data do not reveal hidden liquidity, off-venue activity, "
        "market impact or counterfactual participant behaviour.",
        "- Predictive classification metrics do not establish economic value or executable "
        "performance.",
        "- Results are historical and local; there is no profitability guarantee.",
    ]
    if warnings:
        lines.extend(["", "### Recorded warnings", ""])
        lines.extend(f"- {_markdown_text(value)}" for value in warnings)
    lines.extend(
        [
            "",
            "## Data and code lineage",
            "",
            _markdown_table(
                (
                    "Stage",
                    "Run ID",
                    "Manifest SHA-256",
                    "Config SHA-256",
                    "Identity SHA-256",
                    "Status",
                ),
                _lineage_rows(evidence),
            ),
            "",
            "### Source and replay evidence",
            "",
            _markdown_table(
                (
                    "Trading date",
                    "Source basename",
                    "Source SHA-256",
                    "Bytes",
                    "Replay ID",
                    "Git revision",
                    "Publishable",
                ),
                _source_rows(evidence),
            ),
            "",
            "### Python tool identities",
            "",
            _markdown_table(
                ("Stage", "Version", "Package SHA-256", "Python", "PyArrow"),
                _tool_rows(evidence),
            ),
            "",
            "Replay Git revisions identify the C++ replay code. Python stages are identified by "
            "their recorded package-content SHA-256 values.",
            "",
            "## Dataset and chronological splits",
            "",
            _markdown_table(
                (
                    "Partition",
                    "Dates",
                    "Qualifying",
                    "Dropped history",
                    "Dropped primary tail",
                    "Dropped stride",
                    "Retained",
                    "Down",
                    "Flat",
                    "Up",
                ),
                _dataset_split_rows(evidence),
            ),
            "",
            "Partitions contain complete non-overlapping chronological days. Features were "
            "computed from current/past information; primary labels were computed separately and "
            "joined by immutable row identity.",
            "",
            "## Feature definitions",
            "",
            _markdown_table(
                ("Feature", "Dtype", "Nullable", "Formula", "Lookback", "Unit", "Null policy"),
                _feature_rows(evidence),
            ),
            "",
            "## Models and validation selection",
            "",
            f"Selection metric: `{_markdown_text(selection['metric'])}`; "
            f"tie tolerance: {_number(selection['tie_tolerance'])}.",
            "",
            _markdown_table(
                ("Model", "Status", "Selected parameters", "Validation log loss"),
                _selected_model_rows(evidence),
            ),
            "",
            "### Every declared candidate",
            "",
            _markdown_table(
                ("Model", "Parameters", "Status", "Validation log loss", "Selection/reason"),
                _candidate_rows(evidence),
            ),
            "",
            "Failed candidates are retained rather than silently removed.",
            "",
            "## Validation and test metrics",
            "",
        ]
    )
    metric_headers = (
        "Model",
        "Rows",
        "Down",
        "Flat",
        "Up",
        "Log loss",
        "Balanced accuracy",
        "Macro F1",
    )
    for partition, document in (
        ("Validation", evidence.experiment.validation_metrics),
        ("Test", evidence.experiment.test_metrics),
    ):
        lines.extend(
            [
                f"### {partition} aggregate",
                "",
                _markdown_table(metric_headers, _aggregate_metric_rows(document)),
                "",
            ]
        )
        if partition == "Test":
            lines.append("Confidence intervals:")
            lines.append("")
            lines.extend(
                f"- {_markdown_text(_MODEL_LABELS[cast(str, model['model_name'])])}: "
                f"{_markdown_text(_confidence_text(model))}"
                for model in _metric_models(document)
            )
            lines.append("")
    lines.extend(
        [
            "### Per-symbol test metrics",
            "",
            _markdown_table(
                (
                    "Model",
                    "Symbol",
                    "Rows",
                    "Down",
                    "Flat",
                    "Up",
                    "Log loss",
                    "Balanced accuracy",
                    "Macro F1",
                ),
                _per_symbol_rows(evidence.experiment.test_metrics),
            ),
            "",
            "### Confusion matrices",
            "",
            "Rows are true classes and columns are predicted classes in down/flat/up order.",
            "",
        ]
    )
    for partition, document in (
        ("Validation", evidence.experiment.validation_metrics),
        ("Test", evidence.experiment.test_metrics),
    ):
        for model in _metric_models(document):
            lines.extend(
                [
                    f"#### {partition} — {_MODEL_LABELS[cast(str, model['model_name'])]}",
                    "",
                    _markdown_table(
                        ("True class", "Predicted down", "Predicted flat", "Predicted up"),
                        _confusion_rows(model),
                    ),
                    "",
                ]
            )
    lines.extend(
        [
            "## Negative results and interpretation",
            "",
            _negative_summary(evidence),
            "",
            _markdown_table(
                ("Model", "Metric", "Prior", "Model", "Delta model-prior", "Interpretation"),
                _comparison_rows(evidence),
            ),
            "",
            "These held-out comparisons are reported after selection and are not used to retune "
            "features, hyperparameters or selection rules.",
            "",
            "## Calibration",
            "",
            "The dashed diagonal represents perfect one-vs-rest calibration. Plot data are "
            "available in [calibration.json](plot-data/calibration.json).",
            "",
        ]
    )
    for partition, document in (
        ("validation", evidence.experiment.validation_metrics),
        ("test", evidence.experiment.test_metrics),
    ):
        for model in _metric_models(document):
            model_name = cast(str, model["model_name"])
            label = _MODEL_LABELS[model_name]
            path = f"plots/calibration-{partition}-{model_name}.svg"
            summary = _calibration_summary(model)
            lines.extend(
                [
                    f"### {partition.capitalize()} — {label}",
                    "",
                    f"![{_markdown_text(partition.capitalize())} calibration for "
                    f"{_markdown_text(label)}]({path})",
                    "",
                    f"*Caption:* One-vs-rest calibration for {_markdown_text(label)} on the "
                    f"{_markdown_text(partition)} partition.",
                    "",
                    f"Text summary: {_markdown_text(summary)}",
                    "",
                ]
            )
    lines.extend(
        [
            "## Reproduction",
            "",
            "Run from the repository root. Obtain authorised source files matching the recorded "
            "basenames and SHA-256 values; the application does not download them.",
            "",
            "Canonical configuration snapshots:",
            "",
            "- [Experiment config](configs/experiment.json)",
            "- [Dataset config](configs/dataset.json)",
            "",
        ]
    )
    for command in _reproduction_commands(evidence):
        lines.append(f"    {command}")
    lines.extend(
        [
            "",
            "Reproduction is incomplete if source hashes, recorded configs, manifest hashes or "
            "package-content identities do not match.",
            "",
        ]
    )
    return "\n".join(lines)


def _html_report(evidence: ReportEvidence) -> str:
    warnings = _warnings(evidence)
    selection = cast(dict[str, Any], evidence.experiment.manifest["selection"])
    sections: list[str] = [
        '<!doctype html><html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        "<title>ITCH-Lab predictive research report</title>",
        "<style>body{font-family:system-ui,sans-serif;line-height:1.5;max-width:1100px;"
        "margin:auto;padding:1rem;color:#111;background:#fff}.skip-link{position:absolute;left:-9999px}"
        ".skip-link:focus{left:1rem;top:1rem;background:#fff;padding:.5rem;border:2px solid #111}"
        ".notice{border-left:.4rem solid #0072B2;padding:.75rem;background:#f3f7fa}"
        ".warning{border-left:.4rem solid #D55E00;padding:.75rem;background:#fff6f0}"
        ".table-scroll{overflow-x:auto}table{border-collapse:collapse;width:100%;margin:1rem 0}"
        "caption{font-weight:bold;text-align:left;margin-bottom:.35rem}th,td{border:1px solid #777;"
        "padding:.4rem;text-align:left;vertical-align:top}thead{background:#eee}code,pre{font-family:"
        "ui-monospace,monospace}pre{overflow-x:auto;background:#f5f5f5;padding:.75rem}img{max-width:100%;"
        "height:auto}figure{margin:1rem 0}figcaption{font-weight:600}</style></head><body>",
        '<a class="skip-link" href="#main-content">Skip to report content</a>',
        '<main id="main-content"><h1>Predictive research report</h1>',
        f"<p>Experiment <code>{html.escape(evidence.experiment.experiment_id)}</code>.</p>",
        '<p class="notice">This is an offline historical predictive-research report. It is not '
        "a live-trading system, trading advice, an execution simulation or evidence of "
        "profitability.</p>",
        '<section aria-labelledby="scope"><h2 id="scope">Scope and status</h2>',
        "<p>The report covers the frozen predictive experiment only: authenticated lineage, "
        "chronological partitions, features, model selection, validation/test metrics and "
        "calibration. Test data were evaluated once after validation selection.</p>",
        f"<p>{html.escape(_synthetic_notice(evidence))}</p></section>",
        '<section aria-labelledby="limitations"><h2 id="limitations">'
        "Limitations and non-claims</h2>",
        "<ul><li>No execution simulation, fill model, latency/cost sensitivity, inventory or "
        "P&amp;L "
        "is included in TASK-021.</li><li>Nasdaq visible order-book data do not reveal hidden "
        "liquidity, off-venue activity, market impact or counterfactual participant behaviour.</li>"
        "<li>Predictive classification metrics do not establish economic value or executable "
        "performance.</li><li>Results are historical and local; there is no profitability "
        "guarantee."
        "</li></ul>",
    ]
    if warnings:
        sections.extend(
            [
                '<aside class="warning" aria-labelledby="warnings"><h3 id="warnings">'
                "Recorded warnings</h3><ul>",
                "".join(f"<li>{html.escape(value)}</li>" for value in warnings),
                "</ul></aside>",
            ]
        )
    sections.extend(
        [
            "</section>",
            '<section aria-labelledby="lineage"><h2 id="lineage">Data and code lineage</h2>',
            _html_table(
                "Manifest lineage and identities",
                (
                    "Stage",
                    "Run ID",
                    "Manifest SHA-256",
                    "Config SHA-256",
                    "Identity SHA-256",
                    "Status",
                ),
                _lineage_rows(evidence),
            ),
            '<h3 id="sources">Source and replay evidence</h3>',
            _html_table(
                "Authenticated source and replay evidence",
                (
                    "Trading date",
                    "Source basename",
                    "Source SHA-256",
                    "Bytes",
                    "Replay ID",
                    "Git revision",
                    "Publishable",
                ),
                _source_rows(evidence),
            ),
            '<h3 id="tools">Python tool identities</h3>',
            _html_table(
                "Recorded Python package identities",
                ("Stage", "Version", "Package SHA-256", "Python", "PyArrow"),
                _tool_rows(evidence),
            ),
            "<p>Replay Git revisions identify the C++ replay code. Python stages are identified "
            "by their recorded package-content SHA-256 values.</p></section>",
            '<section aria-labelledby="dataset"><h2 id="dataset">'
            "Dataset and chronological splits</h2>",
            _html_table(
                "Chronological split, filtering and class counts",
                (
                    "Partition",
                    "Dates",
                    "Qualifying",
                    "Dropped history",
                    "Dropped primary tail",
                    "Dropped stride",
                    "Retained",
                    "Down",
                    "Flat",
                    "Up",
                ),
                _dataset_split_rows(evidence),
            ),
            "<p>Partitions contain complete non-overlapping chronological days. Features were "
            "computed from current/past information; primary labels were computed separately and "
            "joined by immutable row identity.</p></section>",
            '<section aria-labelledby="features"><h2 id="features">Feature definitions</h2>',
            _html_table(
                "Version-1 feature catalogue",
                ("Feature", "Dtype", "Nullable", "Formula", "Lookback", "Unit", "Null policy"),
                _feature_rows(evidence),
            ),
            "</section>",
            '<section aria-labelledby="models"><h2 id="models">'
            "Models and validation selection</h2>",
            f"<p>Selection metric: <code>{html.escape(cast(str, selection['metric']))}"
            f"</code>; tie tolerance: {_number(selection['tie_tolerance'])}.</p>",
            _html_table(
                "Frozen model selections",
                ("Model", "Status", "Selected parameters", "Validation log loss"),
                _selected_model_rows(evidence),
            ),
            '<h3 id="candidates">Every declared candidate</h3>',
            _html_table(
                "All validation candidates, including failures",
                ("Model", "Parameters", "Status", "Validation log loss", "Selection/reason"),
                _candidate_rows(evidence),
            ),
            "<p>Failed candidates are retained rather than silently removed.</p></section>",
            '<section aria-labelledby="metrics"><h2 id="metrics">Validation and test metrics</h2>',
        ]
    )
    metric_headers = (
        "Model",
        "Rows",
        "Down",
        "Flat",
        "Up",
        "Log loss",
        "Balanced accuracy",
        "Macro F1",
    )
    for partition, document in (
        ("Validation", evidence.experiment.validation_metrics),
        ("Test", evidence.experiment.test_metrics),
    ):
        sections.extend(
            [
                f"<h3>{partition} aggregate</h3>",
                _html_table(
                    f"{partition} aggregate predictive metrics",
                    metric_headers,
                    _aggregate_metric_rows(document),
                ),
            ]
        )
        if partition == "Test":
            sections.append("<p>Confidence intervals:</p><ul>")
            sections.extend(
                f"<li>{html.escape(_MODEL_LABELS[cast(str, model['model_name'])])}: "
                f"{html.escape(_confidence_text(model))}</li>"
                for model in _metric_models(document)
            )
            sections.append("</ul>")
    sections.extend(
        [
            "<h3>Per-symbol test metrics</h3>",
            _html_table(
                "Per-symbol test metrics",
                (
                    "Model",
                    "Symbol",
                    "Rows",
                    "Down",
                    "Flat",
                    "Up",
                    "Log loss",
                    "Balanced accuracy",
                    "Macro F1",
                ),
                _per_symbol_rows(evidence.experiment.test_metrics),
            ),
            "<h3>Confusion matrices</h3><p>Rows are true classes and columns are predicted classes "
            "in down/flat/up order.</p>",
        ]
    )
    for partition, document in (
        ("Validation", evidence.experiment.validation_metrics),
        ("Test", evidence.experiment.test_metrics),
    ):
        for model in _metric_models(document):
            label = _MODEL_LABELS[cast(str, model["model_name"])]
            sections.extend(
                [
                    f"<h4>{html.escape(partition)} — {html.escape(label)}</h4>",
                    _html_table(
                        f"{partition} confusion matrix for {label}",
                        ("True class", "Predicted down", "Predicted flat", "Predicted up"),
                        _confusion_rows(model),
                        first_column_row_header=True,
                    ),
                ]
            )
    sections.extend(
        [
            "</section>",
            '<section aria-labelledby="negative"><h2 id="negative">'
            "Negative results and interpretation</h2>",
            f"<p>{html.escape(_negative_summary(evidence))}</p>",
            _html_table(
                "Held-out learned-model comparisons with the prior",
                ("Model", "Metric", "Prior", "Model", "Delta model-prior", "Interpretation"),
                _comparison_rows(evidence),
            ),
            "<p>These held-out comparisons are reported after selection and are not used to "
            "retune features, hyperparameters or selection rules.</p></section>",
            '<section aria-labelledby="calibration"><h2 id="calibration">Calibration</h2>',
            "<p>The dashed diagonal represents perfect one-vs-rest calibration. Plot data are "
            '<a href="plot-data/calibration.json">available as JSON</a>.</p>',
        ]
    )
    for partition, document in (
        ("validation", evidence.experiment.validation_metrics),
        ("test", evidence.experiment.test_metrics),
    ):
        for model in _metric_models(document):
            model_name = cast(str, model["model_name"])
            label = _MODEL_LABELS[model_name]
            path = f"plots/calibration-{partition}-{model_name}.svg"
            summary = _calibration_summary(model)
            alt = f"{partition.capitalize()} one-vs-rest calibration for {label}"
            sections.extend(
                [
                    f"<h3>{html.escape(partition.capitalize())} — {html.escape(label)}</h3>",
                    f'<figure><img src="{html.escape(path, quote=True)}" '
                    f'alt="{html.escape(alt, quote=True)}">',
                    f"<figcaption>One-vs-rest calibration for {html.escape(label)} on the "
                    f"{html.escape(partition)} partition.</figcaption></figure>",
                    f'<p class="plot-summary">Text summary: {html.escape(summary)}</p>',
                ]
            )
    sections.extend(
        [
            "</section>",
            '<section aria-labelledby="reproduction"><h2 id="reproduction">Reproduction</h2>',
            "<p>Run from the repository root. Obtain authorised source files matching the recorded "
            "basenames and SHA-256 values; the application does not download them.</p>",
            "<p>Canonical configuration snapshots: "
            '<a href="configs/experiment.json">experiment</a> '
            'and <a href="configs/dataset.json">dataset</a>.</p><pre><code>',
            html.escape("\n".join(_reproduction_commands(evidence))),
            "</code></pre>",
            "<p>Reproduction is incomplete if source hashes, recorded configs, manifest hashes or "
            "package-content identities do not match.</p></section></main></body></html>\n",
        ]
    )
    return "".join(sections)


def render_report_bundle(evidence: ReportEvidence) -> dict[str, bytes]:
    """Render the complete deterministic file set for one requested report format."""
    files_value = _config_files(evidence)
    plot_data = _calibration_plot_data(evidence)
    files_value["plot-data/calibration.json"] = (
        json.dumps(
            plot_data,
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")
    for partition, document in (
        ("validation", evidence.experiment.validation_metrics),
        ("test", evidence.experiment.test_metrics),
    ):
        for model in _metric_models(document):
            model_name = cast(str, model["model_name"])
            files_value[f"plots/calibration-{partition}-{model_name}.svg"] = _calibration_svg(
                partition, model
            ).encode("utf-8")
    if evidence.output_format in {"markdown", "both"}:
        files_value["report.md"] = (_markdown_report(evidence).rstrip() + "\n").encode("utf-8")
    if evidence.output_format in {"html", "both"}:
        files_value["report.html"] = _html_report(evidence).encode("utf-8")
    return dict(sorted(files_value.items()))


def _simulation_metric_rows(evidence: SimulationReportEvidence) -> list[list[object]]:
    rows: list[list[object]] = []
    for item in cast(list[dict[str, Any]], evidence.simulation.metrics["scenarios"]):
        metrics = cast(dict[str, Any], item["metrics"])
        rows.append(
            [
                item["scenario_id"],
                item["strategy_name"],
                item["signal_weight_ticks"],
                item["submission_latency_ns"],
                item["cancellation_latency_ns"],
                item["maker_fee_microusd_per_share"],
                item["taker_fee_microusd_per_share"],
                metrics["passive_fill_count"],
                metrics["max_abs_inventory_by_symbol"],
                metrics["marked_pnl_microusd"],
                metrics["passive_spread_capture_microusd"],
                metrics["inventory_mark_to_market_microusd"],
                metrics["terminal_liquidation_slippage_microusd"],
                metrics["signed_fee_microusd"],
                metrics["max_drawdown_microusd"],
                metrics["turnover_microusd"],
                metrics["adverse_selection_100ms_microusd"],
                metrics["adverse_selection_coverage"],
            ]
        )
    return rows


_SIMULATION_HEADERS = (
    "Scenario",
    "Strategy",
    "Signal weight (ticks)",
    "Submission latency (ns)",
    "Cancellation latency (ns)",
    "Maker cost (microusd/share)",
    "Taker cost (microusd/share)",
    "Passive fills",
    "Maximum absolute inventory",
    "Marked P&L (microusd)",
    "Spread capture (microusd)",
    "Inventory mark-to-market (microusd)",
    "Liquidation slippage (microusd)",
    "Signed fees (microusd)",
    "Maximum drawdown (microusd)",
    "Turnover (microusd)",
    "100 ms adverse selection (microusd)",
    "Markout coverage",
)


def _simulation_markdown(evidence: SimulationReportEvidence) -> str:
    manifest = evidence.simulation.manifest
    selection = cast(dict[str, Any], manifest["selection"])
    assumptions = cast(list[str], manifest["assumptions"])
    limitations = cast(list[str], manifest["limitations"])
    warnings = cast(list[str], manifest["warnings"])
    model = cast(dict[str, Any] | None, selection.get("model"))
    weight = cast(dict[str, Any] | None, selection.get("signal_weight"))
    selected_weight = "unavailable" if weight is None else weight["selected"]
    lines = [
        "# Conservative simulation comparison",
        "",
        f"Simulation `{_markdown_text(evidence.simulation.simulation_id)}`. Historical research "
        "only; this is not a live-trading system, trading advice or evidence of profitability.",
        "",
        "## Selection frozen before test",
        "",
        (
            "Baseline-only run; no signal model or weight was selected."
            if model is None
            else (
                f"Validation log loss selected `{_markdown_text(model['model_name'])}`. "
                f"Validation-day P&L selected signal weight `{_markdown_text(selected_weight)}` "
                "ticks under the fixed 100 microsecond, −2000 microusd/share scenario."
            )
        ),
        "",
        "## Test latency and cost sensitivity",
        "",
        _markdown_table(_SIMULATION_HEADERS, _simulation_metric_rows(evidence)),
        "",
        "Turnover is absolute gross passive-plus-liquidation notional. Maximum drawdown uses the "
        "chronologically concatenated marked-equity path. Positive 100 ms adverse selection is "
        "unfavourable to the passive fill; coverage reports fills with an available future mark.",
        "",
        "## Assumptions, anomalies and limitations",
        "",
        *[f"- Assumption: {_markdown_text(item)}" for item in assumptions],
        *[f"- Limitation: {_markdown_text(item)}" for item in limitations],
    ]
    diagnostic_counts = cast(dict[str, int], evidence.simulation.diagnostics["counts"])
    lines.append(
        "- Queue/prediction diagnostics: "
        + (
            ", ".join(
                f"{_markdown_text(name)}={_number(count)}"
                for name, count in sorted(diagnostic_counts.items())
            )
            if diagnostic_counts
            else "none"
        )
        + "."
    )
    lines.extend(f"- Warning: {_markdown_text(item)}" for item in warnings)
    lines.extend(
        [
            "",
            "## Reproduction",
            "",
            "Run from the repository root after reproducing the authenticated replay, conversion, "
            "dataset and experiment parents shown in the predictive section/config snapshots.",
            "",
            "```console",
            "python -m itchlab_research simulate --config "
            + shlex.quote(f"{evidence.output_locator}/configs/simulation.json"),
            "python -m itchlab_research report --run-id "
            + shlex.quote(evidence.simulation.simulation_id),
            "```",
        ]
    )
    return "\n".join(lines)


def _simulation_html(evidence: SimulationReportEvidence) -> str:
    manifest = evidence.simulation.manifest
    assumptions = cast(list[str], manifest["assumptions"])
    limitations = cast(list[str], manifest["limitations"])
    warnings = cast(list[str], manifest["warnings"])
    selection = cast(dict[str, Any], manifest["selection"])
    model = cast(dict[str, Any] | None, selection.get("model"))
    weight = cast(dict[str, Any] | None, selection.get("signal_weight"))
    selected_weight = "unavailable" if weight is None else weight["selected"]
    selection_text = (
        "Baseline-only run; no signal model or weight was selected."
        if model is None
        else (
            f"Validation log loss selected {model['model_name']}; validation-day P&amp;L selected "
            f"signal weight {selected_weight} ticks under the fixed 100 microsecond, "
            "−2000 microusd/share scenario."
        )
    )
    list_items = "".join(
        f"<li><strong>Assumption:</strong> {html.escape(item)}</li>" for item in assumptions
    ) + "".join(
        f"<li><strong>Limitation:</strong> {html.escape(item)}</li>" for item in limitations
    )
    list_items += "".join(
        f"<li><strong>Warning:</strong> {html.escape(item)}</li>" for item in warnings
    )
    diagnostic_counts = cast(dict[str, int], evidence.simulation.diagnostics["counts"])
    diagnostic_text = (
        ", ".join(
            f"{html.escape(name)}={_number(count)}"
            for name, count in sorted(diagnostic_counts.items())
        )
        if diagnostic_counts
        else "none"
    )
    list_items += "<li><strong>Queue/prediction diagnostics:</strong> " + diagnostic_text + ".</li>"
    command = (
        "python -m itchlab_research simulate --config "
        f"{evidence.output_locator}/configs/simulation.json\n"
        "python -m itchlab_research report --run-id "
        f"{evidence.simulation.simulation_id}"
    )
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>Conservative simulation comparison</title>"
        "<style>body{font-family:system-ui,sans-serif;line-height:1.5;max-width:100rem;margin:auto;"
        "padding:1.5rem}.table-scroll{overflow-x:auto}table{border-collapse:collapse}th,td{border:"
        "1px solid #777;padding:.4rem;text-align:right}th:first-child,td:first-child{"
        "text-align:left}"
        "caption{text-align:left;font-weight:bold;margin:.5rem 0}code,pre{background:#f2f2f2}"
        "pre{padding:1rem;overflow:auto}</style></head><body><main>"
        "<h1>Conservative simulation comparison</h1>"
        f"<p>Simulation <code>{html.escape(evidence.simulation.simulation_id)}</code>. Historical "
        "research only; this is not a live-trading system, trading advice or evidence of "
        "profitability.</p><h2>Selection frozen before test</h2>"
        f"<p>{selection_text}</p><h2>Test latency and cost sensitivity</h2>"
        + _html_table(
            "Metrics by test scenario and strategy",
            _SIMULATION_HEADERS,
            _simulation_metric_rows(evidence),
            first_column_row_header=True,
        )
        + "<p>Turnover is absolute gross passive-plus-liquidation notional. Maximum drawdown uses "
        "the chronologically concatenated marked-equity path. Positive 100 ms adverse selection "
        "is unfavourable to the passive fill; coverage reports available future marks.</p>"
        "<h2>Assumptions, anomalies and limitations</h2><ul>"
        + list_items
        + "</ul><h2>Reproduction</h2><pre><code>"
        + html.escape(command)
        + "</code></pre></main></body></html>\n"
    )


def simulation_report_warnings(evidence: SimulationReportEvidence) -> tuple[str, ...]:
    """Return prominent deterministic warnings for a simulation report."""
    values = list(cast(list[str], evidence.simulation.manifest["warnings"]))
    if evidence.predictive is not None:
        values.extend(report_warnings(evidence.predictive))
    return tuple(dict.fromkeys(values))


def render_simulation_report_bundle(evidence: SimulationReportEvidence) -> dict[str, bytes]:
    """Render a combined predictive and conservative-simulation report bundle."""
    files_value: dict[str, bytes] = {}
    if evidence.predictive is not None:
        files_value.update(render_report_bundle(evidence.predictive))
    simulation_config = cast(dict[str, Any], evidence.simulation.manifest["config"])
    files_value["configs/simulation.json"] = (
        json.dumps(simulation_config, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False)
        + "\n"
    ).encode("utf-8")
    files_value["simulation-metrics.json"] = (
        json.dumps(
            evidence.simulation.metrics,
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")
    markdown = _simulation_markdown(evidence)
    html_value = _simulation_html(evidence)
    if evidence.predictive is not None:
        if "report.md" in files_value:
            predictive_markdown = files_value["report.md"].decode("utf-8")
            predictive_markdown = predictive_markdown.replace(
                "system, trading advice, an execution simulation or evidence of profitability.",
                "system, trading advice or evidence of profitability; execution results follow "
                "in a separate conservative section.",
            )
            predictive_markdown = predictive_markdown.replace(
                "- No execution simulation, fill model, latency/cost sensitivity, inventory or "
                "P&L is included in this predictive report.",
                "- Predictive metrics remain descriptive and are not evidence of executable "
                "profitability.",
            )
            markdown = predictive_markdown.rstrip() + "\n\n---\n\n" + markdown
        if "report.html" in files_value:
            predictive_html = files_value["report.html"].decode("utf-8")
            predictive_html = predictive_html.replace(
                "a live-trading system, trading advice, an execution simulation or evidence of "
                "profitability.",
                "a live-trading system, trading advice or evidence of profitability; execution "
                "results follow in a separate conservative section.",
            )
            predictive_html = predictive_html.replace("</main></body></html>\n", "")
            simulation_body = html_value.split("<main>", 1)[1]
            simulation_body = simulation_body.replace(
                "<h1>Conservative simulation comparison</h1>",
                "<h2>Conservative simulation comparison</h2>",
                1,
            )
            html_value = predictive_html + '<hr aria-hidden="true">' + simulation_body
    if evidence.output_format in {"markdown", "both"}:
        files_value["report.md"] = (markdown.rstrip() + "\n").encode("utf-8")
    else:
        files_value.pop("report.md", None)
    if evidence.output_format in {"html", "both"}:
        files_value["report.html"] = html_value.encode("utf-8")
    else:
        files_value.pop("report.html", None)
    return dict(sorted(files_value.items()))


__all__ = [
    "render_report_bundle",
    "render_simulation_report_bundle",
    "report_warnings",
    "simulation_report_warnings",
]
