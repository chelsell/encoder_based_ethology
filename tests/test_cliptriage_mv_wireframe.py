import pathlib
import sys

import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

from cliptriage_mv_wireframe import resolve_asset_path, summarize_bins  # noqa: E402


def test_resolve_asset_path_keeps_absolute_paths(tmp_path):
    absolute = tmp_path / "clip.mp4"
    observed = resolve_asset_path(absolute, "/some/root")
    assert observed == absolute


def test_resolve_asset_path_resolves_relative_paths_against_clip_root(tmp_path):
    observed = resolve_asset_path("data/rendered/clip.mp4", tmp_path)
    assert observed == tmp_path / "data/rendered/clip.mp4"


def test_summarize_bins_uses_mean_and_max_for_feature_columns(tmp_path):
    path = tmp_path / "bins.csv"
    pd.DataFrame(
        [
            {
                "mvaf_mean": 0.1,
                "mvaf_peak": 0.2,
                "mv_bout_fraction_mean": 0.0,
                "mv_bout_fraction_peak": 0.0,
                "mv_active_frame_fraction": 0.5,
                "analysis_motion_energy_sum": 10.0,
                "p95_magnitude_px_peak": 1.0,
                "spatial_entropy_mean": 0.4,
                "active_spatial_bin_fraction_peak": 0.2,
            },
            {
                "mvaf_mean": 0.3,
                "mvaf_peak": 0.5,
                "mv_bout_fraction_mean": 0.2,
                "mv_bout_fraction_peak": 0.4,
                "mv_active_frame_fraction": 1.0,
                "analysis_motion_energy_sum": 30.0,
                "p95_magnitude_px_peak": 2.0,
                "spatial_entropy_mean": 0.8,
                "active_spatial_bin_fraction_peak": 0.6,
            },
        ]
    ).to_csv(path, index=False)

    summary = summarize_bins(path)

    assert summary["n_feature_bins"] == 2
    assert summary["mvaf_mean_mean"] == 0.2
    assert summary["mvaf_peak_max"] == 0.5
    assert summary["analysis_motion_energy_sum_mean"] == 20.0
