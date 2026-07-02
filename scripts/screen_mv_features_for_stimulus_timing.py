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


DEFAULT_CANDIDATES = [
    "cd10",
    "mv_bout_fraction",
    "mv_bout_log_vectors",
    "mv_bout_inverse_saturation_sq",
    "mv_bout_capped_6_blocks",
    "mv_bout_midrange_3_10_blocks",
]


def load_frame_rows(input_root):
    rows = []
    for path in sorted(pathlib.Path(input_root).glob("*/all_wells_comparison.csv")):
        video = path.parent.name
        df = pd.read_csv(path)
        df.insert(0, "video", video)
        rows.append(df)
    if not rows:
        raise SystemExit(f"No all_wells_comparison.csv files found under {input_root}")
    return pd.concat(rows, ignore_index=True)


def add_candidate_frame_features(df):
    df = df.copy()
    active_fraction = df["mv_active_fraction"].astype(float).fillna(0.0).clip(lower=0.0)
    bout_fraction = df["mv_bout_fraction"].astype(float).fillna(0.0).clip(lower=0.0)
    active_vectors = df["mv_active_vectors"].astype(float).fillna(0.0)
    bout_vectors = df["mv_bout_active_vectors"].astype(float).fillna(0.0)
    n_vectors = df["analysis_n_vectors"].astype(float).replace(0, np.nan).fillna(1.0)
    coherence = df["coherence"].astype(float).fillna(0.0).clip(lower=0.0)

    df["mv_bout_no_global_35"] = bout_fraction.where(active_fraction < 0.35, 0.0)
    df["mv_bout_inverse_saturation_sq"] = bout_fraction * (1.0 - active_fraction).clip(lower=0.0) ** 2
    df["mv_bout_midrange_3_10_blocks"] = bout_fraction.where((active_vectors >= 3) & (active_vectors <= 10), 0.0)
    df["mv_bout_low_coherence_060"] = bout_fraction.where(coherence <= 0.60, 0.0)
    df["mv_bout_capped_6_blocks"] = np.minimum(bout_vectors, 6.0) / n_vectors
    df["mv_bout_log_vectors"] = np.log1p(bout_vectors) / np.log1p(n_vectors)
    return df


def zscore(s):
    s = s.astype(float)
    sd = s.std(ddof=0)
    if not np.isfinite(sd) or sd == 0:
        return s * 0.0
    return (s - s.mean()) / sd


def aggregate_seconds(df, candidates):
    df = df.copy()
    df["second_bin"] = np.floor(df["time_seconds"]).astype(int)
    agg = {}
    for c in candidates:
        if c == "cd10":
            agg[c] = ("cd10", "sum")
        elif c in df.columns:
            agg[c] = (c, "mean")
    missing = [c for c in candidates if c not in agg]
    if missing:
        raise SystemExit(f"Missing candidate columns: {missing}")
    sec = df.groupby(["video", "well", "second_bin"], as_index=False).agg(**agg)
    return sec


def candidate_consensus(sec, candidate, well_quantile):
    work = sec[["video", "well", "second_bin", candidate]].copy()
    work["well_z"] = work.groupby(["video", "well"])[candidate].transform(zscore)
    work["well_thr"] = work.groupby(["video", "well"])[candidate].transform(lambda s: s.quantile(well_quantile))
    work["well_active"] = work[candidate] > work["well_thr"]
    consensus = (
        work.groupby(["video", "second_bin"], as_index=False)
        .agg(
            mean_z=("well_z", "mean"),
            max_z=("well_z", "max"),
            active_wells=("well_active", "sum"),
            n_wells=("well_active", "size"),
            mean_value=(candidate, "mean"),
        )
    )
    consensus["positive_mean_z"] = consensus["mean_z"].clip(lower=0.0)
    consensus["active_well_fraction"] = consensus["active_wells"] / consensus["n_wells"]
    consensus["stimulus_like_score"] = consensus["positive_mean_z"] * (0.5 + consensus["active_well_fraction"])
    consensus["candidate"] = candidate
    return consensus


def pick_peaks(consensus, top_n, min_gap_seconds):
    peaks = []
    for video, g in consensus.groupby("video"):
        chosen = []
        sub = g.sort_values("stimulus_like_score", ascending=False)
        for row in sub.itertuples(index=False):
            if row.stimulus_like_score <= 0:
                continue
            if any(abs(float(row.second_bin) - t) < min_gap_seconds for t in chosen):
                continue
            peaks.append(row._asdict())
            chosen.append(float(row.second_bin))
            if len(chosen) >= top_n:
                break
    return pd.DataFrame(peaks)


def recurrence_summary(peaks, n_videos, tolerance_seconds):
    rows = []
    if peaks.empty:
        return pd.DataFrame()
    for candidate, g in peaks.groupby("candidate"):
        seconds = sorted(g["second_bin"].astype(int).unique())
        best_count = 0
        best_second = None
        rec_rows = []
        for sec in seconds:
            near = g[np.abs(g["second_bin"] - sec) <= tolerance_seconds]
            count = near["video"].nunique()
            score = near["stimulus_like_score"].mean()
            rec_rows.append({"candidate": candidate, "second_bin": sec, "video_count": count, "mean_peak_score": score})
            if count > best_count or (count == best_count and (best_second is None or score > best_score)):
                best_count = count
                best_second = sec
                best_score = score
        rows.append(
            {
                "candidate": candidate,
                "best_recurrent_second": best_second,
                "best_recurrent_video_count": best_count,
                "best_recurrent_video_fraction": best_count / n_videos if n_videos else 0,
                "mean_top_peak_score": g["stimulus_like_score"].mean(),
                "mean_top_active_well_fraction": g["active_well_fraction"].mean(),
                "n_peaks": len(g),
            }
        )
    return pd.DataFrame(rows)


def plot_candidate_heatmaps(consensus, summary, out_dir, max_candidates):
    chosen = summary.sort_values(
        ["best_recurrent_video_count", "mean_top_peak_score"], ascending=False
    )["candidate"].head(max_candidates)
    for candidate in chosen:
        g = consensus[consensus["candidate"] == candidate]
        pivot = g.pivot(index="video", columns="second_bin", values="stimulus_like_score").fillna(0.0)
        fig, ax = plt.subplots(figsize=(12, max(4, 0.35 * len(pivot))))
        sns.heatmap(pivot, cmap="mako", ax=ax)
        ax.set_title(f"Stimulus-like consensus score: {candidate}")
        ax.set_xlabel("seconds within crop")
        ax.set_ylabel("video")
        fig.tight_layout()
        fig.savefig(out_dir / f"{candidate}.stimulus_like_heatmap.png", dpi=160)
        plt.close(fig)


def plot_summary(summary, out):
    plot = summary.sort_values(["best_recurrent_video_count", "mean_top_peak_score"], ascending=False)
    fig, ax = plt.subplots(figsize=(9, max(4, 0.4 * len(plot))))
    sns.scatterplot(
        data=plot,
        x="mean_top_peak_score",
        y="best_recurrent_video_count",
        size="mean_top_active_well_fraction",
        hue="candidate",
        legend=False,
        sizes=(60, 260),
        ax=ax,
    )
    for row in plot.itertuples(index=False):
        ax.text(row.mean_top_peak_score, row.best_recurrent_video_count, row.candidate, fontsize=8)
    ax.set_xlabel("mean top peak consensus score")
    ax.set_ylabel("videos sharing best recurrent second")
    fig.tight_layout()
    fig.savefig(out, dpi=160)
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description="Screen MV-derived features for stimulus-like timing structure.")
    parser.add_argument("--input-root", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--candidates", nargs="+", default=DEFAULT_CANDIDATES)
    parser.add_argument("--well-quantile", type=float, default=0.90)
    parser.add_argument("--top-peaks-per-video", type=int, default=8)
    parser.add_argument("--min-gap-seconds", type=float, default=5.0)
    parser.add_argument("--recurrence-tolerance-seconds", type=int, default=2)
    parser.add_argument("--max-heatmaps", type=int, default=6)
    args = parser.parse_args()

    out = pathlib.Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    frames = add_candidate_frame_features(load_frame_rows(args.input_root))
    sec = aggregate_seconds(frames, args.candidates)
    consensus_tables = []
    peak_tables = []
    for candidate in args.candidates:
        consensus = candidate_consensus(sec, candidate, args.well_quantile)
        consensus_tables.append(consensus)
        peak_tables.append(pick_peaks(consensus, args.top_peaks_per_video, args.min_gap_seconds))
    consensus = pd.concat(consensus_tables, ignore_index=True)
    peaks = pd.concat(peak_tables, ignore_index=True) if peak_tables else pd.DataFrame()
    summary = recurrence_summary(peaks, sec["video"].nunique(), args.recurrence_tolerance_seconds)
    if not summary.empty:
        summary = summary.sort_values(
            ["best_recurrent_video_count", "mean_top_peak_score", "mean_top_active_well_fraction"],
            ascending=False,
        )
    sec.to_csv(out / "candidate_second_level_values.csv", index=False)
    consensus.to_csv(out / "candidate_consensus_by_second.csv", index=False)
    peaks.to_csv(out / "candidate_top_peaks.csv", index=False)
    summary.to_csv(out / "stimulus_timing_candidate_summary.csv", index=False)
    if not summary.empty:
        plot_summary(summary, out / "stimulus_candidate_summary.png")
        plot_candidate_heatmaps(consensus, summary, out, args.max_heatmaps)
    notes = {
        "unit": {
            "input": "video x well x frame",
            "second_level": "video x well x one-second bin",
            "consensus": "video x one-second bin, aggregated across selected wells",
        },
        "weak_target": "Candidate stimulus timing is inferred from cross-well synchronous peaks and recurrence of relative seconds across videos. No stimulus metadata was used.",
        "limitations": [
            "Only selected wells are represented.",
            "Nonresponsive stimuli and incidental synchronized movement can both affect scores.",
            "Recurring seconds are relative to the cropped window start, not absolute experiment time.",
        ],
        "parameters": vars(args),
    }
    (out / "analysis_notes.json").write_text(json.dumps(notes, indent=2), encoding="utf-8")
    print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
