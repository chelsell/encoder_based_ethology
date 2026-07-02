#!/usr/bin/env python3
import argparse
import csv
import gzip
import json
import statistics


VECTOR_COLUMNS = [
    "frame_index",
    "pts",
    "time_seconds",
    "vector_index",
    "source",
    "w",
    "h",
    "src_x",
    "src_y",
    "dst_x",
    "dst_y",
    "motion_x",
    "motion_y",
    "motion_scale",
    "flags",
    "dx_px",
    "dy_px",
    "magnitude_px",
]

FRAME_COLUMNS = [
    "frame_index",
    "pts",
    "time_seconds",
    "n_vectors",
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


def read_gz_csv(path):
    with gzip.open(path, "rt", newline="") as f:
        reader = csv.DictReader(f)
        return reader.fieldnames, list(reader)


def median_sum(rows, start, stop):
    vals = [float(r["sum_magnitude_px"]) for r in rows if start <= int(r["frame_index"]) <= stop]
    return statistics.median(vals)


def mean_axis(rows, start, stop, column):
    vals = [float(r[column]) for r in rows if start <= int(r["frame_index"]) <= stop and int(r["n_vectors"]) > 0]
    return statistics.mean(vals) if vals else 0.0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--frames", required=True)
    parser.add_argument("--vectors", required=True)
    parser.add_argument("--metadata", required=True)
    parser.add_argument("--expected-frames", type=int, default=50)
    args = parser.parse_args()

    frame_columns, frames = read_gz_csv(args.frames)
    vector_columns, vectors = read_gz_csv(args.vectors)

    assert frame_columns == FRAME_COLUMNS, frame_columns
    assert vector_columns == VECTOR_COLUMNS, vector_columns
    assert len(frames) == args.expected_frames, len(frames)
    assert len(vectors) > 0, "expected at least one vector row"
    assert sum(int(r["n_vectors"]) for r in frames) == len(vectors)

    static_before = median_sum(frames, 0, 8)
    horizontal = median_sum(frames, 12, 20)
    static_middle = median_sum(frames, 22, 28)
    vertical = median_sum(frames, 32, 40)

    assert horizontal > static_before, (horizontal, static_before)
    assert vertical > static_before, (vertical, static_before)
    assert horizontal > static_middle, (horizontal, static_middle)
    assert vertical > static_middle, (vertical, static_middle)

    horizontal_dx = mean_axis(frames, 12, 20, "mean_dx_px")
    vertical_dy = mean_axis(frames, 32, 40, "mean_dy_px")
    print(f"observed_horizontal_mean_dx_px={horizontal_dx:.6f}")
    print(f"observed_vertical_mean_dy_px={vertical_dy:.6f}")

    with open(args.metadata, "r", encoding="utf-8") as f:
        metadata = json.load(f)
    assert metadata["schema_name"] == "mestimate-sidecar"
    assert metadata["schema_version"] == "v1"
    assert metadata["outputs"]["frame_row_count"] == len(frames)
    assert metadata["outputs"]["vector_row_count"] == len(vectors)


if __name__ == "__main__":
    main()
