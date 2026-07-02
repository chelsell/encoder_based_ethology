#!/usr/bin/env python3
import argparse
import json
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
        "Missing Python dependency. This script needs opencv-python, pandas, numpy, and matplotlib."
    ) from exc


REGIMES = ["high_both", "high_cd10_only", "high_smvo_only", "low_both"]
RATIO_CATEGORIES = [
    "high_both_absolute",
    "cd10_only_strong",
    "mv_only_strong",
    "cd10_boundary_mv_high",
    "mv_boundary_cd10_high",
    "high_both_boundary",
    "cd10_high_mv_mid",
    "mv_high_cd10_mid",
    "low_both",
]


def load_second_bins(input_root):
    rows = []
    for path in sorted(pathlib.Path(input_root).glob("*/all_wells_comparison.csv")):
        video = path.parent.name
        df = pd.read_csv(path)
        df["video"] = video
        df["second_bin"] = np.floor(df["time_seconds"]).astype(int)
        sec = (
            df.groupby(["video", "well", "second_bin"], as_index=False)
            .agg(
                cd10=("cd10", "sum"),
                smvo=("motion_metric", "mean"),
                raw_mv_energy=("raw_motion_energy", "mean"),
                coherence=("coherence", "mean"),
                max_smvo=("motion_metric", "max"),
                metric_label=("motion_metric_label", "first"),
            )
        )
        rows.append(sec)
    if not rows:
        raise SystemExit(f"No all_wells_comparison.csv files found under {input_root}")
    return pd.concat(rows, ignore_index=True)


def add_regimes(sec, cd_quantile, smvo_quantile):
    sec = sec.copy()
    cd_thr = sec.groupby(["video", "well"])["cd10"].transform(lambda s: s.quantile(cd_quantile))
    smvo_thr = sec.groupby(["video", "well"])["smvo"].transform(lambda s: s.quantile(smvo_quantile))
    sec["cd10_high"] = sec["cd10"] > cd_thr
    sec["smvo_high"] = sec["smvo"] > smvo_thr
    sec["regime"] = "low_both"
    sec.loc[sec["cd10_high"] & sec["smvo_high"], "regime"] = "high_both"
    sec.loc[sec["cd10_high"] & ~sec["smvo_high"], "regime"] = "high_cd10_only"
    sec.loc[~sec["cd10_high"] & sec["smvo_high"], "regime"] = "high_smvo_only"
    sec["cd10_threshold"] = cd_thr
    sec["smvo_threshold"] = smvo_thr
    return sec


def add_ratio_categories(
    sec,
    strong_ratio,
    low_ratio,
    boundary_low_ratio,
    boundary_high_ratio,
    min_cd_threshold,
    min_smvo_threshold,
):
    sec = sec.copy()
    sec["binary_regime"] = sec["regime"]
    sec["effective_cd10_threshold"] = np.maximum(sec["cd10_threshold"], min_cd_threshold)
    sec["effective_smvo_threshold"] = np.maximum(sec["smvo_threshold"], min_smvo_threshold)
    sec["cd10_ratio"] = np.divide(
        sec["cd10"],
        sec["effective_cd10_threshold"],
        out=np.zeros(len(sec), dtype=float),
        where=sec["effective_cd10_threshold"] > 0,
    )
    sec["smvo_ratio"] = np.divide(
        sec["smvo"],
        sec["effective_smvo_threshold"],
        out=np.zeros(len(sec), dtype=float),
        where=sec["effective_smvo_threshold"] > 0,
    )

    cd = sec["cd10_ratio"]
    mv = sec["smvo_ratio"]
    category = np.full(len(sec), "low_both", dtype=object)
    category[(cd >= strong_ratio) & (mv >= strong_ratio)] = "high_both_absolute"
    category[(cd >= strong_ratio) & (mv <= low_ratio)] = "cd10_only_strong"
    category[(mv >= strong_ratio) & (cd <= low_ratio)] = "mv_only_strong"
    category[(mv >= boundary_high_ratio) & (cd >= boundary_low_ratio) & (cd < boundary_high_ratio)] = "cd10_boundary_mv_high"
    category[(cd >= boundary_high_ratio) & (mv >= boundary_low_ratio) & (mv < boundary_high_ratio)] = "mv_boundary_cd10_high"
    category[(cd >= boundary_high_ratio) & (mv >= boundary_high_ratio) & (category == "low_both")] = "high_both_boundary"
    category[(cd >= boundary_high_ratio) & (mv > low_ratio) & (mv < boundary_low_ratio)] = "cd10_high_mv_mid"
    category[(mv >= boundary_high_ratio) & (cd > low_ratio) & (cd < boundary_low_ratio)] = "mv_high_cd10_mid"
    sec["category"] = category
    sec["regime"] = sec["category"]
    return sec


def representative_bins(sec, n_per_regime, category_column="regime", categories=None):
    picks = []
    if categories is None:
        categories = list(dict.fromkeys(sec[category_column]))
    for regime in categories:
        sub = sec[sec[category_column] == regime].copy()
        if sub.empty:
            continue
        if regime in {"high_cd10_only", "cd10_only_strong"}:
            sub["score"] = sub["cd10"] - sub["cd10_threshold"]
        elif regime in {"high_smvo_only", "mv_only_strong"}:
            sub["score"] = sub["smvo"] - sub["smvo_threshold"]
        elif regime in {"high_both", "high_both_absolute", "high_both_boundary"}:
            cd_part = np.where(
                sub["effective_cd10_threshold"].abs() > 1e-6,
                sub["cd10"] / sub["effective_cd10_threshold"],
                sub["cd10"] - sub["cd10_threshold"],
            )
            smvo_part = np.where(
                sub["effective_smvo_threshold"].abs() > 1e-6,
                sub["smvo"] / sub["effective_smvo_threshold"],
                sub["smvo"] - sub["smvo_threshold"],
            )
            sub["score"] = cd_part + smvo_part
        elif regime == "cd10_boundary_mv_high":
            sub["score"] = sub["smvo_ratio"] - (sub["cd10_ratio"] - 1.0).abs()
        elif regime == "mv_boundary_cd10_high":
            sub["score"] = sub["cd10_ratio"] - (sub["smvo_ratio"] - 1.0).abs()
        elif regime == "cd10_high_mv_mid":
            sub["score"] = sub["cd10_ratio"] - sub["smvo_ratio"]
        elif regime == "mv_high_cd10_mid":
            sub["score"] = sub["smvo_ratio"] - sub["cd10_ratio"]
        else:
            sub["score"] = -(sub["cd10"] + sub["smvo"])
        picks.append(sub.sort_values("score", ascending=False).head(n_per_regime))
    if not picks:
        return pd.DataFrame()
    return pd.concat(picks, ignore_index=True)


def crop_path(input_root, video, well):
    return pathlib.Path(input_root) / video / "crops" / f"{video}_{well}.mkv"


def read_frame(video_path, second_bin, fps=100.0):
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"failed to open {video_path}")
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(round(second_bin * fps)))
    ok, frame = cap.read()
    cap.release()
    if not ok:
        return None
    if frame.ndim == 3:
        frame = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return frame


def make_montage(picks, input_root, out_png):
    if picks.empty:
        return
    n = len(picks)
    cols = min(4, n)
    rows = int(np.ceil(n / cols))
    fig, axes = plt.subplots(rows, cols, figsize=(3.6 * cols, 3.4 * rows))
    axes = np.asarray(axes).reshape(-1)
    for ax in axes:
        ax.axis("off")
    for ax, row in zip(axes, picks.itertuples(index=False)):
        frame = read_frame(crop_path(input_root, row.video, row.well), row.second_bin)
        if frame is None:
            ax.text(0.5, 0.5, "missing frame", ha="center", va="center")
            continue
        ax.imshow(frame, cmap="gray", vmin=0, vmax=255)
        ax.set_title(
            f"{row.regime}\n{row.video} {row.well} t={row.second_bin}s\n"
            f"cd10={row.cd10:.0f} sMVO={row.smvo:.3f}",
            fontsize=8,
        )
        ax.axis("off")
    fig.tight_layout()
    fig.savefig(out_png, dpi=160)
    plt.close(fig)


def plot_regime_counts(sec, out_png):
    counts = sec.groupby(["video", "regime"]).size().reset_index(name="n")
    fig, ax = plt.subplots(figsize=(9, 4.5))
    pivot = counts.pivot(index="video", columns="regime", values="n").fillna(0)
    ordered = RATIO_CATEGORIES if any(r in pivot.columns for r in RATIO_CATEGORIES) else REGIMES
    pivot = pivot[[r for r in ordered if r in pivot.columns]]
    pivot.plot(kind="bar", stacked=True, ax=ax)
    ax.set_ylabel("one-second bins")
    ax.set_title("cd(10) vs sMVO regime counts")
    fig.tight_layout()
    fig.savefig(out_png, dpi=160)
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description="Inspect cd(10) and sMVO agreement/disagreement regimes.")
    parser.add_argument("--input-root", default="outputs/mv_active_cd10_multivideo")
    parser.add_argument("--output-dir", default="outputs/mv_active_cd10_multivideo/disagreements")
    parser.add_argument("--cd-quantile", type=float, default=0.90)
    parser.add_argument("--smvo-quantile", type=float, default=0.90)
    parser.add_argument("--n-per-regime", type=int, default=8)
    parser.add_argument("--ratio-categories", action="store_true")
    parser.add_argument("--strong-ratio", type=float, default=1.5)
    parser.add_argument("--low-ratio", type=float, default=0.5)
    parser.add_argument("--boundary-low-ratio", type=float, default=0.95)
    parser.add_argument("--boundary-high-ratio", type=float, default=1.0)
    parser.add_argument("--min-cd-threshold", type=float, default=1.0)
    parser.add_argument(
        "--min-smvo-threshold",
        type=float,
        default=1.0 / (42.0 * 100.0),
        help="Effective denominator floor for MV ratio categories. Default is one active block-frame per second for 42 past-source blocks at 100 fps.",
    )
    args = parser.parse_args()

    out = pathlib.Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    sec = add_regimes(load_second_bins(args.input_root), args.cd_quantile, args.smvo_quantile)
    categories = REGIMES
    if args.ratio_categories:
        sec = add_ratio_categories(
            sec,
            args.strong_ratio,
            args.low_ratio,
            args.boundary_low_ratio,
            args.boundary_high_ratio,
            args.min_cd_threshold,
            args.min_smvo_threshold,
        )
        categories = RATIO_CATEGORIES
    sec.to_csv(out / "second_bin_regimes.csv", index=False)
    picks = representative_bins(sec, args.n_per_regime, categories=categories)
    picks.to_csv(out / "representative_bins.csv", index=False)
    make_montage(picks, args.input_root, out / "representative_frame_montage.png")
    plot_regime_counts(sec, out / "regime_counts.png")
    summary = {
        "unit": "video x well x one-second bin",
        "cd_high_quantile_within_video_well": args.cd_quantile,
        "smvo_high_quantile_within_video_well": args.smvo_quantile,
        "category_mode": "ratio" if args.ratio_categories else "binary",
        "regime_counts": sec["regime"].value_counts().to_dict(),
        "binary_regime_counts": sec["binary_regime"].value_counts().to_dict() if "binary_regime" in sec else sec["regime"].value_counts().to_dict(),
        "ratio_parameters": {
            "strong_ratio": args.strong_ratio,
            "low_ratio": args.low_ratio,
            "boundary_low_ratio": args.boundary_low_ratio,
            "boundary_high_ratio": args.boundary_high_ratio,
            "min_cd_threshold": args.min_cd_threshold,
            "min_smvo_threshold": args.min_smvo_threshold,
        },
        "n_representative_bins": int(len(picks)),
    }
    with open(out / "disagreement_summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
