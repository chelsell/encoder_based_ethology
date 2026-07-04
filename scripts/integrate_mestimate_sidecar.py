#!/usr/bin/env python3
import argparse
import gzip
import json
import math
import struct
from types import SimpleNamespace
import pathlib

try:
    import numpy as np
    import pandas as pd
except ModuleNotFoundError as exc:
    raise SystemExit(
        "Missing Python dependency. This script needs pandas and numpy."
    ) from exc


SCHEMA_NAME = "mestimate-derived-features"
SCHEMA_VERSION = "mv_features_v1"
EPS = 1e-12
VECTOR_COLUMNS = [
    "frame_index",
    "pts",
    "time_seconds",
    "vector_index",
    "source",
    "w",
    "h",
    "src_x",
    "src_y",
    "dst_x",
    "dst_y",
    "motion_x",
    "motion_y",
    "motion_scale",
    "flags",
    "dx_px",
    "dy_px",
    "magnitude_px",
]
VECTOR_BINARY_STRUCT = struct.Struct("<qqfiiIIhhhhiiIQiif")


def read_vectors(path):
    path = str(path)
    if not path.endswith(".bin.gz"):
        return pd.read_csv(path, compression="gzip")
    with gzip.open(path, "rb") as f:
        header = f.read(32)
        magic, version, endian, header_size, record_size, field_count, reserved = struct.unpack("<8s6I", header)
        if (
            magic != b"MSCVB1\x00\x00"
            or version != 1
            or endian != 0x01020304
            or header_size != 32
            or record_size != VECTOR_BINARY_STRUCT.size
            or field_count != len(VECTOR_COLUMNS)
            or reserved != 0
        ):
            raise ValueError(f"unsupported vector binary header in {path}")
        payload = f.read()
    if len(payload) % VECTOR_BINARY_STRUCT.size != 0:
        raise ValueError(f"truncated vector binary payload in {path}")
    return pd.DataFrame(VECTOR_BINARY_STRUCT.iter_unpack(payload), columns=VECTOR_COLUMNS)


def filter_vector_source(vectors, vector_source):
    if vector_source == "all":
        return vectors.copy()
    if vector_source == "past":
        return vectors[vectors["source"] < 0].copy()
    if vector_source == "future":
        return vectors[vectors["source"] > 0].copy()
    raise ValueError(f"unknown vector source filter: {vector_source}")


def consecutive_run_lengths(active):
    active = np.asarray(active, dtype=bool)
    run_lengths = np.zeros(len(active), dtype=np.int32)
    i = 0
    while i < len(active):
        if not active[i]:
            i += 1
            continue
        j = i + 1
        while j < len(active) and active[j]:
            j += 1
        run_lengths[i:j] = j - i
        i = j
    return run_lengths


def infer_spatial_extent(vectors):
    if len(vectors) == 0:
        return {"x0": 0.0, "x1": 1.0, "y0": 0.0, "y1": 1.0}
    half_w = vectors["w"].astype(float).fillna(1.0).clip(lower=1.0) / 2.0
    half_h = vectors["h"].astype(float).fillna(1.0).clip(lower=1.0) / 2.0
    x0 = float((vectors["dst_x"].astype(float) - half_w).min())
    x1 = float((vectors["dst_x"].astype(float) + half_w).max())
    y0 = float((vectors["dst_y"].astype(float) - half_h).min())
    y1 = float((vectors["dst_y"].astype(float) + half_h).max())
    if not math.isfinite(x0) or not math.isfinite(x1) or x1 <= x0:
        x0, x1 = 0.0, 1.0
    if not math.isfinite(y0) or not math.isfinite(y1) or y1 <= y0:
        y0, y1 = 0.0, 1.0
    return {"x0": x0, "x1": x1, "y0": y0, "y1": y1}


def add_spatial_bins(vectors, extent, grid_rows, grid_cols):
    vectors = vectors.copy()
    x = vectors["dst_x"].astype(float)
    y = vectors["dst_y"].astype(float)
    x_norm = ((x - extent["x0"]) / max(extent["x1"] - extent["x0"], EPS)).clip(0.0, 0.999999)
    y_norm = ((y - extent["y0"]) / max(extent["y1"] - extent["y0"], EPS)).clip(0.0, 0.999999)
    col = np.floor(x_norm * grid_cols).astype(int)
    row = np.floor(y_norm * grid_rows).astype(int)
    vectors["grid_col"] = col
    vectors["grid_row"] = row
    vectors["grid_bin"] = row * grid_cols + col
    vectors["dst_x_norm"] = x_norm
    vectors["dst_y_norm"] = y_norm
    return vectors


def spatial_frame_features(active_vectors, all_frame_index, grid_rows, grid_cols):
    n_bins = grid_rows * grid_cols
    empty = pd.DataFrame({"frame_index": all_frame_index})
    if len(active_vectors) == 0:
        empty["active_spatial_bin_fraction"] = 0.0
        empty["spatial_entropy"] = 0.0
        empty["max_bin_fraction"] = 0.0
        empty["motion_centroid_x_norm"] = np.nan
        empty["motion_centroid_y_norm"] = np.nan
        return empty

    bin_counts = (
        active_vectors.groupby(["frame_index", "grid_bin"])
        .size()
        .rename("bin_count")
        .reset_index()
    )
    rows = []
    for frame_index, g in bin_counts.groupby("frame_index", sort=False):
        counts = g["bin_count"].to_numpy(float)
        total = float(counts.sum())
        probs = counts / max(total, EPS)
        entropy = float(-(probs * np.log(probs + EPS)).sum() / max(math.log(n_bins), EPS))
        rows.append(
            {
                "frame_index": frame_index,
                "active_spatial_bin_fraction": float(len(g) / n_bins),
                "spatial_entropy": entropy,
                "max_bin_fraction": float(counts.max() / max(total, EPS)),
            }
        )
    spatial = pd.DataFrame(rows)
    centroids = (
        active_vectors.groupby("frame_index", as_index=False)
        .agg(
            motion_centroid_x_norm=("dst_x_norm", "mean"),
            motion_centroid_y_norm=("dst_y_norm", "mean"),
        )
    )
    spatial = spatial.merge(centroids, on="frame_index", how="left")
    return empty.merge(spatial, on="frame_index", how="left").fillna(
        {
            "active_spatial_bin_fraction": 0.0,
            "spatial_entropy": 0.0,
            "max_bin_fraction": 0.0,
        }
    )


def make_frame_features(
    frames,
    vectors,
    vector_source,
    active_vector_threshold,
    min_active_blocks_per_frame,
    min_active_run_frames,
    grid_rows,
    grid_cols,
    capped_active_vectors,
):
    frames = frames.copy().sort_values("frame_index").reset_index(drop=True)
    vectors = filter_vector_source(vectors, vector_source)
    extent = infer_spatial_extent(vectors)
    vectors = add_spatial_bins(vectors, extent, grid_rows, grid_cols)

    source_agg = (
        vectors.groupby("frame_index")
        .agg(
            analysis_n_vectors=("magnitude_px", "size"),
            analysis_motion_energy=("magnitude_px", "sum"),
            analysis_mean_magnitude_px=("magnitude_px", "mean"),
            analysis_p95_magnitude_px=("magnitude_px", lambda s: float(s.quantile(0.95))),
            analysis_sum_dx_px=("dx_px", "sum"),
            analysis_sum_dy_px=("dy_px", "sum"),
        )
        .reset_index()
    )
    out = frames.merge(source_agg, on="frame_index", how="left")
    fill_zero = [
        "analysis_n_vectors",
        "analysis_motion_energy",
        "analysis_mean_magnitude_px",
        "analysis_p95_magnitude_px",
        "analysis_sum_dx_px",
        "analysis_sum_dy_px",
    ]
    out[fill_zero] = out[fill_zero].fillna(0.0)
    out["analysis_n_vectors"] = out["analysis_n_vectors"].astype(int)

    active = vectors[vectors["magnitude_px"] > active_vector_threshold].copy()
    active_agg = (
        active.groupby("frame_index")
        .agg(
            mv_active_vectors=("magnitude_px", "size"),
            mv_active_motion_energy=("magnitude_px", "sum"),
            mv_active_mean_magnitude_px=("magnitude_px", "mean"),
        )
        .reset_index()
    )
    out = out.merge(active_agg, on="frame_index", how="left")
    out[["mv_active_vectors", "mv_active_motion_energy", "mv_active_mean_magnitude_px"]] = out[
        ["mv_active_vectors", "mv_active_motion_energy", "mv_active_mean_magnitude_px"]
    ].fillna(0.0)
    out["mv_active_vectors"] = out["mv_active_vectors"].astype(int)
    out["mvaf"] = np.where(
        out["analysis_n_vectors"] > 0,
        out["mv_active_vectors"] / out["analysis_n_vectors"],
        0.0,
    )
    out["mv_active_frame"] = out["mv_active_vectors"] >= min_active_blocks_per_frame
    out["mv_active_run_frames"] = consecutive_run_lengths(out["mv_active_frame"].to_numpy())
    out["mv_bout_frame"] = out["mv_active_run_frames"] >= min_active_run_frames
    out["mv_bout_active_vectors"] = np.where(out["mv_bout_frame"], out["mv_active_vectors"], 0)
    out["mv_bout_motion_energy"] = np.where(out["mv_bout_frame"], out["mv_active_motion_energy"], 0.0)
    out["mv_bout_fraction"] = np.where(
        out["analysis_n_vectors"] > 0,
        out["mv_bout_active_vectors"] / out["analysis_n_vectors"],
        0.0,
    )
    out["mv_capped_active_fraction"] = np.where(
        out["analysis_n_vectors"] > 0,
        np.minimum(out["mv_active_vectors"], capped_active_vectors) / out["analysis_n_vectors"],
        0.0,
    )
    out["mv_log_active_fraction"] = np.where(
        out["analysis_n_vectors"] > 0,
        np.log1p(out["mv_active_vectors"]) / np.log1p(out["analysis_n_vectors"]),
        0.0,
    )
    out["mv_inverse_saturation_fraction"] = out["mvaf"] * np.square((1.0 - out["mvaf"]).clip(lower=0.0))

    spatial = spatial_frame_features(active, out["frame_index"], grid_rows, grid_cols)
    out = out.merge(spatial, on="frame_index", how="left")
    out["vector_source_filter"] = vector_source
    return out, extent


def _active_mean(series, active):
    values = series[active]
    return float(values.mean()) if len(values) else 0.0


def aggregate_temporal_bins(frame_features, bin_seconds):
    work = frame_features.copy()
    work["bin_index"] = np.floor(work["time_seconds"].astype(float) / bin_seconds).astype(int)
    rows = []
    for bin_index, g in work.groupby("bin_index", sort=True):
        active = g["mv_active_frame"].astype(bool)
        bout = g["mv_bout_frame"].astype(bool)
        rows.append(
            {
                "bin_index": int(bin_index),
                "time_start_seconds": float(bin_index * bin_seconds),
                "time_end_seconds": float((bin_index + 1) * bin_seconds),
                "frame_index_start": int(g["frame_index"].min()),
                "frame_index_end": int(g["frame_index"].max()),
                "n_frames": int(len(g)),
                "analysis_n_vectors_median": float(g["analysis_n_vectors"].median()),
                "analysis_motion_energy_sum": float(g["analysis_motion_energy"].sum()),
                "analysis_motion_energy_mean": float(g["analysis_motion_energy"].mean()),
                "mv_active_vectors_sum": int(g["mv_active_vectors"].sum()),
                "mv_active_vectors_max": int(g["mv_active_vectors"].max()),
                "mvaf_mean": float(g["mvaf"].mean()),
                "mvaf_peak": float(g["mvaf"].max()),
                "mv_active_frame_fraction": float(active.mean()),
                "mv_active_frames": int(active.sum()),
                "mv_longest_active_run_frames": int(g["mv_active_run_frames"].max()),
                "mv_bout_fraction_mean": float(g["mv_bout_fraction"].mean()),
                "mv_bout_fraction_peak": float(g["mv_bout_fraction"].max()),
                "mv_bout_active_vectors_sum": int(g["mv_bout_active_vectors"].sum()),
                "mv_bout_frame_fraction": float(bout.mean()),
                "mv_capped_active_fraction_mean": float(g["mv_capped_active_fraction"].mean()),
                "mv_log_active_fraction_mean": float(g["mv_log_active_fraction"].mean()),
                "mv_inverse_saturation_fraction_mean": float(g["mv_inverse_saturation_fraction"].mean()),
                "coherence_mean": float(g["coherence"].mean()),
                "coherence_active_mean": _active_mean(g["coherence"], active),
                "resultant_magnitude_px_mean": float(g["resultant_magnitude_px"].mean()),
                "p95_magnitude_px_peak": float(g["analysis_p95_magnitude_px"].max()),
                "active_spatial_bin_fraction_mean": float(g["active_spatial_bin_fraction"].mean()),
                "active_spatial_bin_fraction_peak": float(g["active_spatial_bin_fraction"].max()),
                "spatial_entropy_mean": float(g["spatial_entropy"].mean()),
                "max_bin_fraction_mean": float(g["max_bin_fraction"].mean()),
                "motion_centroid_x_norm_mean": float(g["motion_centroid_x_norm"].mean(skipna=True)),
                "motion_centroid_y_norm_mean": float(g["motion_centroid_y_norm"].mean(skipna=True)),
                "motion_centroid_x_norm_sd": float(g["motion_centroid_x_norm"].std(skipna=True, ddof=0)),
                "motion_centroid_y_norm_sd": float(g["motion_centroid_y_norm"].std(skipna=True, ddof=0)),
            }
        )
    out = pd.DataFrame(rows)
    return out.fillna(
        {
            "motion_centroid_x_norm_mean": np.nan,
            "motion_centroid_y_norm_mean": np.nan,
            "motion_centroid_x_norm_sd": 0.0,
            "motion_centroid_y_norm_sd": 0.0,
        }
    )


def sidecar_stem(frames_path):
    name = pathlib.Path(frames_path).name
    return name.replace(".mestimate-v1.frames.csv.gz", "")


def write_metadata(path, args, frame_features, extent, source_metadata):
    payload = {
        "schema_name": SCHEMA_NAME,
        "schema_version": SCHEMA_VERSION,
        "source_frames": str(args.frames),
        "source_vectors": str(args.vectors),
        "source_metadata": str(args.metadata) if args.metadata else None,
        "vector_source_filter": args.vector_source,
        "active_vector_threshold_px": args.active_vector_threshold,
        "min_active_blocks_per_frame": args.min_active_blocks_per_frame,
        "min_active_run_frames": args.min_active_run_frames,
        "grid_rows": args.grid_rows,
        "grid_cols": args.grid_cols,
        "spatial_extent": extent,
        "bin_ms": args.bin_ms,
        "feature_definitions": {
            "mvaf": "fraction of selected-source blocks whose magnitude_px exceeds active_vector_threshold_px",
            "mv_bout_fraction": "mvaf after zeroing frames that are not in active runs meeting min_active_blocks_per_frame and min_active_run_frames",
            "mv_capped_active_fraction": "min(active block count, capped_active_vectors) divided by selected-source vector count",
            "mv_log_active_fraction": "log1p(active block count) divided by log1p(selected-source vector count)",
            "spatial_entropy": "normalized Shannon entropy of active-vector counts over the configured Cartesian grid",
        },
        "units": {
            "fine_time_series": "one row per source sidecar frame",
            "temporal_bins": "fixed-width bins based on time_seconds",
            "magnitudes": "pixels in sidecar motion-vector coordinate convention",
            "centroids": "0-1 normalized destination-vector position within inferred sidecar extent",
        },
        "row_counts": {
            "frame_features": int(len(frame_features)),
        },
        "source_sidecar": {
            "schema_name": source_metadata.get("schema_name"),
            "schema_version": source_metadata.get("schema_version"),
            "filtergraph": source_metadata.get("filtergraph"),
        },
        "warning": "Derived motion summaries are engineering features for review and modeling; they are not biological validation labels.",
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def integrate_sidecar(args):
    out = pathlib.Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    frames = pd.read_csv(args.frames, compression="gzip")
    vectors = read_vectors(args.vectors)
    source_metadata = {}
    if args.metadata:
        with open(args.metadata, "r", encoding="utf-8") as f:
            source_metadata = json.load(f)

    frame_features, extent = make_frame_features(
        frames,
        vectors,
        args.vector_source,
        args.active_vector_threshold,
        args.min_active_blocks_per_frame,
        args.min_active_run_frames,
        args.grid_rows,
        args.grid_cols,
        args.capped_active_vectors,
    )
    stem = sidecar_stem(args.frames)
    frame_path = out / f"{stem}.mv-features-v1.frames.csv.gz"
    frame_features.to_csv(frame_path, index=False, compression="gzip")
    bin_paths = []
    for bin_ms in args.bin_ms:
        bin_features = aggregate_temporal_bins(frame_features, bin_ms / 1000.0)
        bin_path = out / f"{stem}.mv-features-v1.bin-{bin_ms}ms.csv.gz"
        bin_features.to_csv(bin_path, index=False, compression="gzip")
        bin_paths.append(bin_path)
    metadata_path = out / f"{stem}.mv-features-v1.metadata.json"
    write_metadata(metadata_path, args, frame_features, extent, source_metadata)
    return {
        "stem": stem,
        "frame_path": frame_path,
        "bin_paths": bin_paths,
        "metadata_path": metadata_path,
        "frame_rows": int(len(frame_features)),
    }


def default_options(**overrides):
    values = {
        "bin_ms": [50, 100, 250],
        "vector_source": "past",
        "active_vector_threshold": 0.0,
        "min_active_blocks_per_frame": 2,
        "min_active_run_frames": 2,
        "grid_rows": 4,
        "grid_cols": 4,
        "capped_active_vectors": 6,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def main():
    parser = argparse.ArgumentParser(description="Integrate mestimate sidecar vectors into compact temporal MV features.")
    parser.add_argument("--frames", required=True, help="*.mestimate-v1.frames.csv.gz")
    parser.add_argument("--vectors", required=True, help="*.mestimate-v1.vectors.csv.gz or *.mestimate-v1.vectors.bin.gz")
    parser.add_argument("--metadata", default="", help="Optional *.mestimate-v1.metadata.json")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--bin-ms", type=int, nargs="+", default=[50, 100, 250])
    parser.add_argument("--vector-source", choices=["all", "past", "future"], default="past")
    parser.add_argument("--active-vector-threshold", type=float, default=0.0)
    parser.add_argument("--min-active-blocks-per-frame", type=int, default=2)
    parser.add_argument("--min-active-run-frames", type=int, default=2)
    parser.add_argument("--grid-rows", type=int, default=4)
    parser.add_argument("--grid-cols", type=int, default=4)
    parser.add_argument("--capped-active-vectors", type=int, default=6)
    args = parser.parse_args()

    result = integrate_sidecar(args)
    frame_path = result["frame_path"]
    print(f"wrote {frame_path}")


if __name__ == "__main__":
    main()
