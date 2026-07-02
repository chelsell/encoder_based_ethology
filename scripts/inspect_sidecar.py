#!/usr/bin/env python3
import argparse
import json
import os
import pathlib

os.environ.setdefault("MPLCONFIGDIR", "/tmp/mestimate-sidecar-matplotlib")

try:
    import matplotlib.pyplot as plt
    import pandas as pd
except ModuleNotFoundError as exc:
    raise SystemExit(
        "Missing Python plotting dependency. Install the optional inspection "
        "packages from requirements-inspect.txt."
    ) from exc


def read_csv(path):
    return pd.read_csv(path, compression="gzip")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--frames", required=True)
    parser.add_argument("--vectors", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--metadata", default=None)
    args = parser.parse_args()

    out = pathlib.Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)

    frames = read_csv(args.frames)
    vectors = read_csv(args.vectors)

    fig, axes = plt.subplots(4, 1, figsize=(10, 8), sharex=True)
    axes[0].plot(frames["frame_index"], frames["sum_magnitude_px"])
    axes[0].set_ylabel("sum mag")
    axes[1].plot(frames["frame_index"], frames["p95_magnitude_px"])
    axes[1].set_ylabel("p95 mag")
    axes[2].plot(frames["frame_index"], frames["coherence"])
    axes[2].set_ylabel("coherence")
    axes[3].plot(frames["frame_index"], frames["n_vectors"])
    axes[3].set_ylabel("vectors")
    axes[3].set_xlabel("frame")
    fig.tight_layout()
    fig.savefig(out / "frame_summary.png", dpi=160)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(8, 5))
    vectors["magnitude_px"].hist(ax=ax, bins=60)
    ax.set_xlabel("vector magnitude px")
    ax.set_ylabel("count")
    fig.tight_layout()
    fig.savefig(out / "magnitude_histogram.png", dpi=160)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(8, 5))
    frames["coherence"].hist(ax=ax, bins=60)
    ax.set_xlabel("frame coherence")
    ax.set_ylabel("count")
    fig.tight_layout()
    fig.savefig(out / "coherence_histogram.png", dpi=160)
    plt.close(fig)

    top = frames.sort_values("sum_magnitude_px", ascending=False).head(25)
    top.to_csv(out / "top_motion_frames.tsv", sep="\t", index=False)

    metadata = {}
    if args.metadata:
        with open(args.metadata, "r", encoding="utf-8") as f:
            metadata = json.load(f)

    summary = {
        "number_of_frames": int(len(frames)),
        "number_of_vector_rows": int(len(vectors)),
        "median_vectors_per_frame": float(frames["n_vectors"].median()),
        "median_frame_sum_magnitude_px": float(frames["sum_magnitude_px"].median()),
        "p95_frame_sum_magnitude_px": float(frames["sum_magnitude_px"].quantile(0.95)),
        "fraction_frames_with_zero_vectors": float((frames["n_vectors"] == 0).mean()),
        "filtergraph": metadata.get("filtergraph"),
    }
    with open(out / "summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)


if __name__ == "__main__":
    main()
