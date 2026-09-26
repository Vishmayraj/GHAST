"""Dataset loading for the Stage 1 evaluation harness.

An AISObservation is one labeled AIS reading: Normal or Spoofed, with
whatever kinematic fields the source dataset provides. This is
intentionally observation-level rather than a strict per-vessel sequence,
because that's the shape of the ground-truth data actually available
(see the note in `load_gps_spoofing_mass` below) - a detector can still
group by `mmsi` into per-vessel sequences itself if it needs to.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from features.extract import COG_INDEX, HEADING_INDEX, SOG_INDEX
from features.inject import build_synthetic_dataset
from features.pipeline import load_training_windows


@dataclass(frozen=True)
class AISObservation:
    mmsi: str
    timestamp: str
    latitude: float
    longitude: float
    sog: float | None
    cog: float | None
    heading: float | None
    is_spoofed: bool
    # Optional fields present in some sources but not others - a detector
    # should not assume these are populated.
    speed_calc: float | None = None
    acceleration: float | None = None
    delta_heading: float | None = None
    prediction_error: float | None = None
    source: str = "unknown"


def _to_float(value: str) -> float | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def load_gps_spoofing_mass(path: str | Path) -> list[AISObservation]:
    """Load the "Synthetic GPS Dataset for AI-Based Spoofing Detection on
    Maritime Autonomous Surface Ships" (Agrebi, IEEE DataPort, DOI
    10.21227/9zcb-qb02) from its `gps_spoofing data.csv` file.

    IEEE DataPort itself gates the download behind an institutional
    subscription, but the author publishes the same file in the paper's
    companion GitHub repo, which is open:
    https://github.com/InesAg405/AI-Detection-Response-for-GPS-Spoofing-on-MASS
    See data/research_datasets/README.md for the exact fetch command.

    Note on shape: despite "track" in the paper's title, this file is
    6,351 independently-labeled observations across 4,892 MMSIs (~1.3
    observations/vessel on average) rather than long per-vessel
    trajectories - it reads as point-level ground truth, not sequence
    data. Fine for a precision/recall harness; something to know before
    trying to train a sequence model (ml/models/bilstm/) directly on it.
    """
    path = Path(path)
    observations: list[AISObservation] = []
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            label = (row.get("Label") or "").strip()
            if label not in ("Normal", "Spoofed"):
                continue  # skip any malformed/unlabeled row rather than guess
            observations.append(
                AISObservation(
                    mmsi=(row.get("MMSI") or "").strip(),
                    timestamp=(row.get("BaseDateTime") or "").strip(),
                    latitude=float(row["LAT"]),
                    longitude=float(row["LON"]),
                    sog=_to_float(row.get("SOG", "")),
                    cog=_to_float(row.get("COG", "")),
                    heading=_to_float(row.get("Heading", "")),
                    is_spoofed=(label == "Spoofed"),
                    speed_calc=_to_float(row.get("speed_calc", "")),
                    acceleration=_to_float(row.get("acceleration", "")),
                    delta_heading=_to_float(row.get("delta_heading", "")),
                    prediction_error=_to_float(row.get("prediction_error", "")),
                    source="gps_spoofing_mass",
                )
            )
    return observations


# Registry so the harness CLI (harness.py) can take a dataset name rather
# than requiring the caller to know which loader function to import.
DATASET_LOADERS = {
    "gps_spoofing_mass": load_gps_spoofing_mass,
    "injected_synthetic": None,  # assigned after its async loader definition
}

DEFAULT_DATASET_PATHS = {
    "gps_spoofing_mass": Path("../data/research_datasets/gps_spoofing_mass/gps_spoofing_data.csv"),
}


async def load_injected_synthetic(
    dsn: str, start, end, seed: int = 0
) -> list[AISObservation]:
    """Create labeled point observations from TimescaleDB trajectories on demand.

    Unlike file datasets this loader is async because it deliberately reads the
    project's live trajectory store. Prediction errors remain unset until model
    inference populates them in training/evaluation code.
    """
    injected_windows = build_synthetic_dataset(await load_training_windows(dsn, start, end), seed=seed)
    observations: list[AISObservation] = []
    for injected in injected_windows:
        for index, is_spoofed in enumerate(injected.is_spoofed):
            features = injected.window.features[index]
            latitude, longitude = injected.window.positions[index]
            observations.append(AISObservation(
                mmsi=str(injected.window.mmsi), timestamp=injected.window.timestamps[index].isoformat(),
                latitude=float(latitude), longitude=float(longitude), sog=float(features[SOG_INDEX]),
                cog=float(features[COG_INDEX]), heading=float(features[HEADING_INDEX]),
                is_spoofed=is_spoofed, source="injected_synthetic",
            ))
    return observations


DATASET_LOADERS["injected_synthetic"] = load_injected_synthetic
