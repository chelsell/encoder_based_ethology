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
    from sklearn.decomposition import PCA
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.inspection import permutation_importance
    from sklearn.metrics import accuracy_score, balanced_accuracy_score, confusion_matrix
    from sklearn.model_selection import GroupKFold, StratifiedGroupKFold, cross_val_score
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
except ModuleNotFoundError as exc:
    raise SystemExit(
        "Missing Python dependency. This script needs pandas, numpy, matplotlib, "
        "seaborn, and scikit-learn."
    ) from exc


RAW_MV_FEATURES = [
    "mean_dx_px",
    "mean_dy_px",
    "mean_magnitude_px",
    "median_magnitude_px",
    "p90_magnitude_px",
    "p95_magnitude_px",
    "max_magnitude_px",
    "sum_magnitude_px",
    "resultant_magnitude_px",
    "coherence",
]

CANDIDATE_FEATURES = ["mv_active_fraction"]


def load_rows(root):
    root = pathlib.Path(root)
    tables = []
    for path in sorted(root.glob("*/all_wells_comparison.csv")):
        video = path.parent.name
        df = pd.read_csv(path)
        df.insert(0, "video", video)
        tables.append(df)
    if not tables:
        raise SystemExit(f"No all_wells_comparison.csv files found under {root}")
    return pd.concat(tables, ignore_index=True)


def aggregate_seconds(df):
    df = df.copy()
    df["second_bin"] = np.floor(df["time_seconds"]).astype(int)
    agg_spec = {}
    for col in RAW_MV_FEATURES + CANDIDATE_FEATURES:
        agg_spec[f"{col}_mean"] = (col, "mean")
        agg_spec[f"{col}_p95"] = (col, lambda s: s.quantile(0.95))
        agg_spec[f"{col}_max"] = (col, "max")
    agg_spec["cd10_sum"] = ("cd10", "sum")
    agg_spec["cd10_active_fraction"] = ("cd10", lambda s: float((s > 0).mean()))
    return (
        df.groupby(["video", "well", "second_bin"], as_index=False)
        .agg(**agg_spec)
        .sort_values(["video", "well", "second_bin"])
        .reset_index(drop=True)
    )


def feature_sets(columns):
    raw = [c for c in columns if any(c.startswith(f"{base}_") for base in RAW_MV_FEATURES)]
    candidate = [c for c in columns if any(c.startswith(f"{base}_") for base in CANDIDATE_FEATURES)]
    return {
        "raw_mv_only": raw,
        "candidate_only": candidate,
        "raw_plus_candidate": raw + candidate,
    }


def evaluate_feature_set(data, features):
    X = data[features].replace([np.inf, -np.inf], np.nan).fillna(0.0)
    y = data["well"]
    video_groups = data["video"]
    second_groups = data["video"].astype(str) + ":" + data["second_bin"].astype(str)

    clf = RandomForestClassifier(
        n_estimators=400,
        min_samples_leaf=3,
        random_state=1,
        n_jobs=-1,
        class_weight="balanced",
    )

    within_scores = cross_val_score(
        clf,
        X,
        y,
        groups=second_groups,
        cv=StratifiedGroupKFold(n_splits=5, shuffle=True, random_state=1),
        scoring="balanced_accuracy",
        n_jobs=-1,
    )

    leave_video_rows = []
    labels = sorted(y.unique())
    combined_confusion = np.zeros((len(labels), len(labels)), dtype=int)
    for video in sorted(video_groups.unique()):
        train = video_groups != video
        test = video_groups == video
        model = clf.fit(X.loc[train], y.loc[train])
        pred = model.predict(X.loc[test])
        leave_video_rows.append(
            {
                "held_out_video": video,
                "accuracy": accuracy_score(y.loc[test], pred),
                "balanced_accuracy": balanced_accuracy_score(y.loc[test], pred),
            }
        )
        combined_confusion += confusion_matrix(y.loc[test], pred, labels=labels)

    final_model = clf.fit(X, y)
    importance = pd.DataFrame(
        {
            "feature": features,
            "importance": final_model.feature_importances_,
        }
    ).sort_values("importance", ascending=False)

    return {
        "within_cv_mean_balanced_accuracy": float(within_scores.mean()),
        "within_cv_sd_balanced_accuracy": float(within_scores.std(ddof=1)),
        "leave_video": pd.DataFrame(leave_video_rows),
        "labels": labels,
        "confusion": combined_confusion,
        "importance": importance,
    }


def plot_pca(data, features, out):
    X = data[features].replace([np.inf, -np.inf], np.nan).fillna(0.0)
    Xz = StandardScaler().fit_transform(X)
    pca = PCA(n_components=2, random_state=1)
    coords = pca.fit_transform(Xz)
    plot_df = data[["video", "well", "second_bin"]].copy()
    plot_df["PC1"] = coords[:, 0]
    plot_df["PC2"] = coords[:, 1]
    fig, ax = plt.subplots(figsize=(8, 6))
    sns.scatterplot(
        data=plot_df,
        x="PC1",
        y="PC2",
        hue="well",
        style="video",
        s=20,
        alpha=0.7,
        ax=ax,
    )
    ax.set_title(
        f"PCA of MV features, variance {pca.explained_variance_ratio_[0]:.2f} + "
        f"{pca.explained_variance_ratio_[1]:.2f}"
    )
    fig.tight_layout()
    fig.savefig(out, dpi=160)
    plt.close(fig)


def plot_confusion(confusion, labels, title, out):
    row_sums = confusion.sum(axis=1, keepdims=True)
    norm = np.divide(confusion, row_sums, out=np.zeros_like(confusion, dtype=float), where=row_sums > 0)
    fig, ax = plt.subplots(figsize=(5.5, 4.8))
    sns.heatmap(norm, annot=True, fmt=".2f", cmap="Blues", xticklabels=labels, yticklabels=labels, ax=ax)
    ax.set_xlabel("predicted well")
    ax.set_ylabel("true well")
    ax.set_title(title)
    fig.tight_layout()
    fig.savefig(out, dpi=160)
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description="Assess well distinguishability using MV-derived features.")
    parser.add_argument("--input-root", default="outputs/mv_active_cd10_multivideo")
    parser.add_argument("--output-dir", default="outputs/mv_active_cd10_multivideo/separability")
    args = parser.parse_args()

    out = pathlib.Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    rows = load_rows(args.input_root)
    sec = aggregate_seconds(rows)
    sec.to_csv(out / "second_level_features.csv", index=False)

    sets = feature_sets(sec.columns)
    summary_rows = []
    all_leave = []
    for name, features in sets.items():
        result = evaluate_feature_set(sec, features)
        summary_rows.append(
            {
                "feature_set": name,
                "n_features": len(features),
                "within_cv_mean_balanced_accuracy": result["within_cv_mean_balanced_accuracy"],
                "within_cv_sd_balanced_accuracy": result["within_cv_sd_balanced_accuracy"],
                "leave_video_mean_balanced_accuracy": result["leave_video"]["balanced_accuracy"].mean(),
                "leave_video_min_balanced_accuracy": result["leave_video"]["balanced_accuracy"].min(),
            }
        )
        leave = result["leave_video"].copy()
        leave.insert(0, "feature_set", name)
        all_leave.append(leave)
        result["importance"].to_csv(out / f"{name}.feature_importance.csv", index=False)
        plot_confusion(
            result["confusion"],
            result["labels"],
            f"{name}: leave-one-video-out confusion",
            out / f"{name}.leave_video_confusion.png",
        )
        if name == "raw_plus_candidate":
            plot_pca(sec, features, out / "raw_plus_candidate.pca.png")

    summary = pd.DataFrame(summary_rows)
    summary.to_csv(out / "separability_summary.csv", index=False)
    pd.concat(all_leave, ignore_index=True).to_csv(out / "leave_video_results.csv", index=False)
    with open(out / "analysis_notes.json", "w", encoding="utf-8") as f:
        json.dump(
            {
                "unit": "one row per video x well x one-second bin",
                "target": "well label among the selected wells",
                "chance_balanced_accuracy": 0.25,
                "warning": "Exploratory separability only; this may reflect well position, animal activity, optical differences, or crop artifacts.",
            },
            f,
            indent=2,
        )
    print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
