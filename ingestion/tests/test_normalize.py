from datetime import timezone

import pytest

from normalizer.normalize import normalize_envelope, parse_time_utc

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
