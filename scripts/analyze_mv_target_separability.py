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
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.metrics import balanced_accuracy_score, confusion_matrix
    from sklearn.model_selection import StratifiedGroupKFold, cross_val_score
except ModuleNotFoundError as exc:
    raise SystemExit(
        "Missing Python dependency. This script needs pandas, numpy, "
        "matplotlib, seaborn, and scikit-learn."
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


def target_config(target, data):
    if target == "well":
        return {
            "target_col": "well",
            "nuisance_group_col": "video:second_bin",
            "chance": 1.0 / max(1, data["well"].nunique()),
        }
    if target == "video":
        return {
            "target_col": "video",
            "nuisance_group_col": "well:second_bin",
            "chance": 1.0 / max(1, data["video"].nunique()),
        }
    raise SystemExit(f"Unknown target: {target}")


def evaluate_feature_set(data, features, target_col, nuisance_group_col):
    X = data[features].replace([np.inf, -np.inf], np.nan).fillna(0.0)
    y = data[target_col].astype(str)
    if nuisance_group_col == "video:second_bin":
        groups = data["video"].astype(str) + ":" + data["second_bin"].astype(str)
    elif nuisance_group_col == "well:second_bin":
        groups = data["well"].astype(str) + ":" + data["second_bin"].astype(str)
    else:
        groups = data[nuisance_group_col].astype(str)

    clf = RandomForestClassifier(
        n_estimators=400,
        min_samples_leaf=3,
        random_state=1,
        n_jobs=-1,
        class_weight="balanced",
    )

    cv = StratifiedGroupKFold(n_splits=5, shuffle=True, random_state=1)
    scores = cross_val_score(
        clf,
        X,
        y,
        groups=groups,
        cv=cv,
        scoring="balanced_accuracy",
        n_jobs=-1,
    )

    labels = sorted(y.unique())
    final_model = clf.fit(X, y)
    importance = pd.DataFrame(
        {
            "feature": features,
            "importance": final_model.feature_importances_,
        }
    ).sort_values("importance", ascending=False)
    pred = final_model.predict(X)
    confusion = confusion_matrix(y, pred, labels=labels)

    return {
        "within_cv_mean_balanced_accuracy": float(scores.mean()),
        "within_cv_sd_balanced_accuracy": float(scores.std(ddof=1)),
        "labels": labels,
        "confusion": confusion,
        "importance": importance,
    }


def plot_confusion(confusion, labels, title, out):
    row_sums = confusion.sum(axis=1, keepdims=True)
    norm = np.divide(confusion, row_sums, out=np.zeros_like(confusion, dtype=float), where=row_sums > 0)
    fig, ax = plt.subplots(figsize=(6.0, 5.0))
    sns.heatmap(norm, annot=False, cmap="Blues", xticklabels=labels, yticklabels=labels, ax=ax)
    ax.set_xlabel("predicted")
    ax.set_ylabel("true")
    ax.set_title(title)
    fig.tight_layout()
    fig.savefig(out, dpi=160)
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description="Compare MV feature separability across well and video targets.")
    parser.add_argument("--input-root", default="outputs/mv_active_cd10_multivideo_expanded_20260701")
    parser.add_argument("--output-dir", default="outputs/mv_active_cd10_multivideo_expanded_20260701/separability_targets")
    parser.add_argument("--targets", nargs="+", default=["well", "video"], choices=["well", "video"])
    args = parser.parse_args()

    out = pathlib.Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    rows = load_rows(args.input_root)
    sec = aggregate_seconds(rows)
    sec.to_csv(out / "second_level_features.csv", index=False)

    sets = feature_sets(sec.columns)
    summary_rows = []
    notes = {
        "unit": "one row per video x well x one-second bin",
        "warning": "Exploratory separability only; this can reflect plate geometry, optical differences, or crop artifacts.",
        "targets": {},
    }

    for target in args.targets:
        cfg = target_config(target, sec)
        notes["targets"][target] = {
            "target_col": cfg["target_col"],
            "nuisance_group_col": cfg["nuisance_group_col"],
            "chance_balanced_accuracy": cfg["chance"],
            "n_classes": int(sec[cfg["target_col"]].nunique()),
        }
        for name, features in sets.items():
            result = evaluate_feature_set(sec, features, cfg["target_col"], cfg["nuisance_group_col"])
            summary_rows.append(
                {
                    "target": target,
                    "feature_set": name,
                    "n_features": len(features),
                    "within_cv_mean_balanced_accuracy": result["within_cv_mean_balanced_accuracy"],
                    "within_cv_sd_balanced_accuracy": result["within_cv_sd_balanced_accuracy"],
                }
            )
            result["importance"].to_csv(out / f"{target}.{name}.feature_importance.csv", index=False)
            plot_confusion(
                result["confusion"],
                result["labels"],
                f"{target} target: {name}",
                out / f"{target}.{name}.confusion.png",
            )

    pd.DataFrame(summary_rows).to_csv(out / "separability_summary.csv", index=False)
    with open(out / "analysis_notes.json", "w", encoding="utf-8") as f:
        json.dump(notes, f, indent=2)
    print(pd.DataFrame(summary_rows).to_string(index=False))


if __name__ == "__main__":
    main()
