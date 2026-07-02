#!/usr/bin/env python3
import argparse
import json
import math
import os
import pathlib

os.environ.setdefault("MPLCONFIGDIR", "/tmp/mestimate-sidecar-matplotlib")

try:
    import matplotlib.pyplot as plt
    import numpy as np
    import pandas as pd
    import seaborn as sns
except ModuleNotFoundError as exc:
    raise SystemExit(
        "Missing Python dependency. This script needs pandas, numpy, matplotlib, and seaborn."
    ) from exc


def load_second_traces(input_root):
    rows = []
    for path in sorted(pathlib.Path(input_root).glob("*/all_wells_comparison.csv")):
        video = path.parent.name
        df = pd.read_csv(path)
        if "motion_metric" not in df.columns:
            raise SystemExit(f"{path} lacks motion_metric; rerun comparison with --motion-metric active_fraction.")
        df["video"] = video
        df["second_bin"] = np.floor(df["time_seconds"]).astype(int)
        sec = (
            df.groupby(["video", "well", "second_bin"], as_index=False)
            .agg(
                cd10=("cd10", "sum"),
                smvo=("motion_metric", "mean"),
                raw_mv_energy=("raw_motion_energy", "mean"),
                cd10_sum_absdiff=("cd10_sum_absdiff", "sum"),
            )
        )
        rows.append(sec)
    if not rows:
        raise SystemExit(f"No all_wells_comparison.csv files found under {input_root}")
    return pd.concat(rows, ignore_index=True)


def zscore(x):
    x = np.asarray(x, dtype=float)
    sd = x.std()
    if not np.isfinite(sd) or sd == 0:
        return x * 0.0
    return (x - x.mean()) / sd


def corr(a, b):
    a = zscore(a)
    b = zscore(b)
    if a.std() == 0 or b.std() == 0:
        return np.nan
    return float(np.corrcoef(a, b)[0, 1])


def cosine(a, b):
    a = zscore(a)
    b = zscore(b)
    denom = np.linalg.norm(a) * np.linalg.norm(b)
    if denom == 0:
        return np.nan
    return float(np.dot(a, b) / denom)


def bhattacharyya_distance(a, b, bins):
    a = np.asarray([x for x in a if np.isfinite(x)], dtype=float)
    b = np.asarray([x for x in b if np.isfinite(x)], dtype=float)
    ha, _ = np.histogram(a, bins=bins)
    hb, _ = np.histogram(b, bins=bins)
    if ha.sum() == 0 or hb.sum() == 0:
        return np.nan
    pa = ha / ha.sum()
    pb = hb / hb.sum()
    bc = np.sum(np.sqrt(pa * pb))
    return float(-np.log(max(bc, 1e-12)))


def cross_metric_pairs(sec):
    rows = []
    for video, vdf in sec.groupby("video"):
        wells = sorted(vdf["well"].unique())
        traces = {
            well: vdf[vdf["well"] == well].sort_values("second_bin")
            for well in wells
        }
        for cd_well in wells:
            for mv_well in wells:
                left = traces[cd_well][["second_bin", "cd10"]]
                right = traces[mv_well][["second_bin", "smvo"]]
                merged = left.merge(right, on="second_bin", how="inner")
                kind = "same_well" if cd_well == mv_well else "different_well"
                rows.append(
                    {
                        "video": video,
                        "cd10_well": cd_well,
                        "smvo_well": mv_well,
                        "pair_type": kind,
                        "pearson_r": corr(merged["cd10"], merged["smvo"]),
                        "cosine": cosine(merged["cd10"], merged["smvo"]),
                    }
                )
    return pd.DataFrame(rows)


def within_metric_unrelated(sec):
    rows = []
    for video, vdf in sec.groupby("video"):
        wells = sorted(vdf["well"].unique())
        traces = {
            well: vdf[vdf["well"] == well].sort_values("second_bin")
            for well in wells
        }
        for metric in ["cd10", "smvo", "raw_mv_energy"]:
            for i, well_a in enumerate(wells):
                for well_b in wells[i + 1 :]:
                    left = traces[well_a][["second_bin", metric]]
                    right = traces[well_b][["second_bin", metric]]
                    merged = left.merge(right, on="second_bin", how="inner", suffixes=("_a", "_b"))
                    rows.append(
                        {
                            "video": video,
                            "metric": metric,
                            "well_a": well_a,
                            "well_b": well_b,
                            "pearson_r": corr(merged[f"{metric}_a"], merged[f"{metric}_b"]),
                            "cosine": cosine(merged[f"{metric}_a"], merged[f"{metric}_b"]),
                        }
                    )
    return pd.DataFrame(rows)


def plot_same_vs_different(pairs, out):
    fig, axes = plt.subplots(1, 2, figsize=(11, 4))
    for ax, metric in zip(axes, ["pearson_r", "cosine"]):
        sns.histplot(
            data=pairs,
            x=metric,
            hue="pair_type",
            bins=np.linspace(-1, 1, 41),
            stat="density",
            common_norm=False,
            element="step",
            ax=ax,
        )
        ax.set_title(f"cd(10) vs sMVO: {metric}")
        ax.set_xlim(-1, 1)
    fig.tight_layout()
    fig.savefig(out, dpi=160)
    plt.close(fig)


def plot_unrelated(unrelated, out):
    fig, ax = plt.subplots(figsize=(7, 4.5))
    sns.boxplot(data=unrelated, x="metric", y="pearson_r", ax=ax)
    sns.stripplot(data=unrelated, x="metric", y="pearson_r", color="black", alpha=0.45, size=3, ax=ax)
    ax.set_title("Different-well same-video synchrony")
    ax.set_ylabel("Pearson r")
    fig.tight_layout()
    fig.savefig(out, dpi=160)
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description="Specificity diagnostics for cd(10) and sMVO traces.")
    parser.add_argument("--input-root", default="outputs/mv_active_cd10_multivideo")
    parser.add_argument("--output-dir", default="outputs/mv_active_cd10_multivideo/specificity")
    args = parser.parse_args()

    out = pathlib.Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    sec = load_second_traces(args.input_root)
    sec.to_csv(out / "second_level_traces.csv", index=False)

    pairs = cross_metric_pairs(sec)
    pairs.to_csv(out / "cd10_smvo_same_vs_different_pairs.csv", index=False)
    unrelated = within_metric_unrelated(sec)
    unrelated.to_csv(out / "different_well_synchrony.csv", index=False)

    bins = np.linspace(-1, 1, 41)
    same = pairs[pairs["pair_type"] == "same_well"]
    different = pairs[pairs["pair_type"] == "different_well"]
    summary = {
        "feature_name": "sMVO",
        "feature_full_name": "supported motion-vector occupancy",
        "unit": "video x well x one-second bin",
        "same_well_pairs": int(len(same)),
        "different_well_pairs": int(len(different)),
        "same_well_pearson_median": float(same["pearson_r"].median()),
        "different_well_pearson_median": float(different["pearson_r"].median()),
        "same_well_cosine_median": float(same["cosine"].median()),
        "different_well_cosine_median": float(different["cosine"].median()),
        "bhattacharyya_distance_pearson": bhattacharyya_distance(same["pearson_r"], different["pearson_r"], bins),
        "bhattacharyya_distance_cosine": bhattacharyya_distance(same["cosine"], different["cosine"], bins),
        "different_well_synchrony_median_by_metric": unrelated.groupby("metric")["pearson_r"].median().to_dict(),
    }
    with open(out / "specificity_summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    plot_same_vs_different(pairs, out / "cd10_smvo_same_vs_different_hist.png")
    plot_unrelated(unrelated, out / "different_well_synchrony.png")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
