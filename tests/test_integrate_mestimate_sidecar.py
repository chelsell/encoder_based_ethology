import pathlib
import sys
import gzip
import struct

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

from integrate_mestimate_sidecar import (  # noqa: E402
    aggregate_temporal_bins,
    consecutive_run_lengths,
    make_frame_features,
    read_vectors,
)


def frame_fixture():
    return pd.DataFrame(
        {
            "frame_index": [0, 1, 2, 3, 4],
            "pts": [0, 1, 2, 3, 4],
            "time_seconds": [0.00, 0.01, 0.02, 0.03, 0.04],
            "n_vectors": [2, 2, 2, 2, 2],
            "mean_dx_px": [0, 0, 0, 0, 0],
            "mean_dy_px": [0, 0, 0, 0, 0],
            "mean_magnitude_px": [0, 0, 0, 0, 0],
            "median_magnitude_px": [0, 0, 0, 0, 0],
            "p90_magnitude_px": [0, 0, 0, 0, 0],
            "p95_magnitude_px": [0, 0, 0, 0, 0],
            "max_magnitude_px": [0, 0, 0, 0, 0],
            "sum_magnitude_px": [0, 0, 0, 0, 0],
            "resultant_magnitude_px": [0, 0, 0, 0, 0],
            "coherence": [0.0, 0.2, 0.4, 0.6, 0.8],
        }
    )


def vector_fixture():
    rows = []
    magnitudes_by_frame = {
        0: [0.0, 0.0],
        1: [1.0, 0.0],
        2: [1.0, 1.0],
        3: [1.0, 0.0],
        4: [0.0, 0.0],
    }
    for frame_index, magnitudes in magnitudes_by_frame.items():
        for vector_index, magnitude in enumerate(magnitudes):
            rows.append(
                {
                    "frame_index": frame_index,
                    "source": -1,
                    "vector_index": vector_index,
                    "w": 16,
                    "h": 16,
                    "dst_x": 8 + vector_index * 16,
                    "dst_y": 8 + frame_index * 16,
                    "dx_px": magnitude,
                    "dy_px": 0.0,
                    "magnitude_px": magnitude,
                }
            )
    rows.append(
        {
            "frame_index": 2,
            "source": 1,
            "vector_index": 99,
            "w": 16,
            "h": 16,
            "dst_x": 64,
            "dst_y": 64,
            "dx_px": 100.0,
            "dy_px": 0.0,
            "magnitude_px": 100.0,
        }
    )
    return pd.DataFrame(rows)


def test_consecutive_run_lengths_assigns_whole_run_length():
    observed = consecutive_run_lengths([False, True, True, False, True])
    np.testing.assert_array_equal(observed, np.array([0, 2, 2, 0, 1], dtype=np.int32))


def test_make_frame_features_filters_future_vectors_and_marks_bouts():
    features, _extent = make_frame_features(
        frame_fixture(),
        vector_fixture(),
        vector_source="past",
        active_vector_threshold=0.0,
        min_active_blocks_per_frame=1,
        min_active_run_frames=2,
        grid_rows=2,
        grid_cols=2,
        capped_active_vectors=6,
    )

    assert features.loc[features["frame_index"] == 2, "analysis_n_vectors"].item() == 2
    assert features["mv_active_vectors"].tolist() == [0, 1, 2, 1, 0]
    assert features["mv_active_run_frames"].tolist() == [0, 3, 3, 3, 0]
    assert features["mv_bout_active_vectors"].tolist() == [0, 1, 2, 1, 0]
    assert features.loc[features["frame_index"] == 2, "mvaf"].item() == 1.0


def test_aggregate_temporal_bins_uses_frame_rows_as_observations():
    features, _extent = make_frame_features(
        frame_fixture(),
        vector_fixture(),
        vector_source="past",
        active_vector_threshold=0.0,
        min_active_blocks_per_frame=1,
        min_active_run_frames=2,
        grid_rows=2,
        grid_cols=2,
        capped_active_vectors=1,
    )
    bins = aggregate_temporal_bins(features, bin_seconds=0.02)

    first = bins[bins["bin_index"] == 0].iloc[0]
    second = bins[bins["bin_index"] == 1].iloc[0]
    assert first["n_frames"] == 2
    assert first["frame_index_start"] == 0
    assert first["frame_index_end"] == 1
    assert first["mv_active_vectors_sum"] == 1
    assert first["mv_active_frame_fraction"] == 0.5
    assert second["mv_active_vectors_sum"] == 3
    assert second["mvaf_peak"] == 1.0


def test_read_vectors_accepts_binary_vector_stream(tmp_path):
    path = tmp_path / "vectors.bin.gz"
    header = struct.pack("<8s6I", b"MSCVB1\x00\x00", 1, 0x01020304, 32, 76, 18, 0)
    record = struct.pack(
        "<qqfiiIIhhhhiiIQiif",
        7,
        70,
        0.7,
        3,
        -1,
        16,
        16,
        1,
        2,
        4,
        6,
        12,
        16,
        4,
        0,
        3,
        4,
        5.0,
    )
    with gzip.open(path, "wb") as f:
        f.write(header)
        f.write(record)

    vectors = read_vectors(path)

    assert vectors.loc[0, "frame_index"] == 7
    assert vectors.loc[0, "source"] == -1
    assert vectors.loc[0, "dx_px"] == 3
    assert vectors.loc[0, "dy_px"] == 4
    assert vectors.loc[0, "magnitude_px"] == 5.0
