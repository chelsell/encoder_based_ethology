#!/usr/bin/env python3
import argparse
import json
import math
import os
import pathlib
import subprocess

os.environ.setdefault("MPLCONFIGDIR", "/tmp/mestimate-sidecar-matplotlib")

try:
    import cv2
    import matplotlib.pyplot as plt
    import numpy as np
    import pandas as pd
except ModuleNotFoundError as exc:
    raise SystemExit(
        "Missing Python dependency. This script needs opencv-python, pandas, "
        "numpy, and matplotlib."
    ) from exc


DEFAULT_WELLS = ["A01", "A04", "C02", "D05"]
ROWS_24 = "ABCD"
ROWS_96 = "ABCDEFGH"


def s22_24well_rois(crop_size):
    # Empirical centers for 20260514_104647_S22.mkv, inspected from the full
    # 1664x1440 frame. Keep this explicit until a plate ROI file is available.
    x_centers = [138, 400, 662, 925, 1187, 1450]
    y_centers = [137, 400, 663, 927]
    rois = {}
    half = crop_size // 2
    for r, y in zip(ROWS_24, y_centers):
        for c, x in enumerate(x_centers, start=1):
            well = f"{r}{c:02d}"
            rois[well] = {
                "x0": int(x - half),
                "y0": int(y - half),
                "x1": int(x + half),
                "y1": int(y + half),
            }
    return rois


def s24_96well_rois():
    # Geometry matches the S24 1600x1068 ROI JSON examples in /media/ssd1.
    # The wells are rectangular crops around each well interior, not the full
    # visible outer plate border.
    rois = {}
    x0_start = 22
    y0_start = 16
    x_step = 131
    y_step = 132
    w = 112
    h = 109
    for r_i, r in enumerate(ROWS_96):
        for c in range(1, 13):
            x0 = x0_start + (c - 1) * x_step
            y0 = y0_start + r_i * y_step
            rois[f"{r}{c:02d}"] = {
                "x0": x0,
                "y0": y0,
                "x1": x0 + w,
                "y1": y0 + h,
            }
    return rois


def video_size(path):
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise RuntimeError(f"failed to open {path}")
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()
    return width, height


def choose_rois(input_video, layout, crop_size):
    if layout == "auto":
        width, height = video_size(input_video)
        if (width, height) == (1600, 1068):
            layout = "s24_96"
        elif (width, height) == (1664, 1440):
            layout = "s22_24"
        else:
            raise SystemExit(f"Cannot infer ROI layout for video size {width}x{height}; pass --layout.")
    if layout == "s24_96":
        return layout, s24_96well_rois()
    if layout == "s22_24":
        return layout, s22_24well_rois(crop_size)
    raise SystemExit(f"Unknown layout: {layout}")


def run(cmd):
    print("+", " ".join(str(c) for c in cmd), flush=True)
    subprocess.run(cmd, check=True)


def crop_video(ffmpeg, input_video, output_video, roi, start_seconds, duration_seconds):
    output_video.parent.mkdir(parents=True, exist_ok=True)
    w = roi["x1"] - roi["x0"]
    h = roi["y1"] - roi["y0"]
    vf = f"crop={w}:{h}:{roi['x0']}:{roi['y0']},format=gray"
    cmd = [
        ffmpeg,
        "-hide_banner",
        "-nostdin",
        "-v",
        "error",
        "-y",
        "-ss",
        str(start_seconds),
        "-i",
        str(input_video),
        "-t",
        str(duration_seconds),
        "-map",
        "0:v:0",
        "-an",
        "-vf",
        vf,
        "-c:v",
        "ffv1",
        "-level",
        "3",
        str(output_video),
    ]
    run(cmd)


def run_sidecar(crop_video_path, sidecar_dir, image):
    sidecar_dir.mkdir(parents=True, exist_ok=True)
    if image:
        cmd = [
            "env",
            f"IMAGE={image}",
            "scripts/run_sidecar_sif.sh",
            "--input",
            f"/work/{crop_video_path}",
            "--output-dir",
            f"/work/{sidecar_dir}",
            "--method",
            "epzs",
            "--mb-size",
            "16",
            "--search-param",
            "12",
            "--force",
        ]
    else:
        cmd = [
            "./build/mestimate-sidecar",
            "--input",
            str(crop_video_path),
            "--output-dir",
            str(sidecar_dir),
            "--method",
            "epzs",
            "--mb-size",
            "16",
            "--search-param",
            "12",
            "--force",
        ]
    run(cmd)


def filter_vector_source(vectors, vector_source):
    if vector_source == "all":
        return vectors.reset_index(drop=True)
    if vector_source == "past":
        return vectors[vectors["source"] < 0].copy().reset_index(drop=True)
    if vector_source == "future":
        return vectors[vectors["source"] > 0].copy().reset_index(drop=True)
    raise ValueError(f"unknown vector source filter: {vector_source}")


def compute_cd10(video_path, threshold=10):
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"failed to open {video_path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 100.0
    rows = []
    prev = None
    frame_index = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if frame.ndim == 3:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        else:
            gray = frame
        gray = gray.astype(np.int16)
        if prev is None:
            cd10 = 0
            cd10_sum_absdiff = 0
            sad = 0
        else:
            diff = np.abs(gray - prev)
            over = diff > threshold
            cd10 = int(np.count_nonzero(over))
            cd10_sum_absdiff = int(diff[over].sum())
            sad = int(diff.sum())
        rows.append(
            {
                "frame_index": frame_index,
                "time_seconds": frame_index / fps,
                "cd10": cd10,
                "cd10_sum_absdiff": cd10_sum_absdiff,
                "sum_absdiff": sad,
            }
        )
        prev = gray
        frame_index += 1
    cap.release()
    return pd.DataFrame(rows)


def block_bounds(row, width, height, padding):
    half_w = max(int(row.w) // 2, 1) + padding
    half_h = max(int(row.h) // 2, 1) + padding
    cx = int(round(row.dst_x))
    cy = int(round(row.dst_y))
    return (
        max(0, cx - half_w),
        max(0, cy - half_h),
        min(width, cx + half_w),
        min(height, cy + half_h),
    )


def add_local_cd_pixels(video_path, vectors, threshold, padding):
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"failed to open {video_path}")
    grouped = {int(k): v for k, v in vectors.groupby("frame_index", sort=False)}
    block_cd_pixels = np.zeros(len(vectors), dtype=np.int32)
    prev = None
    frame_index = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if frame.ndim == 3:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        else:
            gray = frame
        if prev is not None and frame_index in grouped:
            diff = cv2.absdiff(gray, prev)
            rows = grouped[frame_index]
            for row in rows.itertuples():
                x0, y0, x1, y1 = block_bounds(row, diff.shape[1], diff.shape[0], padding)
                if x1 <= x0 or y1 <= y0:
                    continue
                block_cd_pixels[row.Index] = int(np.count_nonzero(diff[y0:y1, x0:x1] > threshold))
        prev = gray.copy()
        frame_index += 1
    cap.release()
    vectors = vectors.copy()
    vectors["block_cd_pixels"] = block_cd_pixels
    return vectors


def stem_without_sidecar_suffix(path):
    name = pathlib.Path(path).name
    return name.replace(".mestimate-v1.frames.csv.gz", "")


def zscore(series):
    s = series.astype(float)
    sd = s.std(ddof=0)
    if not math.isfinite(sd) or sd == 0:
        return s * 0.0
    return (s - s.mean()) / sd


def consecutive_run_mask(active):
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


def compare_one_well(
    well,
    crop_video_path,
    sidecar_dir,
    output_dir,
    threshold,
    min_vector_magnitude,
    motion_energy_floor,
    active_vector_threshold,
    active_min_block_cd_pixels,
    block_padding,
    motion_metric,
    vector_source,
    min_active_blocks_per_frame,
    min_active_run_frames,
):
    crop_stem = crop_video_path.stem
    frames_path = sidecar_dir / f"{crop_stem}.mestimate-v1.frames.csv.gz"
    vectors_path = sidecar_dir / f"{crop_stem}.mestimate-v1.vectors.csv.gz"
    metadata_path = sidecar_dir / f"{crop_stem}.mestimate-v1.metadata.json"
    motion = pd.read_csv(frames_path, compression="gzip")
    motion["raw_motion_energy"] = motion["sum_magnitude_px"]
    vectors_for_active = pd.read_csv(
        vectors_path,
        compression="gzip",
        usecols=["frame_index", "source", "magnitude_px", "w", "h", "dst_x", "dst_y"],
    )
    vectors_for_active = filter_vector_source(vectors_for_active, vector_source)
    source_agg = (
        vectors_for_active.groupby("frame_index")
        .agg(
            analysis_n_vectors=("magnitude_px", "size"),
            analysis_motion_energy=("magnitude_px", "sum"),
        )
        .reset_index()
    )
    motion = motion.merge(source_agg, on="frame_index", how="left")
    motion["analysis_n_vectors"] = motion["analysis_n_vectors"].fillna(0).astype(int)
    motion["analysis_motion_energy"] = motion["analysis_motion_energy"].fillna(0.0)
    if active_min_block_cd_pixels > 0:
        vectors_for_active = add_local_cd_pixels(
            crop_video_path,
            vectors_for_active,
            threshold,
            block_padding,
        )
    active = vectors_for_active[vectors_for_active["magnitude_px"] > active_vector_threshold]
    if active_min_block_cd_pixels > 0:
        active = active[active["block_cd_pixels"] >= active_min_block_cd_pixels]
    active_counts = (
        active.groupby("frame_index")
        .size()
        .rename("mv_active_vectors")
        .reset_index()
    )
    motion = motion.merge(active_counts, on="frame_index", how="left")
    motion["mv_active_vectors"] = motion["mv_active_vectors"].fillna(0).astype(int)
    motion["mv_active_fraction"] = np.where(
        motion["analysis_n_vectors"] > 0,
        motion["mv_active_vectors"] / motion["analysis_n_vectors"],
        0.0,
    )
    motion["mv_active_frame"] = motion["mv_active_vectors"] >= min_active_blocks_per_frame
    motion["mv_active_run_frames"] = consecutive_run_mask(motion["mv_active_frame"].to_numpy())
    motion["mv_bout_frame"] = motion["mv_active_run_frames"] >= min_active_run_frames
    motion["mv_bout_active_vectors"] = np.where(
        motion["mv_bout_frame"],
        motion["mv_active_vectors"],
        0,
    )
    motion["mv_bout_fraction"] = np.where(
        motion["analysis_n_vectors"] > 0,
        motion["mv_bout_active_vectors"] / motion["analysis_n_vectors"],
        0.0,
    )
    if min_vector_magnitude > 0:
        vectors = pd.read_csv(
            vectors_path,
            compression="gzip",
            usecols=["frame_index", "source", "magnitude_px"],
        )
        vectors = filter_vector_source(vectors, vector_source)
        vectors = vectors[vectors["magnitude_px"] >= min_vector_magnitude]
        agg = (
            vectors.groupby("frame_index")
            .agg(
                thresholded_motion_energy=("magnitude_px", "sum"),
                thresholded_n_vectors=("magnitude_px", "size"),
            )
            .reset_index()
        )
        motion = motion.merge(agg, on="frame_index", how="left")
        motion["thresholded_motion_energy"] = motion["thresholded_motion_energy"].fillna(0.0)
        motion["thresholded_n_vectors"] = motion["thresholded_n_vectors"].fillna(0).astype(int)
    else:
        motion["thresholded_motion_energy"] = motion["analysis_motion_energy"]
        motion["thresholded_n_vectors"] = motion["analysis_n_vectors"]
    if motion_energy_floor > 0:
        motion["thresholded_motion_energy"] = (
            motion["thresholded_motion_energy"] - motion_energy_floor
        ).clip(lower=0)
    cd = compute_cd10(crop_video_path, threshold=threshold)

    merged = motion.merge(
        cd[["frame_index", "cd10", "cd10_sum_absdiff", "sum_absdiff"]],
        on="frame_index",
        how="inner",
    )
    merged["well"] = well
    merged["vector_source_filter"] = vector_source
    merged["motion_energy"] = merged["thresholded_motion_energy"]
    if motion_metric == "energy":
        merged["motion_metric"] = merged["motion_energy"]
        metric_label = "mestimate energy"
    elif motion_metric == "active_fraction":
        merged["motion_metric"] = merged["mv_active_fraction"]
        metric_label = "MV-active fraction"
    elif motion_metric == "active_vectors":
        merged["motion_metric"] = merged["mv_active_vectors"]
        metric_label = "MV-active vectors"
    elif motion_metric == "active_bout_fraction":
        merged["motion_metric"] = merged["mv_bout_fraction"]
        metric_label = "MV bout fraction"
    elif motion_metric == "active_bout_vectors":
        merged["motion_metric"] = merged["mv_bout_active_vectors"]
        metric_label = "MV bout vectors"
    elif motion_metric == "active_frame":
        merged["motion_metric"] = merged["mv_bout_frame"].astype(float)
        metric_label = "MV active frame"
    else:
        raise ValueError(f"unknown motion metric: {motion_metric}")
    merged["motion_energy_z"] = zscore(merged["motion_energy"])
    merged["motion_metric_z"] = zscore(merged["motion_metric"])
    merged["cd10_z"] = zscore(merged["cd10"])
    merged["sum_absdiff_z"] = zscore(merged["sum_absdiff"])
    merged["motion_metric_label"] = metric_label
    merged.to_csv(output_dir / f"{well}.comparison.csv", index=False)

    with open(metadata_path, "r", encoding="utf-8") as f:
        metadata = json.load(f)

    return merged, metadata


def make_plot(all_rows, output_png, title):
    wells = list(dict.fromkeys(all_rows["well"]))
    fig, axes = plt.subplots(len(wells), 1, figsize=(13, 2.8 * len(wells)), sharex=True)
    if len(wells) == 1:
        axes = [axes]
    for ax, well in zip(axes, wells):
        df = all_rows[all_rows["well"] == well]
        t = df["time_seconds"]
        metric_label = str(df["motion_metric_label"].iloc[0])
        ax.plot(t, df["motion_metric_z"], label=f"{metric_label} z", lw=1.0)
        ax.plot(t, df["cd10_z"], label="local cd(10) count z", lw=0.9, alpha=0.8)
        ax.set_ylabel(well)
        ax.grid(True, alpha=0.18)
        r = df["motion_metric"].corr(df["cd10"])
        ax.text(0.995, 0.86, f"Pearson r={r:.3f}", transform=ax.transAxes, ha="right")
    axes[-1].set_xlabel("seconds within cropped window")
    axes[0].legend(loc="upper left", ncol=2)
    fig.suptitle(title)
    fig.tight_layout()
    fig.savefig(output_png, dpi=160)
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--wells", nargs="+", default=DEFAULT_WELLS)
    parser.add_argument("--start-seconds", type=float, default=60.0)
    parser.add_argument("--duration-seconds", type=float, default=120.0)
    parser.add_argument("--crop-size", type=int, default=224)
    parser.add_argument("--layout", default="auto", choices=["auto", "s22_24", "s24_96"])
    parser.add_argument("--cd-threshold", type=int, default=10)
    parser.add_argument("--min-vector-magnitude", type=float, default=0.0)
    parser.add_argument("--motion-energy-floor", type=float, default=0.0)
    parser.add_argument("--active-vector-threshold", type=float, default=0.0)
    parser.add_argument("--active-min-block-cd-pixels", type=int, default=0)
    parser.add_argument(
        "--min-active-blocks-per-frame",
        type=int,
        default=1,
        help="Minimum supported active vectors required for a frame to enter an MV bout.",
    )
    parser.add_argument(
        "--min-active-run-frames",
        type=int,
        default=1,
        help="Minimum consecutive active frames required for MV bout metrics.",
    )
    parser.add_argument("--block-padding", type=int, default=0)
    parser.add_argument(
        "--vector-source",
        choices=["all", "past", "future"],
        default="all",
        help="Which AVMotionVector source values to analyze. 'past' keeps source < 0 and is the no-future-reference analogue.",
    )
    parser.add_argument(
        "--motion-metric",
        choices=[
            "energy",
            "active_fraction",
            "active_vectors",
            "active_bout_fraction",
            "active_bout_vectors",
            "active_frame",
        ],
        default="energy",
    )
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--image", default="", help="Optional Apptainer image/sandbox for sidecar extraction.")
    parser.add_argument("--skip-existing", action="store_true")
    args = parser.parse_args()

    input_video = pathlib.Path(args.input)
    output_dir = pathlib.Path(args.output_dir)
    crop_dir = output_dir / "crops"
    sidecar_root = output_dir / "sidecars"
    output_dir.mkdir(parents=True, exist_ok=True)

    layout, rois = choose_rois(input_video, args.layout, args.crop_size)
    selected = []
    for well in args.wells:
        if well not in rois:
            raise SystemExit(f"Unknown well for S22 24-well layout: {well}")
        selected.append(well)

    roi_manifest = {
        "input": str(input_video),
        "start_seconds": args.start_seconds,
        "duration_seconds": args.duration_seconds,
        "s22_crop_size_if_applicable": args.crop_size,
        "layout": layout,
        "roi_source": "hardcoded S22 centers or S24 96-well geometry; exact coordinates listed below",
        "cd_threshold": args.cd_threshold,
        "min_vector_magnitude": args.min_vector_magnitude,
        "motion_energy_floor": args.motion_energy_floor,
        "active_vector_threshold": args.active_vector_threshold,
        "active_min_block_cd_pixels": args.active_min_block_cd_pixels,
        "min_active_blocks_per_frame": args.min_active_blocks_per_frame,
        "min_active_run_frames": args.min_active_run_frames,
        "block_padding": args.block_padding,
        "vector_source": args.vector_source,
        "motion_metric": args.motion_metric,
        "wells": {well: rois[well] for well in selected},
        "cd10_definition": "count of pixels where abs(gray_t - gray_t_minus_1) > threshold",
        "cd10_sum_absdiff_definition": "sum of abs(gray_t - gray_t_minus_1) over pixels where absdiff > threshold",
        "mv_active_fraction_definition": "fraction of selected-source motion-vector blocks with magnitude_px > active_vector_threshold and optional local block cd-pixel support",
        "mv_bout_fraction_definition": "mv_active_fraction after zeroing frames that do not meet min_active_blocks_per_frame or belong to a run shorter than min_active_run_frames",
        "vector_source_definition": "all keeps all AVMotionVector source values; past keeps source < 0; future keeps source > 0",
    }
    with open(output_dir / "roi_manifest.json", "w", encoding="utf-8") as f:
        json.dump(roi_manifest, f, indent=2)

    merged_tables = []
    metadata_by_well = {}
    for well in selected:
        crop_path = crop_dir / f"{input_video.stem}_{well}.mkv"
        sidecar_dir = sidecar_root / well
        if not (args.skip_existing and crop_path.exists()):
            crop_video(args.ffmpeg, input_video, crop_path, rois[well], args.start_seconds, args.duration_seconds)
        expected_frames = sidecar_dir / f"{crop_path.stem}.mestimate-v1.frames.csv.gz"
        if not (args.skip_existing and expected_frames.exists()):
            run_sidecar(crop_path, sidecar_dir, args.image)
        merged, metadata = compare_one_well(
            well,
            crop_path,
            sidecar_dir,
            output_dir,
            args.cd_threshold,
            args.min_vector_magnitude,
            args.motion_energy_floor,
            args.active_vector_threshold,
            args.active_min_block_cd_pixels,
            args.block_padding,
            args.motion_metric,
            args.vector_source,
            args.min_active_blocks_per_frame,
            args.min_active_run_frames,
        )
        merged_tables.append(merged)
        metadata_by_well[well] = metadata

    all_rows = pd.concat(merged_tables, ignore_index=True)
    all_rows.to_csv(output_dir / "all_wells_comparison.csv", index=False)
    metrics = (
        all_rows.groupby("well")
        .apply(
            lambda g: pd.Series(
                {
                    "n_aligned_frames": len(g),
                    "motion_energy_median": g["motion_energy"].median(),
                    "raw_motion_energy_median": g["raw_motion_energy"].median(),
                    "analysis_motion_energy_median": g["analysis_motion_energy"].median(),
                    "analysis_n_vectors_median": g["analysis_n_vectors"].median(),
                    "mv_active_fraction_median": g["mv_active_fraction"].median(),
                    "mv_bout_fraction_median": g["mv_bout_fraction"].median(),
                    "mv_bout_frame_fraction": g["mv_bout_frame"].mean(),
                    "motion_metric_median": g["motion_metric"].median(),
                    "cd10_median": g["cd10"].median(),
                    "cd10_sum_absdiff_median": g["cd10_sum_absdiff"].median(),
                    "pearson_r": g["motion_metric"].corr(g["cd10"]),
                    "spearman_r": g["motion_metric"].corr(g["cd10"], method="spearman"),
                }
            )
        )
        .reset_index()
    )
    metrics.to_csv(output_dir / "per_well_metrics.csv", index=False)
    make_plot(
        all_rows,
        output_dir / "mestimate_vs_local_cd10.png",
        f"{input_video.name}: mestimate motion energy vs local cd(10)",
    )
    with open(output_dir / "sidecar_metadata_by_well.json", "w", encoding="utf-8") as f:
        json.dump(metadata_by_well, f, indent=2)
    print(metrics.to_string(index=False))
    print(f"Wrote {output_dir / 'mestimate_vs_local_cd10.png'}")


if __name__ == "__main__":
    main()
