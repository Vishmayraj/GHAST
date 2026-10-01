from datetime import datetime, timezone

import pytest

from models.bilstm.threshold import OPERATING_THRESHOLD
from orchestrator.state_machine import FlaggedAnomaly, form_hypothesis
from tools.incident_history import SAME_PATTERN_ELSEWHERE_QUERY, SAME_VESSEL_QUERY, find_similar_incidents

requires_operating_threshold = pytest.mark.skipif(OPERATING_THRESHOLD is None, reason="OPERATING_THRESHOLD is None")

NOW = datetime(2026, 9, 29, tzinfo=timezone.utc)


class _FakeConnection:
    def __init__(self, same_vessel, elsewhere) -> None:
        self.calls = []
        self._same_vessel = same_vessel
        self._elsewhere = elsewhere

    async def fetch(self, query, *args):
        self.calls.append((query, args))
        return self._same_vessel if query == SAME_VESSEL_QUERY else self._elsewhere


@pytest.mark.asyncio
async def test_same_vessel_and_same_pattern_elsewhere_are_separate_keys() -> None:
    connection = _FakeConnection([{"id": "a", "hypothesis": "benign"}], [{"id": "b", "mmsi": 999}])

    result = await find_similar_incidents(connection, 123, "prediction_error")

    assert result == {"same_vessel": [{"id": "a", "hypothesis": "benign"}], "same_pattern_elsewhere": [{"id": "b", "mmsi": 999}]}


@pytest.mark.asyncio
async def test_each_query_matches_only_its_own_kind_of_evidence() -> None:
    connection = _FakeConnection([], [])

    await find_similar_incidents(connection, 123, "prediction_error")

    assert connection.calls == [(SAME_VESSEL_QUERY, (123,)), (SAME_PATTERN_ELSEWHERE_QUERY, (123, "prediction_error"))]
    assert "anomaly_type =" not in SAME_VESSEL_QUERY and "mmsi = $1" in SAME_VESSEL_QUERY
    assert "mmsi <> $1" in SAME_PATTERN_ELSEWHERE_QUERY and "anomaly_type = $2" in SAME_PATTERN_ELSEWHERE_QUERY


def _anomaly() -> FlaggedAnomaly:
    return FlaggedAnomaly(1, NOW, (OPERATING_THRESHOLD or 0.0) * 10, "prediction_error", 10, 20,
                          detector_votes=frozenset({"prediction_error", "speed_jump"}))


def _evidence(history: dict) -> dict:
    return {"jamming_zones": {"matched": False}, "incident_history": history}


@requires_operating_threshold
def test_same_pattern_on_other_vessels_does_not_stop_a_first_incident_being_targeted() -> None:
    history = {"same_vessel": [], "same_pattern_elsewhere": [{"id": "b", "mmsi": 999}]}
    assert form_hypothesis(_anomaly(), _evidence(history)) == ("targeted_spoof", 0.72)


@requires_operating_threshold
def test_a_prior_incident_on_this_vessel_downgrades_to_equipment_fault() -> None:
    history = {"same_vessel": [{"id": "a"}], "same_pattern_elsewhere": []}
    assert form_hypothesis(_anomaly(), _evidence(history)) == ("equipment_fault", 0.55)
