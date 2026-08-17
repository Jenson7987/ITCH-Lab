"""Immutable public and internal models for predictive research reporting."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from itchlab_research.models import AuthenticatedExperiment
from itchlab_research.simulation.models import AuthenticatedSimulation

ReportFormat = Literal["markdown", "html", "both"]
ManifestKind = Literal["conversion", "replay"]


@dataclass(frozen=True, slots=True)
class AuthenticatedLineageManifest:
    """One hash-authenticated conversion or replay manifest used for presentation."""

    kind: ManifestKind
    run_id: str
    locator: str
    sha256: str
    document: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ReportEvidence:
    """All authenticated, path-safe evidence needed by deterministic renderers."""

    experiment: AuthenticatedExperiment
    conversions: tuple[AuthenticatedLineageManifest, ...]
    replays: tuple[AuthenticatedLineageManifest, ...]
    output_format: ReportFormat
    output_locator: str


@dataclass(frozen=True, slots=True)
class ReportResult:
    """A completed or safely reused predictive research-report bundle."""

    experiment_id: str
    status: Literal["completed"]
    output_directory: Path
    output_format: ReportFormat
    artefacts: tuple[str, ...]
    warnings: tuple[str, ...]
    reused: bool


@dataclass(frozen=True, slots=True)
class SimulationReportEvidence:
    """Authenticated simulation evidence plus optional upstream predictive evidence."""

    simulation: AuthenticatedSimulation
    predictive: ReportEvidence | None
    output_format: ReportFormat
    output_locator: str


__all__ = [
    "AuthenticatedLineageManifest",
    "ManifestKind",
    "ReportEvidence",
    "ReportFormat",
    "ReportResult",
    "SimulationReportEvidence",
]
