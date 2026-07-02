#!/usr/bin/env python3
import argparse
import json
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


def load_rows(input_root):
    tables = []
    for path in sorted(pathlib.Path(input_root).glob("*/all_wells_comparison.csv")):
        video = path.parent.name
        df = pd.read_csv(path)
        df.insert(0, "video", video)
        tables.append(df)
    if not tables:
        raise SystemExit(f"No all_wells_comparison.csv files found under {input_root}")
    return pd.concat(tables, ignore_index=True)


def safe_divide(num, den):
    return np.divide(num, den, out=np.zeros_like(num, dtype=float), where=den != 0)


def add_candidate_frame_features(df):
    df = df.copy()
    active_fraction = df["mv_active_fraction"].astype(float).fillna(0.0).clip(lower=0.0)
    bout_fraction = df["mv_bout_fraction"].astype(float).fillna(0.0).clip(lower=0.0)
    active_vectors = df["mv_active_vectors"].astype(float).fillna(0.0)
    bout_vectors = df["mv_bout_active_vectors"].astype(float).fillna(0.0)
    n_vectors = df["analysis_n_vectors"].astype(float).replace(0, np.nan).fillna(1.0)
    coherence = df["coherence"].astype(float).fillna(0.0).clip(lower=0.0)

    candidates = {}
    candidates["mv_bout_fraction"] = bout_fraction
    candidates["mv_bout_no_global_50"] = bout_fraction.where(active_fraction < 0.50, 0.0)
    candidates["mv_bout_no_global_35"] = bout_fraction.where(active_fraction < 0.35, 0.0)
    candidates["mv_bout_no_global_25"] = bout_fraction.where(active_fraction < 0.25, 0.0)
    candidates["mv_bout_inverse_saturation"] = bout_fraction * (1.0 - active_fraction).clip(lower=0.0)
    candidates["mv_bout_inverse_saturation_sq"] = bout_fraction * (1.0 - active_fraction).clip(lower=0.0) ** 2
    candidates["mv_bout_midrange_2_10_blocks"] = bout_fraction.where((active_vectors >= 2) & (active_vectors <= 10), 0.0)
    candidates["mv_bout_midrange_2_6_blocks"] = bout_fraction.where((active_vectors >= 2) & (active_vectors <= 6), 0.0)
    candidates["mv_bout_midrange_3_10_blocks"] = bout_fraction.where((active_vectors >= 3) & (active_vectors <= 10), 0.0)
    candidates["mv_bout_low_coherence_075"] = bout_fraction.where(coherence <= 0.75, 0.0)
    candidates["mv_bout_low_coherence_060"] = bout_fraction.where(coherence <= 0.60, 0.0)
    candidates["mv_bout_capped_6_blocks"] = np.minimum(bout_vectors, 6.0) / n_vectors
    candidates["mv_bout_capped_10_blocks"] = np.minimum(bout_vectors, 10.0) / n_vectors
    candidates["mv_bout_log_vectors"] = np.log1p(bout_vectors) / np.log1p(n_vectors)
    candidates["mv_bout_log_vectors_no_global_35"] = candidates["mv_bout_log_vectors"].where(active_fraction < 0.35, 0.0)

    for name, values in candidates.items():
        df[name] = values.astype(float)
    return df, list(candidates)


def aggregate_seconds(df, candidate_cols):
    df = df.copy()
    df["second_bin"] = np.floor(df["time_seconds"]).astype(int)
    agg = {
        "cd10": ("cd10", "sum"),
        "cd10_active_frames": ("cd10", lambda s: int((s > 0).sum())),
        "cd10_max": ("cd10", "max"),
        "raw_mv_energy": ("raw_motion_energy", "mean"),
        "coherence": ("coherence", "mean"),
        "max_active_fraction": ("mv_active_fraction", "max"),
        "mean_active_fraction": ("mv_active_fraction", "mean"),
    }
    for col in candidate_cols:
        agg[col] = (col, "mean")
        agg[f"{col}_max"] = (col, "max")
        agg[f"{col}_active_frames"] = (col, lambda s: int((s > 0).sum()))
    return df.groupby(["video", "well", "second_bin"], as_index=False).agg(**agg)


def correlations(sec, candidate_cols):
    rows = []
    for candidate in candidate_cols:
        per_trace = []
        for (video, well), g in sec.groupby(["video", "well"]):
            x = g[candidate].astype(float)
            y = g["cd10"].astype(float)
            pearson = x.corr(y)
            spearman = x.corr(y, method="spearman")
            per_trace.append(
                {
                    "candidate": candidate,
                    "video": video,
                    "well": well,
                    "pearson_r": pearson,
                    "spearman_r": spearman,
                    "candidate_nonzero_seconds": int((x > 0).sum()),
                    "cd10_nonzero_seconds": int((y > 0).sum()),
                }
            )
        t = pd.DataFrame(per_trace)
        rows.append(
            {
                "candidate": candidate,
                "mean_pearson_r": t["pearson_r"].mean(skipna=True),
                "median_pearson_r": t["pearson_r"].median(skipna=True),
                "mean_spearman_r": t["spearman_r"].mean(skipna=True),
                "median_spearman_r": t["spearman_r"].median(skipna=True),
                "n_valid_pearson": int(t["pearson_r"].notna().sum()),
                "mean_candidate_nonzero_seconds": t["candidate_nonzero_seconds"].mean(),
            }
        )
    return pd.DataFrame(rows), pd.concat(
        [
            pd.DataFrame(
                [
                    {
                        "candidate": candidate,
                        "video": video,
                        "well": well,
                        "pearson_r": g[candidate].corr(g["cd10"]),
                        "spearman_r": g[candidate].corr(g["cd10"], method="spearman"),
                    }
                    for (video, well), g in sec.groupby(["video", "well"])
                ]
            )
            for candidate in candidate_cols
        ],
        ignore_index=True,
    )


def category_counts(sec, candidate_cols, quantile, strong_ratio, low_ratio, min_mv_threshold):
    rows = []
    for candidate in candidate_cols:
        work = sec[["video", "well", "second_bin", "cd10", candidate]].copy()
        work["cd10_threshold"] = work.groupby(["video", "well"])["cd10"].transform(lambda s: s.quantile(quantile))
        work["mv_threshold"] = work.groupby(["video", "well"])[candidate].transform(lambda s: s.quantile(quantile))
        work["effective_cd10_threshold"] = work["cd10_threshold"].clip(lower=1.0)
        work["effective_mv_threshold"] = work["mv_threshold"].clip(lower=min_mv_threshold)
        work["cd10_ratio"] = safe_divide(work["cd10"].to_numpy(float), work["effective_cd10_threshold"].to_numpy(float))
        work["mv_ratio"] = safe_divide(work[candidate].to_numpy(float), work["effective_mv_threshold"].to_numpy(float))
        cd = work["cd10_ratio"]
        mv = work["mv_ratio"]
        work["category"] = "other"
        work.loc[(cd >= strong_ratio) & (mv >= strong_ratio), "category"] = "high_both_absolute"
        work.loc[(cd >= strong_ratio) & (mv <= low_ratio), "category"] = "cd10_only_strong"
        work.loc[(mv >= strong_ratio) & (cd <= low_ratio), "category"] = "mv_only_strong"
        work.loc[(cd < strong_ratio) & (mv < strong_ratio), "category"] = "not_strong"
        counts = work["category"].value_counts().to_dict()
        rows.append(
            {
                "candidate": candidate,
                "high_both_absolute": counts.get("high_both_absolute", 0),
                "cd10_only_strong": counts.get("cd10_only_strong", 0),
                "mv_only_strong": counts.get("mv_only_strong", 0),
                "not_strong": counts.get("not_strong", 0),
                "other": counts.get("other", 0),
                "strong_discordance_total": counts.get("cd10_only_strong", 0) + counts.get("mv_only_strong", 0),
            }
        )
    return pd.DataFrame(rows)


def plot_summary(summary, out):
    plot = summary.sort_values("mean_pearson_r", ascending=False).copy()
    fig, ax = plt.subplots(figsize=(10, max(4, 0.38 * len(plot))))
    sns.barplot(data=plot, y="candidate", x="mean_pearson_r", ax=ax, color="#4f8bc9")
    ax.set_xlabel("mean Pearson r vs cd10 across video-well traces")
    ax.set_ylabel("")
    fig.tight_layout()
    fig.savefig(out, dpi=160)
    plt.close(fig)


def plot_tradeoff(summary, out):
    fig, ax = plt.subplots(figsize=(7.2, 5.2))
    sns.scatterplot(
        data=summary,
        x="mean_pearson_r",
        y="strong_discordance_total",
        hue="mv_only_strong",
        size="mean_candidate_nonzero_seconds",
        sizes=(30, 220),
        ax=ax,
    )
    for row in summary.itertuples(index=False):
        ax.text(row.mean_pearson_r, row.strong_discordance_total, row.candidate, fontsize=7, alpha=0.75)
    ax.set_xlabel("mean Pearson r vs cd10")
    ax.set_ylabel("strong discordance bins")
    fig.tight_layout()
    fig.savefig(out, dpi=160)
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description="Iterate MV-derived candidate features against cd10.")
    parser.add_argument("--input-root", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--quantile", type=float, default=0.90)
    parser.add_argument("--strong-ratio", type=float, default=1.5)
    parser.add_argument("--low-ratio", type=float, default=0.5)
    parser.add_argument("--min-mv-threshold", type=float, default=1.0 / (42.0 * 100.0))
    args = parser.parse_args()

    out = pathlib.Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    rows = load_rows(args.input_root)
    frames, candidate_cols = add_candidate_frame_features(rows)
    sec = aggregate_seconds(frames, candidate_cols)
    corr_summary, per_trace = correlations(sec, candidate_cols)
    cat = category_counts(sec, candidate_cols, args.quantile, args.strong_ratio, args.low_ratio, args.min_mv_threshold)
    summary = corr_summary.merge(cat, on="candidate", how="left")
    summary = summary.sort_values(
        ["mean_pearson_r", "strong_discordance_total", "mv_only_strong"],
        ascending=[False, True, True],
    )
    sec.to_csv(out / "candidate_second_level_features.csv", index=False)
    per_trace.to_csv(out / "candidate_per_trace_correlations.csv", index=False)
    summary.to_csv(out / "candidate_summary.csv", index=False)
    plot_summary(summary, out / "candidate_mean_pearson.png")
    plot_tradeoff(summary, out / "candidate_tradeoff.png")
    notes = {
        "unit": "candidate metrics are computed per frame, then averaged to video x well x one-second bins",
        "cd10_unit": "sum of per-frame cd10 pixel counts within the same one-second bin",
        "strong_category_rule": {
            "threshold_scope": "within video x well",
            "quantile": args.quantile,
            "strong_ratio": args.strong_ratio,
            "low_ratio": args.low_ratio,
            "min_mv_threshold": args.min_mv_threshold,
        },
        "warning": "Feature screen only. Higher cd10 concordance is not proof of biological validity, and lower discordance can mean over-filtering.",
    }
    (out / "analysis_notes.json").write_text(json.dumps(notes, indent=2), encoding="utf-8")
    print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
