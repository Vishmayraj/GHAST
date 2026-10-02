from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from models.clustering.fleet_cluster import (
    NOISE, cluster_containing, cluster_reports, dbscan, haversine_matrix_km,
)

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


def rep(mmsi, lat, lon, minutes=0):
    return {"mmsi": mmsi, "time": T0 + timedelta(minutes=minutes), "lat": lat, "lon": lon}


def test_haversine_known_distances_and_antimeridian():
    d = haversine_matrix_km(np.array([0.0, 0.0]), np.array([0.0, 1.0]))
    assert d[0, 1] == pytest.approx(111.195, abs=0.05)  # one degree of longitude at the equator
    across = haversine_matrix_km(np.array([0.0, 0.0]), np.array([179.9, -179.9]))
    assert across[0, 1] == pytest.approx(22.2, abs=0.1)  # not 359.8 degrees apart


def test_dbscan_two_groups_and_noise():
    pts = np.array([[0, 0], [1, 0], [0, 1], [10, 10], [11, 10], [10, 11], [50, 50]], float)
    dist = np.linalg.norm(pts[:, None] - pts[None, :], axis=2)
    labels = dbscan(dist, eps=1.5, min_samples=3)
    assert labels[0] == labels[1] == labels[2] != NOISE
    assert labels[3] == labels[4] == labels[5] != NOISE and labels[0] != labels[3]
    assert labels[6] == NOISE


def test_dbscan_border_point_joins_but_does_not_extend_the_cluster():
    # three core points in a row, a border point within eps of the last one, a far point beyond it
    xs = np.array([0, 1, 2, 3.4, 5.2], float)
    dist = np.abs(xs[:, None] - xs[None, :])
    labels = dbscan(dist, eps=1.5, min_samples=3)
    assert labels[:4] == [0, 0, 0, 0] and labels[4] == NOISE


def test_three_vessels_close_together_form_one_cluster():
    clusters = cluster_reports([rep(1, 51.0, 4.0), rep(2, 51.05, 4.02), rep(3, 51.1, 4.0)], bucket_minutes=None)
    assert len(clusters) == 1 and clusters[0]["vessels"] == [1, 2, 3] and clusters[0]["n_vessels"] == 3
    assert 0 < clusters[0]["radius_km"] < 10


def test_one_vessel_flagging_many_times_is_not_a_cluster():
    many = [rep(1, 51.0, 4.0, minutes=i) for i in range(10)] + [rep(2, 51.01, 4.0)]
    assert cluster_reports(many, bucket_minutes=30) == []


def test_two_vessels_are_below_the_default_minimum():
    assert cluster_reports([rep(1, 51.0, 4.0), rep(2, 51.01, 4.0)], bucket_minutes=None) == []
    assert len(cluster_reports([rep(1, 51.0, 4.0), rep(2, 51.01, 4.0)], min_vessels=2, bucket_minutes=None)) == 1


def test_vessels_far_apart_do_not_cluster():
    far = [rep(1, 51.0, 4.0), rep(2, 55.0, 4.0), rep(3, 60.0, 10.0)]
    assert cluster_reports(far, bucket_minutes=None) == []


def test_each_vessel_counts_once_per_bucket_using_its_latest_report():
    reports = [rep(1, 51.0, 4.0, 0), rep(1, 51.0, 4.0, 5), rep(2, 51.02, 4.0, 1), rep(3, 51.04, 4.0, 2)]
    (cluster,) = cluster_reports(reports, bucket_minutes=30)
    assert cluster["n_vessels"] == 3


def test_same_place_in_different_time_buckets_is_not_one_cluster():
    spread = [rep(1, 51.0, 4.0, 0), rep(2, 51.02, 4.0, 40), rep(3, 51.04, 4.0, 80)]
    assert cluster_reports(spread, bucket_minutes=30) == []
    assert len(cluster_reports(spread, bucket_minutes=None)) == 1


def test_two_separate_clusters_in_one_bucket():
    group_a = [rep(i, 51.0 + i * 0.01, 4.0) for i in (1, 2, 3)]
    group_b = [rep(i, -33.0 + i * 0.01, 18.0) for i in (11, 12, 13)]
    clusters = cluster_reports(group_a + group_b, bucket_minutes=None)
    assert sorted(c["vessels"] for c in clusters) == [[1, 2, 3], [11, 12, 13]]


def test_cluster_across_the_antimeridian_is_one_cluster():
    reports = [rep(1, 0.0, 179.95), rep(2, 0.0, -179.95), rep(3, 0.05, 180.0)]
    (cluster,) = cluster_reports(reports, bucket_minutes=None)
    assert cluster["n_vessels"] == 3


def test_cluster_containing_answers_isolated_versus_clustered():
    nearby = [rep(2, 51.02, 4.0), rep(3, 51.04, 4.0)]
    assert cluster_containing(1, [rep(1, 51.0, 4.0)] + nearby)["n_vessels"] == 3
    assert cluster_containing(1, [rep(1, 51.0, 4.0), rep(9, 58.0, 20.0), rep(8, -20.0, 40.0)]) is None
    assert cluster_containing(99, [rep(1, 51.0, 4.0)] + nearby) is None


def test_bad_parameters_are_refused():
    with pytest.raises(ValueError):
        cluster_reports([], min_vessels=0)
    with pytest.raises(ValueError):
        cluster_reports([], eps_km=0)
    assert cluster_reports([]) == []
