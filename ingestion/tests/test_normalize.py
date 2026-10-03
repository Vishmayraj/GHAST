from datetime import timezone

import pytest

import copy

from normalizer.normalize import drain_drop_counts, normalize_envelope, parse_time_utc

POSITION_REPORT_ENVELOPE = {
    "MessageType": "PositionReport",
    "MetaData": {
        "MMSI": 368207620,
        "ShipName": "EXAMPLE VESSEL",
        "Latitude": 25.7617,
        "Longitude": -80.1918,
        "time_utc": "2023-05-10 11:46:52.509865357 +0000 UTC",
    },
    "Message": {
        "PositionReport": {
            "MessageID": 1,
            "UserID": 368207620,
            "Sog": 12.4,
            "Cog": 86.7,
            "TrueHeading": 87,
            "RateOfTurn": 0,
            "NavigationalStatus": 0,
            "Raim": False,
            "PositionAccuracy": True,
            "Valid": True,
        }
    },
}

SHIP_STATIC_DATA_ENVELOPE = {
    "MessageType": "ShipStaticData",
    "MetaData": {
        "MMSI": 319156300,
        "ShipName": "PAPA",
        "latitude": 43.587181666666666,
        "longitude": 7.130946666666667,
        "time_utc": "2023-05-10 11:46:52.509865357 +0000 UTC",
    },
    "Message": {
        "ShipStaticData": {
            "AisVersion": 2,
            "CallSign": "ZGIJ3",
            "Destination": "ANTIBES  ",
            "Dte": False,
            "ImoNumber": 9794549,
            "MaximumStaticDraught": 3.3,
            "MessageID": 5,
            "Name": "PAPA",
            "Type": 37,
            "UserID": 319156300,
            "Valid": True,
        }
    },
}

SUBSCRIPTION_CONFIRMATION_ENVELOPE = {
    "MessageType": "SubscriptionConfirmation",
    "Message": {"CompressionEnabled": True},
}


def test_parse_time_utc_truncates_nanoseconds_to_microseconds():
    dt = parse_time_utc("2023-05-10 11:46:52.509865357 +0000 UTC")
    assert dt.tzinfo == timezone.utc
    assert dt.year == 2023 and dt.month == 5 and dt.day == 10
    assert dt.hour == 11 and dt.minute == 46 and dt.second == 52
    assert dt.microsecond == 509865


def test_parse_time_utc_without_fractional_seconds():
    dt = parse_time_utc("2023-05-10 11:46:52 +0000 UTC")
    assert dt.microsecond == 0


def test_parse_time_utc_rejects_unrecognized_format():
    with pytest.raises(ValueError):
        parse_time_utc("not a timestamp")


def test_normalize_position_report():
    record = normalize_envelope(POSITION_REPORT_ENVELOPE)
    assert record is not None
    assert record["kind"] == "position"
    assert record["mmsi"] == 368207620
    assert record["ship_name"] == "EXAMPLE VESSEL"
    assert record["latitude"] == pytest.approx(25.7617)
    assert record["longitude"] == pytest.approx(-80.1918)
    assert record["sog_knots"] == pytest.approx(12.4)
    assert record["cog_deg"] == pytest.approx(86.7)
    assert record["true_heading_deg"] == 87
    assert record["message_type"] == "PositionReport"


def test_normalize_ship_static_data():
    record = normalize_envelope(SHIP_STATIC_DATA_ENVELOPE)
    assert record is not None
    assert record["kind"] == "static"
    assert record["mmsi"] == 319156300
    assert record["call_sign"] == "ZGIJ3"
    assert record["imo_number"] == 9794549
    assert record["ship_type"] == 37
    # trailing whitespace in fixed-width AIS fields gets stripped
    assert record["destination"] == "ANTIBES"
    assert record["max_draught"] == pytest.approx(3.3)


def test_normalize_ignores_unsupported_message_types():
    assert normalize_envelope(SUBSCRIPTION_CONFIRMATION_ENVELOPE) is None


def test_normalize_ignores_envelope_missing_mmsi():
    envelope = {
        "MessageType": "PositionReport",
        "MetaData": {"Latitude": 1.0, "Longitude": 2.0, "time_utc": "2023-05-10 11:46:52 +0000 UTC"},
        "Message": {"PositionReport": {}},
    }
    assert normalize_envelope(envelope) is None


def test_normalize_falls_back_to_lowercase_metadata_keys():
    envelope = {
        "MessageType": "PositionReport",
        "MetaData": {
            "mmsi": 111222333,
            "shipname": "lowercase test",
            "latitude": 1.5,
            "longitude": 2.5,
            "time_utc": "2023-05-10 11:46:52 +0000 UTC",
        },
        "Message": {"PositionReport": {"Sog": 5.0}},
    }
    record = normalize_envelope(envelope)
    assert record is not None
    assert record["mmsi"] == 111222333
    assert record["ship_name"] == "lowercase test"
    assert record["latitude"] == pytest.approx(1.5)


def _position(**changes):
    """The position fixture with fields of MetaData or the message body replaced."""
    envelope = copy.deepcopy(POSITION_REPORT_ENVELOPE)
    for key, value in changes.items():
        target = envelope["MetaData"] if key in ("MMSI", "Latitude", "Longitude", "time_utc") else envelope["Message"]["PositionReport"]
        target[key] = value
    return envelope


@pytest.fixture(autouse=True)
def _clean_drop_counts():
    drain_drop_counts()
    yield
    drain_drop_counts()


@pytest.mark.parametrize("latitude,longitude", [(91.0, 181.0), (91.0, 10.0), (10.0, 181.0), (-90.5, 0.0), (0.0, -180.5), (float("nan"), 0.0), ("north", 0.0)])
def test_out_of_range_or_sentinel_coordinates_drop_the_message(latitude, longitude):
    assert normalize_envelope(_position(Latitude=latitude, Longitude=longitude)) is None
    assert drain_drop_counts() == {"invalid_coordinate": 1}


def test_edge_coordinates_are_kept():
    for lat, lon in ((90.0, 180.0), (-90.0, -180.0), (0.0, 0.0)):
        record = normalize_envelope(_position(Latitude=lat, Longitude=lon))
        assert (record["latitude"], record["longitude"]) == (lat, lon)


def test_message_the_sender_marked_invalid_is_dropped():
    assert normalize_envelope(_position(Valid=False)) is None
    assert drain_drop_counts() == {"invalid_flag": 1}
    assert normalize_envelope(_position(Valid=True)) is not None


def test_not_available_values_become_null_and_real_values_stay():
    record = normalize_envelope(_position(Sog=102.3, Cog=360.0, TrueHeading=511))
    assert (record["sog_knots"], record["cog_deg"], record["true_heading_deg"]) == (None, None, None)
    real = normalize_envelope(_position(Sog=102.2, Cog=359.9, TrueHeading=359))
    assert (real["sog_knots"], real["cog_deg"], real["true_heading_deg"]) == (102.2, 359.9, 359)
    at_rest = normalize_envelope(_position(Sog=0.0, Cog=0.0, TrueHeading=0))
    assert (at_rest["sog_knots"], at_rest["cog_deg"], at_rest["true_heading_deg"]) == (0.0, 0.0, 0)


def test_missing_and_junk_motion_fields_become_null_without_failing():
    record = normalize_envelope(_position(Sog=None, Cog="n/a", TrueHeading=-3))
    assert (record["sog_knots"], record["cog_deg"], record["true_heading_deg"]) == (None, None, None)


def test_a_malformed_timestamp_drops_one_message_and_does_not_raise():
    for bad in ("yesterday", "2023-05-10 11:46:52 +0100 CET", 12345):
        assert normalize_envelope(_position(time_utc=bad)) is None
    assert drain_drop_counts() == {"bad_timestamp": 3}
    assert normalize_envelope(POSITION_REPORT_ENVELOPE) is not None  # the next message is fine


def test_a_non_numeric_mmsi_drops_the_message():
    assert normalize_envelope(_position(MMSI="abc")) is None
    assert drain_drop_counts() == {"bad_mmsi": 1}


def test_drain_returns_counts_once_then_resets():
    normalize_envelope(_position(Latitude=91.0, Longitude=181.0))
    normalize_envelope(_position(Latitude=91.0, Longitude=181.0))
    assert drain_drop_counts() == {"invalid_coordinate": 2}
    assert drain_drop_counts() == {}


def test_static_messages_survive_a_bad_timestamp_the_same_way():
    envelope = copy.deepcopy(SHIP_STATIC_DATA_ENVELOPE)
    envelope["MetaData"]["time_utc"] = "garbage"
    assert normalize_envelope(envelope) is None
