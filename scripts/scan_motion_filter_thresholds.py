#!/usr/bin/env python3
import argparse
import os
import pathlib

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


def parse_floats(text):
    return [float(x) for x in text.split(",") if x.strip()]


def parse_ints(text):
    return [int(x) for x in text.split(",") if x.strip()]


def gray_frame(frame):
    if frame.ndim == 2:
        return frame
    return cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)


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


def compute_cd10(video_path, threshold):
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"failed to open {video_path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 100.0
    rows = []
    prev = None
    i = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        gray = gray_frame(frame).astype(np.int16)
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
                "frame_index": i,
                "time_seconds": i / fps,
                "cd10": cd10,
                "cd10_sum_absdiff": cd10_sum_absdiff,
                "sum_absdiff": sad,
            }
        )
        prev = gray
        i += 1
    cap.release()
    return pd.DataFrame(rows)


def add_local_diff_features(video_path, vectors, cd_threshold, padding):
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"failed to open {video_path}")
    grouped = {int(k): v for k, v in vectors.groupby("frame_index", sort=False)}
    mean_absdiff = np.zeros(len(vectors), dtype=np.float32)
    max_absdiff = np.zeros(len(vectors), dtype=np.float32)
    cd_pixels = np.zeros(len(vectors), dtype=np.int32)
    prev = None
    frame_index = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        gray = gray_frame(frame)
        if prev is not None and frame_index in grouped:
            diff = cv2.absdiff(gray, prev)
            rows = grouped[frame_index]
            for row in rows.itertuples():
                x0, y0, x1, y1 = block_bounds(row, diff.shape[1], diff.shape[0], padding)
                if x1 <= x0 or y1 <= y0:
                    continue
                block = diff[y0:y1, x0:x1]
                idx = row.Index
                mean_absdiff[idx] = float(block.mean())
                max_absdiff[idx] = float(block.max())
                cd_pixels[idx] = int(np.count_nonzero(block > cd_threshold))
        prev = gray.copy()
        frame_index += 1
    cap.release()
    vectors = vectors.copy()
    vectors["block_mean_absdiff"] = mean_absdiff
    vectors["block_max_absdiff"] = max_absdiff
    vectors["block_cd_pixels"] = cd_pixels
    return vectors


def scan(cd, vectors, frames, min_magnitudes, min_block_cd_pixels, energy_floors):
    base = cd[["frame_index", "cd10", "cd10_sum_absdiff"]].merge(
        frames[["frame_index", "sum_magnitude_px", "coherence", "n_vectors"]],
        on="frame_index",
        how="left",
    ).fillna({"sum_magnitude_px": 0.0, "coherence": 0.0, "n_vectors": 0})
    results = []
    for min_mag in min_magnitudes:
        for min_cd_pixels in min_block_cd_pixels:
            keep = vectors[
                (vectors["magnitude_px"] >= min_mag)
                & (vectors["block_cd_pixels"] >= min_cd_pixels)
            ]
            agg = keep.groupby("frame_index").agg(
                filtered_energy=("magnitude_px", "sum"),
                filtered_vectors=("magnitude_px", "size"),
                median_block_cd_pixels=("block_cd_pixels", "median"),
            ).reset_index()
            merged = base.merge(agg, on="frame_index", how="left").fillna(
                {"filtered_energy": 0.0, "filtered_vectors": 0, "median_block_cd_pixels": 0}
            )
            merged["mv_active_fraction"] = np.divide(
                merged["filtered_vectors"].to_numpy(dtype=float),
                merged["n_vectors"].to_numpy(dtype=float),
                out=np.zeros(len(merged), dtype=float),
                where=merged["n_vectors"].to_numpy(dtype=float) > 0,
            )
            for floor in energy_floors:
                energy = (merged["filtered_energy"] - floor).clip(lower=0)
                active_fraction = merged["mv_active_fraction"]
                cd10 = merged["cd10"]
                active = energy > 0
                cd_active = cd10 > 0
                false_cd0 = int((active & ~cd_active).sum())
                true_cdpos = int((active & cd_active).sum())
                missed_cdpos = int((~active & cd_active).sum())
                results.append(
                    {
                        "min_magnitude_px": min_mag,
                        "min_block_cd_pixels": min_cd_pixels,
                        "motion_energy_floor": floor,
                        "pearson_r": float(energy.corr(cd10)),
                        "spearman_r": float(energy.corr(cd10, method="spearman")),
                        "active_fraction_pearson_r": float(active_fraction.corr(cd10)),
                        "active_fraction_spearman_r": float(active_fraction.corr(cd10, method="spearman")),
                        "active_frames": int(active.sum()),
                        "cd10_active_frames": int(cd_active.sum()),
                        "cd10_sum_absdiff_active_frames": int((merged["cd10_sum_absdiff"] > 0).sum()),
                        "motion_active_cd10_zero_frames": false_cd0,
                        "motion_active_cd10_positive_frames": true_cdpos,
                        "missed_cd10_positive_frames": missed_cdpos,
                        "median_energy_when_cd10_positive": float(energy[cd_active].median()),
                        "p95_energy_when_cd10_zero": float(energy[~cd_active].quantile(0.95)),
                    }
                )
    return pd.DataFrame(results)


def plot_scan(results, output):
    subset = results[results["motion_energy_floor"] == results["motion_energy_floor"].min()]
    if subset.empty:
        return
    pivot = subset.pivot(index="min_block_cd_pixels", columns="min_magnitude_px", values="pearson_r")
    fig, ax = plt.subplots(figsize=(8, 5))
    im = ax.imshow(pivot.values, aspect="auto", origin="lower", cmap="viridis")
    ax.set_xticks(range(len(pivot.columns)))
    ax.set_xticklabels([str(x) for x in pivot.columns])
    ax.set_yticks(range(len(pivot.index)))
    ax.set_yticklabels([str(x) for x in pivot.index])
    ax.set_xlabel("min vector magnitude px")
    ax.set_ylabel("min local block cd pixels")
    ax.set_title("Pearson r at lowest energy floor")
    fig.colorbar(im, ax=ax, label="r")
    fig.tight_layout()
    fig.savefig(output, dpi=160)
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description="Scan post-hoc filters for mestimate sidecars against local cd(10).")
    parser.add_argument("--video", required=True, help="Cropped well video.")
    parser.add_argument("--vectors", required=True)
    parser.add_argument("--frames", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--cd-threshold", type=int, default=10)
    parser.add_argument("--block-padding", type=int, default=0)
    parser.add_argument("--min-magnitudes", default="0,1,1.5,2,3")
    parser.add_argument("--min-block-cd-pixels", default="0,1,2,4,8,16")
    parser.add_argument("--energy-floors", default="0,1,2,3,5,10")
    args = parser.parse_args()

    out = pathlib.Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    vectors = pd.read_csv(args.vectors, compression="gzip")
    frames = pd.read_csv(args.frames, compression="gzip")
    cd = compute_cd10(args.video, args.cd_threshold)
    vectors = add_local_diff_features(args.video, vectors, args.cd_threshold, args.block_padding)
    vectors.to_csv(out / "vectors_with_local_diff_features.csv.gz", index=False, compression="gzip")
    results = scan(
        cd,
        vectors,
        frames,
        parse_floats(args.min_magnitudes),
        parse_ints(args.min_block_cd_pixels),
        parse_floats(args.energy_floors),
    )
    results = results.sort_values(
        ["motion_active_cd10_zero_frames", "pearson_r"],
        ascending=[True, False],
    )
    results.to_csv(out / "filter_scan.tsv", sep="\t", index=False)
    plot_scan(results, out / "filter_scan_pearson_heatmap.png")
    print(results.head(20).to_string(index=False))
    print(f"Wrote {out / 'filter_scan.tsv'}")


if __name__ == "__main__":
    main()
