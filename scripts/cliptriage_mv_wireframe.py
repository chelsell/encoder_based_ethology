#!/usr/bin/env python3
import argparse
import html
import json
import os
import pathlib
import subprocess
import sys
from types import SimpleNamespace

os.environ.setdefault("MPLCONFIGDIR", "/tmp/mestimate-sidecar-matplotlib")

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from integrate_mestimate_sidecar import integrate_sidecar  # noqa: E402

try:
    import matplotlib.pyplot as plt
    import pandas as pd
except ModuleNotFoundError as exc:
    raise SystemExit("Missing Python dependency. This script needs pandas and matplotlib.") from exc


FEATURE_COLUMNS = [
    "analysis_n_vectors_median",
    "mvaf_mean",
    "mvaf_peak",
    "mv_bout_fraction_mean",
    "mv_bout_fraction_peak",
    "mv_active_frame_fraction",
    "analysis_motion_energy_sum",
    "p95_magnitude_px_peak",
    "spatial_entropy_mean",
    "active_spatial_bin_fraction_peak",
]


def run(cmd):
    print("+", " ".join(str(part) for part in cmd), flush=True)
    subprocess.run(cmd, check=True)


def resolve_asset_path(asset_path, clip_root):
    path = pathlib.Path(str(asset_path))
    if path.is_absolute():
        return path
    return pathlib.Path(clip_root) / path


def sidecar_paths(sidecar_dir, video_path):
    stem = pathlib.Path(video_path).stem
    return {
        "frames": sidecar_dir / f"{stem}.mestimate-v1.frames.csv.gz",
        "vectors": sidecar_dir / f"{stem}.mestimate-v1.vectors.csv.gz",
        "metadata": sidecar_dir / f"{stem}.mestimate-v1.metadata.json",
    }


def ensure_sidecar(row, video_path, sidecar_dir, sidecar_bin, force):
    paths = sidecar_paths(sidecar_dir, video_path)
    if paths["frames"].exists() and paths["vectors"].exists() and paths["metadata"].exists() and not force:
        return paths
    sidecar_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        sidecar_bin,
        "--input",
        str(video_path),
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
    return paths


def integrate_one(paths, derived_dir, vector_source, bin_ms, force):
    expected = derived_dir / f"preview.mv-features-v1.bin-{bin_ms}ms.csv.gz"
    if expected.exists() and not force:
        return expected
    opts = SimpleNamespace(
        frames=str(paths["frames"]),
        vectors=str(paths["vectors"]),
        metadata=str(paths["metadata"]),
        output_dir=str(derived_dir),
        bin_ms=[bin_ms],
        vector_source=vector_source,
        active_vector_threshold=0.0,
        min_active_blocks_per_frame=2,
        min_active_run_frames=2,
        grid_rows=4,
        grid_cols=4,
        capped_active_vectors=6,
    )
    integrate_sidecar(opts)
    return expected


def summarize_bins(bin_path):
    bins = pd.read_csv(bin_path)
    row = {"n_feature_bins": int(len(bins))}
    for col in FEATURE_COLUMNS:
        if col in bins.columns:
            row[f"{col}_mean"] = float(bins[col].mean())
            row[f"{col}_max"] = float(bins[col].max())
    return row


def label_order(df):
    counts = df["primary_label_path"].value_counts()
    return list(counts.index)


def plot_label_counts(df, out):
    counts = df["primary_label_path"].value_counts().sort_values()
    fig, ax = plt.subplots(figsize=(9, max(3.5, 0.45 * len(counts))))
    ax.barh(counts.index, counts.values, color="#5b8db8")
    ax.set_xlabel("clips")
    ax.set_ylabel("")
    ax.set_title("Annotation labels in wireframe set")
    fig.tight_layout()
    fig.savefig(out, dpi=160)
    plt.close(fig)


def plot_feature_by_label(df, feature, out):
    order = label_order(df)
    fig, ax = plt.subplots(figsize=(10, max(4.0, 0.55 * len(order))))
    positions = {label: i for i, label in enumerate(order)}
    for label, group in df.groupby("primary_label_path", sort=False):
        y = [positions[label]] * len(group)
        ax.scatter(group[feature], y, alpha=0.82, s=42)
        if len(group):
            ax.scatter([group[feature].median()], [positions[label]], marker="|", s=420, color="black")
    ax.set_yticks(range(len(order)))
    ax.set_yticklabels(order)
    ax.set_xlabel(feature)
    ax.set_ylabel("")
    ax.set_title(f"{feature} by annotation label")
    ax.grid(axis="x", alpha=0.2)
    fig.tight_layout()
    fig.savefig(out, dpi=160)
    plt.close(fig)


def write_html(df, out, plots, metadata):
    rows = []
    for row in df.sort_values(["primary_label_path", "run_tag", "well_label"]).to_dict("records"):
        clip = html.escape(str(row["clip_id"]))
        asset = pathlib.Path(str(row["resolved_asset_path"])).resolve()
        rows.append(
            "<tr>"
            f"<td>{clip}</td>"
            f"<td>{html.escape(str(row.get('well_label', '')))}</td>"
            f"<td>{html.escape(str(row.get('run_tag', '')))}</td>"
            f"<td>{html.escape(str(row.get('primary_label_path', '')))}</td>"
            f"<td>{float(row.get('mvaf_peak_mean', 0.0)):.4g}</td>"
            f"<td>{float(row.get('mv_bout_fraction_peak_mean', 0.0)):.4g}</td>"
            f"<td>{float(row.get('analysis_motion_energy_sum_mean', 0.0)):.4g}</td>"
            f"<td><a href=\"{asset.as_uri()}\">preview</a></td>"
            "</tr>"
        )
    plot_tags = "\n".join(
        f"<figure><img src=\"{path.name}\" style=\"max-width: 100%\"><figcaption>{html.escape(path.name)}</figcaption></figure>"
        for path in plots
    )
    body = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Cliptriage MV Wireframe</title>
  <style>
    body {{ font-family: sans-serif; margin: 24px; }}
    table {{ border-collapse: collapse; font-size: 13px; width: 100%; }}
    th, td {{ border-bottom: 1px solid #ddd; padding: 6px 8px; text-align: left; vertical-align: top; }}
    th {{ background: #f4f4f4; position: sticky; top: 0; }}
    code, pre {{ background: #f7f7f7; padding: 2px 4px; }}
  </style>
</head>
<body>
  <h1>Cliptriage MV Wireframe</h1>
  <p>This report runs mestimate-derived summaries on rendered annotation preview clips. It is a wiring check, not source-domain validation.</p>
  <pre>{html.escape(json.dumps(metadata, indent=2))}</pre>
  {plot_tags}
  <h2>Clip Rows</h2>
  <table>
    <thead><tr><th>clip</th><th>well</th><th>run</th><th>label</th><th>mvaf peak mean</th><th>mv bout peak mean</th><th>MV energy mean</th><th>clip</th></tr></thead>
    <tbody>
      {''.join(rows)}
    </tbody>
  </table>
</body>
</html>
"""
    out.write_text(body, encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="First-pass MV feature wireframe over cliptriage-labeled preview clips.")
    parser.add_argument("--annotations", required=True, help="cliptriage_current_annotations.csv")
    parser.add_argument("--clip-root", default="/home/cole/code/well_annotation")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--sidecar-bin", default="./build/mestimate-sidecar")
    parser.add_argument("--vector-source", choices=["all", "past", "future"], default="past")
    parser.add_argument("--bin-ms", type=int, default=100)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    out = pathlib.Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    annotations = pd.read_csv(args.annotations).fillna("")
    feature_rows = []
    for row in annotations.to_dict("records"):
        clip_id = str(row["clip_id"])
        video_path = resolve_asset_path(row["asset_path"], args.clip_root)
        if not video_path.exists():
            raise FileNotFoundError(video_path)
        rel = pathlib.Path(clip_id[:2]) / clip_id
        sidecar_dir = out / "sidecars" / rel
        derived_dir = out / "derived" / rel
        paths = ensure_sidecar(row, video_path, sidecar_dir, args.sidecar_bin, args.force)
        bin_path = integrate_one(paths, derived_dir, args.vector_source, args.bin_ms, args.force)
        summary = summarize_bins(bin_path)
        summary.update(row)
        summary["resolved_asset_path"] = str(video_path)
        summary["sidecar_dir"] = str(sidecar_dir)
        summary["derived_dir"] = str(derived_dir)
        feature_rows.append(summary)

    features = pd.DataFrame(feature_rows)
    features.to_csv(out / "clip_label_mv_features.csv", index=False)
    group = (
        features.groupby("primary_label_path", dropna=False)
        .agg(
            n_clips=("clip_id", "size"),
            analysis_n_vectors_median_mean=("analysis_n_vectors_median_mean", "median"),
            mvaf_peak_mean_median=("mvaf_peak_mean", "median"),
            mv_bout_fraction_peak_mean_median=("mv_bout_fraction_peak_mean", "median"),
            analysis_motion_energy_sum_mean_median=("analysis_motion_energy_sum_mean", "median"),
            p95_magnitude_px_peak_max_median=("p95_magnitude_px_peak_max", "median"),
        )
        .reset_index()
        .sort_values(["n_clips", "primary_label_path"], ascending=[False, True])
    )
    group.to_csv(out / "label_feature_summary.csv", index=False)
    plots = []
    label_counts = out / "label_counts.png"
    plot_label_counts(features, label_counts)
    plots.append(label_counts)
    for feature in ["mvaf_peak_mean", "mv_bout_fraction_peak_mean", "analysis_motion_energy_sum_mean"]:
        plot_path = out / f"{feature}_by_label.png"
        plot_feature_by_label(features, feature, plot_path)
        plots.append(plot_path)
    metadata = {
        "input_annotations": str(pathlib.Path(args.annotations).resolve()),
        "clip_root": str(pathlib.Path(args.clip_root).resolve()),
        "output_dir": str(out.resolve()),
        "n_clips": int(len(features)),
        "n_labels": int(features["primary_label_path"].nunique()),
        "bin_ms": args.bin_ms,
        "vector_source": args.vector_source,
        "unit_of_observation": "one rendered annotation preview clip",
        "warning": "Wireframe only: features are computed from rendered H.264 preview clips, not historical source-domain video.",
    }
    (out / "wireframe_metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    write_html(features, out / "wireframe_report.html", plots, metadata)
    print(json.dumps(metadata, indent=2))
    print(group.to_string(index=False))


if __name__ == "__main__":
    main()
