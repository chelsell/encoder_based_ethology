#!/usr/bin/env python3
import argparse
import json
import os
import pathlib
import re

os.environ.setdefault("MPLCONFIGDIR", "/tmp/mestimate-sidecar-matplotlib")

try:
    import matplotlib.pyplot as plt
    import numpy as np
    import pandas as pd
    import seaborn as sns
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.metrics import balanced_accuracy_score, confusion_matrix
    from sklearn.model_selection import StratifiedGroupKFold, cross_val_score
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
except ModuleNotFoundError as exc:
    raise SystemExit(
        "Missing Python dependency. This script needs pandas, numpy, matplotlib, "
        "seaborn, and scikit-learn."
    ) from exc


FRAME_FEATURES = [
    "cd10",
    "sum_absdiff",
    "raw_motion_energy",
    "analysis_motion_energy",
    "mean_magnitude_px",
    "p95_magnitude_px",
    "max_magnitude_px",
    "coherence",
    "mv_active_fraction",
    "mv_active_vectors",
    "mv_bout_fraction",
    "mv_bout_active_vectors",
]


def stage_from_video(video):
    match = re.search(r"_S(\d+)$", str(video))
    return f"S{match.group(1)}" if match else "unknown"


def load_rows(root):
    tables = []
    for path in sorted(pathlib.Path(root).glob("*/all_wells_comparison.csv")):
        video = path.parent.name
        df = pd.read_csv(path)
        df.insert(0, "video", video)
        df["stage"] = stage_from_video(video)
        tables.append(df)
    if not tables:
        raise SystemExit(f"No all_wells_comparison.csv files found under {root}")
    return pd.concat(tables, ignore_index=True)


def longest_true_run(values):
    values = np.asarray(values, dtype=bool)
    best = 0
    cur = 0
    for value in values:
        if value:
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return int(best)


def aggregate_one_second(df):
    df = df.copy()
    df["second_bin"] = np.floor(df["time_seconds"]).astype(int)
    agg = {
        "stage": ("stage", "first"),
        "cd10_sum": ("cd10", "sum"),
        "cd10_max": ("cd10", "max"),
        "cd10_active_fraction": ("cd10", lambda s: float((s > 0).mean())),
    }
    for col in FRAME_FEATURES:
        if col in df.columns:
            agg[f"{col}_mean"] = (col, "mean")
            agg[f"{col}_p95"] = (col, lambda s: s.quantile(0.95))
            agg[f"{col}_max"] = (col, "max")
    if "mv_bout_frame" in df.columns:
        agg["mv_bout_frame_fraction"] = ("mv_bout_frame", "mean")
        agg["mv_bout_longest_run_frames"] = ("mv_bout_frame", longest_true_run)
    if {"mean_dx_px", "mean_dy_px"}.issubset(df.columns):
        agg["mean_dx_px_mean"] = ("mean_dx_px", "mean")
        agg["mean_dy_px_mean"] = ("mean_dy_px", "mean")
        agg["mean_abs_dx_px"] = ("mean_dx_px", lambda s: s.abs().mean())
        agg["mean_abs_dy_px"] = ("mean_dy_px", lambda s: s.abs().mean())
    return df.groupby(["video", "stage", "well", "second_bin"], as_index=False).agg(**agg)


def summarize_profiles(sec):
    metric_cols = [
        c
        for c in sec.columns
        if c not in {"video", "stage", "well", "second_bin"}
        and pd.api.types.is_numeric_dtype(sec[c])
    ]
    agg = {}
    for col in metric_cols:
        agg[f"{col}_mean"] = (col, "mean")
        agg[f"{col}_median"] = (col, "median")
        agg[f"{col}_p95"] = (col, lambda s: s.quantile(0.95))
        agg[f"{col}_max"] = (col, "max")
    profiles = sec.groupby(["video", "stage", "well"], as_index=False).agg(**agg)
    return profiles


def feature_columns(df):
    return [
        c
        for c in df.columns
        if c not in {"video", "stage", "well", "second_bin"}
        and pd.api.types.is_numeric_dtype(df[c])
    ]


def evaluate_target(sec, target, out):
    features = feature_columns(sec)
    data = sec.copy()
    X = data[features].replace([np.inf, -np.inf], np.nan).fillna(0.0)
    y = data[target].astype(str)
    if y.nunique() < 2:
        return None
    groups = data["video"].astype(str) + ":" + data["well"].astype(str)
    clf = make_pipeline(
        StandardScaler(),
        RandomForestClassifier(
            n_estimators=400,
            min_samples_leaf=3,
            random_state=1,
            class_weight="balanced",
            n_jobs=-1,
        ),
    )
    n_splits = min(5, y.value_counts().min(), groups.nunique())
    if n_splits < 2:
        return None
    scores = cross_val_score(
        clf,
        X,
        y,
        groups=groups,
        cv=StratifiedGroupKFold(n_splits=n_splits, shuffle=True, random_state=1),
        scoring="balanced_accuracy",
        n_jobs=-1,
    )
    model = clf.fit(X, y)
    pred = model.predict(X)
    labels = sorted(y.unique())
    conf = confusion_matrix(y, pred, labels=labels)
    plot_confusion(conf, labels, f"{target} classification, apparent fit", out / f"{target}.confusion.png")
    rf = model.named_steps["randomforestclassifier"]
    importance = pd.DataFrame({"feature": features, "importance": rf.feature_importances_}).sort_values(
        "importance", ascending=False
    )
    importance.to_csv(out / f"{target}.feature_importance.csv", index=False)
    return {
        "target": target,
        "n_classes": int(y.nunique()),
        "chance_balanced_accuracy": float(1.0 / y.nunique()),
        "cv_mean_balanced_accuracy": float(scores.mean()),
        "cv_sd_balanced_accuracy": float(scores.std(ddof=1)) if len(scores) > 1 else 0.0,
        "n_features": len(features),
    }


def plot_confusion(confusion, labels, title, out):
    row_sums = confusion.sum(axis=1, keepdims=True)
    norm = np.divide(confusion, row_sums, out=np.zeros_like(confusion, dtype=float), where=row_sums > 0)
    fig_w = max(6.0, 0.35 * len(labels))
    fig_h = max(5.0, 0.35 * len(labels))
    fig, ax = plt.subplots(figsize=(fig_w, fig_h))
    sns.heatmap(norm, cmap="Blues", xticklabels=labels, yticklabels=labels, ax=ax)
    ax.set_xlabel("predicted")
    ax.set_ylabel("true")
    ax.set_title(title)
    fig.tight_layout()
    fig.savefig(out, dpi=160)
    plt.close(fig)


def pairwise_profile_distances(profiles):
    features = feature_columns(profiles)
    X = profiles[features].replace([np.inf, -np.inf], np.nan).fillna(0.0)
    Xz = StandardScaler().fit_transform(X)
    rows = []
    for i in range(len(profiles)):
        for j in range(i + 1, len(profiles)):
            left = profiles.iloc[i]
            right = profiles.iloc[j]
            rows.append(
                {
                    "left_video": left["video"],
                    "left_well": left["well"],
                    "right_video": right["video"],
                    "right_well": right["well"],
                    "same_video": bool(left["video"] == right["video"]),
                    "same_stage": bool(left["stage"] == right["stage"]),
                    "same_well": bool(left["well"] == right["well"]),
                    "distance": float(np.linalg.norm(Xz[i] - Xz[j])),
                }
            )
    return pd.DataFrame(rows)


def plot_distance_summary(pairs, out):
    plot_df = pairs.copy()
    plot_df["relationship"] = "different_well_different_video"
    plot_df.loc[plot_df["same_well"] & ~plot_df["same_video"], "relationship"] = "same_well_different_video"
    plot_df.loc[~plot_df["same_well"] & plot_df["same_video"], "relationship"] = "different_well_same_video"
    fig, ax = plt.subplots(figsize=(8.5, 4.8))
    sns.boxplot(data=plot_df, x="relationship", y="distance", ax=ax)
    ax.tick_params(axis="x", rotation=20)
    ax.set_title("Derived MV feature profile distances")
    fig.tight_layout()
    fig.savefig(out, dpi=160)
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description="Build exploratory derived MV feature profiles.")
    parser.add_argument("--input-root", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    out = pathlib.Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    rows = load_rows(args.input_root)
    sec = aggregate_one_second(rows)
    profiles = summarize_profiles(sec)
    sec.to_csv(out / "second_level_derived_features.csv", index=False)
    profiles.to_csv(out / "video_well_feature_profiles.csv", index=False)
    pairs = pairwise_profile_distances(profiles)
    pairs.to_csv(out / "profile_pairwise_distances.csv", index=False)
    plot_distance_summary(pairs, out / "profile_distance_summary.png")

    summary_rows = []
    for target in ["stage", "video", "well"]:
        result = evaluate_target(sec, target, out)
        if result is not None:
            summary_rows.append(result)
    summary = pd.DataFrame(summary_rows)
    summary.to_csv(out / "classification_summary.csv", index=False)
    notes = {
        "unit": {
            "frame_rows": "video x well x frame from all_wells_comparison.csv",
            "second_level": "video x well x one-second bin",
            "profiles": "video x well aggregated across the analyzed crop window",
            "pairwise_distances": "Euclidean distance between standardized video-well profiles",
        },
        "same_different_definition": {
            "same_well": "same well label across video-well profiles, interpreted as same plate position rather than same animal",
            "same_stage": "S-number parsed from video filename",
            "same_video": "same source video",
        },
        "warning": "Exploratory separability only; these features can reflect plate geometry, optical differences, crop artifacts, and animal behavior.",
    }
    (out / "analysis_notes.json").write_text(json.dumps(notes, indent=2), encoding="utf-8")
    print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
