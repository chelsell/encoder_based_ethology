import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "configs" / "qc" / "clipsift_qc_bridge_v0_1.json"


def test_clipsift_qc_bridge_contract_has_required_source_decode_channels():
    config = json.loads(CONFIG.read_text(encoding="utf-8"))

    assert config["feature_set"] == "clipsift_qc_bridge_v0_1"
    assert config["image_dynamics"]["lags_frames"] == [1, 3, 10]
    assert config["motion_vectors"]["preaggregate_spatial_grid"] == [4, 4]
    assert config["decision_semantics"]["automatic_rejection"] is False

    well = set(config["transcode_trace_features"]["well"])
    plate = set(config["transcode_trace_features"]["plate_context"])
    assert {
        "imgdiff_l1_changed_fraction",
        "imgdiff_l3_changed_fraction",
        "imgdiff_l10_changed_fraction",
        "frame_mean_intensity_signed_delta",
        "mv_active_fraction",
        "motion_spatial_entropy",
    } <= well
    assert {
        "reference_imgdiff_changed_fraction",
        "plate_registration_shift_px",
        "plate_registration_response",
        "plate_well_active_fraction",
        "plate_activity_dispersion",
    } <= plate


def test_qc_contract_keeps_window_decisions_derived():
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    derived = set(config["derived_after_transcode"]["features"])

    assert "duplicate_like_frame_fraction" in derived
    assert "corrupt_like_frame_fraction" in derived
    assert config["derived_after_transcode"]["ratio_floor_policy"] == "versioned_robust_floor_required"
