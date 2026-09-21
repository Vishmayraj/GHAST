from pathlib import Path

from evaluation.datasets import load_gps_spoofing_mass

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "gps_spoofing_sample.csv"


def test_load_gps_spoofing_mass_reads_all_labeled_rows():
    observations = load_gps_spoofing_mass(FIXTURE_PATH)
    assert len(observations) == 25  # fixture is a fixed sample - see tests/fixtures/


def test_load_gps_spoofing_mass_parses_both_labels():
    observations = load_gps_spoofing_mass(FIXTURE_PATH)
    assert any(o.is_spoofed for o in observations)
    assert any(not o.is_spoofed for o in observations)


def test_load_gps_spoofing_mass_parses_core_fields():
    observations = load_gps_spoofing_mass(FIXTURE_PATH)
    first = observations[0]
    assert first.mmsi
    assert first.timestamp
    assert isinstance(first.latitude, float)
    assert isinstance(first.longitude, float)
    assert first.source == "gps_spoofing_mass"


def test_load_gps_spoofing_mass_handles_blank_optional_fields():
    # Some real rows have blank acceleration/speed_ma (first observation of
    # a vessel's sequence, nothing to compute a delta from yet) - these
    # must come through as None, not a parse error.
    observations = load_gps_spoofing_mass(FIXTURE_PATH)
    assert any(o.acceleration is None for o in observations)
