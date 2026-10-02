import json

import pytest

import load_jamming_zones as loader
from load_jamming_zones import ZoneFileError, parse_zones

RING = [[10.0, 50.0], [11.0, 50.0], [11.0, 51.0], [10.0, 51.0], [10.0, 50.0]]


def feature(**props):
    base = {"name": "Test zone", "source_name": "A public notice", "source_url": "https://example.org/notice",
            "source_date": "2026-01-15", "confidence": 0.6, "first_seen": "2026-01-01", "last_seen": None, "notes": "drawn conservatively"}
    base.update(props)
    return {"type": "Feature", "properties": base, "geometry": {"type": "Polygon", "coordinates": [RING]}}


def collection(*features):
    return {"type": "FeatureCollection", "features": list(features)}


def test_a_valid_file_parses_with_dates_and_nullable_fields():
    (zone,) = parse_zones(collection(feature()))
    assert zone.name == "Test zone" and zone.confidence == 0.6
    assert str(zone.first_seen) == "2026-01-01" and zone.last_seen is None
    assert zone.geometry["type"] == "Polygon"


def test_multipolygon_is_accepted():
    f = feature()
    f["geometry"] = {"type": "MultiPolygon", "coordinates": [[RING], [RING]]}
    assert parse_zones(collection(f))[0].geometry["type"] == "MultiPolygon"


@pytest.mark.parametrize("missing", ["name", "source_name", "source_url", "source_date", "confidence"])
def test_required_properties_are_required(missing):
    f = feature()
    del f["properties"][missing]
    with pytest.raises(ZoneFileError, match=missing):
        parse_zones(collection(f))


def test_a_zone_without_a_real_source_url_is_refused():
    for bad in ("", "   ", "not a url", "ftp://example.org/x"):
        with pytest.raises(ZoneFileError, match="source_url"):
            parse_zones(collection(feature(source_url=bad)))


@pytest.mark.parametrize("confidence", [-0.1, 1.5, "high", True, None])
def test_confidence_must_be_a_number_between_zero_and_one(confidence):
    with pytest.raises(ZoneFileError, match="confidence"):
        parse_zones(collection(feature(confidence=confidence)))


@pytest.mark.parametrize("field,value", [("source_date", "15/01/2026"), ("first_seen", "2026-13-40"), ("last_seen", 20260101)])
def test_dates_must_be_real_iso_dates(field, value):
    with pytest.raises(ZoneFileError, match=field):
        parse_zones(collection(feature(**{field: value})))


def test_last_seen_cannot_precede_first_seen():
    with pytest.raises(ZoneFileError, match="before"):
        parse_zones(collection(feature(first_seen="2026-02-01", last_seen="2026-01-01")))


def test_ring_must_be_closed_and_long_enough():
    f = feature()
    f["geometry"]["coordinates"] = [RING[:-1]]
    with pytest.raises(ZoneFileError, match="closed"):
        parse_zones(collection(f))
    f["geometry"]["coordinates"] = [RING[:3]]
    with pytest.raises(ZoneFileError, match="4 positions"):
        parse_zones(collection(f))


def test_swapped_latitude_longitude_is_caught():
    f = feature()
    f["geometry"]["coordinates"] = [[[100.0, 200.0], [101.0, 200.0], [101.0, 201.0], [100.0, 200.0]]]
    with pytest.raises(ZoneFileError, match="longitude, latitude"):
        parse_zones(collection(f))


def test_other_geometry_types_and_shapes_are_refused():
    f = feature()
    f["geometry"] = {"type": "Point", "coordinates": [1, 2]}
    with pytest.raises(ZoneFileError, match="Polygon or MultiPolygon"):
        parse_zones(collection(f))
    with pytest.raises(ZoneFileError, match="FeatureCollection"):
        parse_zones({"type": "Feature"})
    with pytest.raises(ZoneFileError, match="no features"):
        parse_zones(collection())


def test_duplicate_name_and_source_in_one_file_is_refused_but_other_sources_are_fine():
    with pytest.raises(ZoneFileError, match="twice"):
        parse_zones(collection(feature(), feature()))
    assert len(parse_zones(collection(feature(), feature(source_url="https://example.org/other")))) == 2


class FakeConnection:
    def __init__(self):
        self.rows, self.calls = {}, []

    async def fetchrow(self, query, *args):
        self.calls.append((query, args))
        key = (args[0], args[7])  # name, source_url
        if key in self.rows and self.rows[key] == "owned_elsewhere":
            return None
        inserted = key not in self.rows
        self.rows[key] = args
        return {"inserted": inserted}


@pytest.mark.asyncio
async def test_upsert_inserts_then_updates_and_is_idempotent():
    zones = parse_zones(collection(feature(), feature(name="Second", source_url="https://example.org/2")))
    connection = FakeConnection()
    assert await loader.upsert_zones(connection, zones) == (2, 0, 0)
    assert await loader.upsert_zones(connection, zones) == (0, 2, 0)
    query, args = connection.calls[0]
    assert "ON CONFLICT (name, source_url)" in query and "'manual'" in query and "ST_Multi" in query
    assert json.loads(args[1])["type"] == "Polygon" and args[7] == "https://example.org/notice"


@pytest.mark.asyncio
async def test_rows_not_owned_by_the_loader_are_skipped_not_overwritten():
    zones = parse_zones(collection(feature()))
    connection = FakeConnection()
    connection.rows[("Test zone", "https://example.org/notice")] = "owned_elsewhere"
    assert await loader.upsert_zones(connection, zones) == (0, 0, 1)
    assert "WHERE jamming_zones.source = 'manual'" in connection.calls[0][0]


def test_dry_run_validates_prints_and_never_touches_a_database(tmp_path, capsys, monkeypatch):
    path = tmp_path / "zones.geojson"
    path.write_text(json.dumps(collection(feature())))
    monkeypatch.setattr(loader, "_run", lambda *a: (_ for _ in ()).throw(AssertionError("must not connect")))
    assert loader.main([str(path), "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "1 zone valid" in out and "dry run: nothing written" in out


def test_bad_file_exits_nonzero_with_the_reason(tmp_path, capsys):
    bad = collection(feature())
    bad["features"][0]["properties"]["source_url"] = ""
    path = tmp_path / "zones.geojson"
    path.write_text(json.dumps(bad))
    assert loader.main([str(path), "--dry-run"]) == 1
    assert "source_url" in capsys.readouterr().err
    assert loader.main([str(tmp_path / "missing.geojson"), "--dry-run"]) == 1
    (tmp_path / "broken.geojson").write_text("{nope")
    assert loader.main([str(tmp_path / "broken.geojson"), "--dry-run"]) == 1
