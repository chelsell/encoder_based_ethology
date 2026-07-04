#!/usr/bin/env python3
import argparse
import csv
import gzip
import json
import statistics
import struct


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
    "frame_diff_threshold",
    "frame_diff_changed_pixels",
    "frame_diff_changed_fraction",
    "frame_diff_abs_sum",
    "frame_diff_abs_mean",
]


def read_gz_csv(path):
    with gzip.open(path, "rt", newline="") as f:
        reader = csv.DictReader(f)
        return reader.fieldnames, list(reader)


def read_gz_binary_frames(path):
    record_struct = struct.Struct("<qqfi10fiqfqf")
    with gzip.open(path, "rb") as f:
        header = f.read(32)
        magic, version, endian, header_size, record_size, field_count, reserved = struct.unpack("<8s6I", header)
        assert magic == b"MSCFB1\x00\x00"
        assert version == 1
        assert endian == 0x01020304
        assert header_size == 32
        assert record_size == record_struct.size
        assert field_count == len(FRAME_COLUMNS)
        assert reserved == 0
        payload = f.read()
    assert len(payload) % record_struct.size == 0
    rows = []
    for values in record_struct.iter_unpack(payload):
        row = dict(zip(FRAME_COLUMNS, values))
        rows.append({k: str(v) for k, v in row.items()})
    return FRAME_COLUMNS, rows


def read_gz_binary_vectors(path):
    record_struct = struct.Struct("<qqfiiIIhhhhiiIQiif")
    with gzip.open(path, "rb") as f:
        header = f.read(32)
        magic, version, endian, header_size, record_size, field_count, reserved = struct.unpack("<8s6I", header)
        assert magic == b"MSCVB1\x00\x00"
        assert version == 1
        assert endian == 0x01020304
        assert header_size == 32
        assert record_size == record_struct.size
        assert field_count == len(VECTOR_COLUMNS)
        assert reserved == 0
        payload = f.read()
    assert len(payload) % record_struct.size == 0
    rows = []
    for values in record_struct.iter_unpack(payload):
        row = dict(zip(VECTOR_COLUMNS, values))
        rows.append({k: str(v) for k, v in row.items()})
    return VECTOR_COLUMNS, rows


def read_frames(path):
    if path.endswith(".bin.gz"):
        return read_gz_binary_frames(path)
    return read_gz_csv(path)


def read_vectors(path):
    if path.endswith(".bin.gz"):
        return read_gz_binary_vectors(path)
    return read_gz_csv(path)


def median_sum(rows, start, stop):
    vals = [float(r["sum_magnitude_px"]) for r in rows if start <= int(r["frame_index"]) <= stop]
    return statistics.median(vals)


def median_float(rows, start, stop, column):
    vals = [float(r[column]) for r in rows if start <= int(r["frame_index"]) <= stop]
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
    parser.add_argument("--allow-sampled-vectors", action="store_true")
    args = parser.parse_args()

    frame_columns, frames = read_frames(args.frames)
    vector_columns, vectors = read_vectors(args.vectors)

    assert frame_columns == FRAME_COLUMNS, frame_columns
    assert vector_columns == VECTOR_COLUMNS, vector_columns
    assert len(frames) == args.expected_frames, len(frames)
    assert len(vectors) > 0, "expected at least one vector row"
    raw_vector_count = sum(int(r["n_vectors"]) for r in frames)
    if args.allow_sampled_vectors:
        assert raw_vector_count >= len(vectors)
    else:
        assert raw_vector_count == len(vectors)

    static_before = median_sum(frames, 0, 8)
    horizontal = median_sum(frames, 12, 20)
    static_middle = median_sum(frames, 22, 28)
    vertical = median_sum(frames, 32, 40)

    assert horizontal > static_before, (horizontal, static_before)
    assert vertical > static_before, (vertical, static_before)
    assert horizontal > static_middle, (horizontal, static_middle)
    assert vertical > static_middle, (vertical, static_middle)
    assert int(frames[0]["frame_diff_changed_pixels"]) == 0
    assert {int(r["frame_diff_threshold"]) for r in frames} == {10}
    horizontal_cd = median_float(frames, 12, 20, "frame_diff_changed_pixels")
    vertical_cd = median_float(frames, 32, 40, "frame_diff_changed_pixels")
    static_cd = median_float(frames, 0, 8, "frame_diff_changed_pixels")
    assert horizontal_cd > static_cd, (horizontal_cd, static_cd)
    assert vertical_cd > static_cd, (vertical_cd, static_cd)

    horizontal_dx = mean_axis(frames, 12, 20, "mean_dx_px")
    vertical_dy = mean_axis(frames, 32, 40, "mean_dy_px")
    print(f"observed_horizontal_mean_dx_px={horizontal_dx:.6f}")
    print(f"observed_vertical_mean_dy_px={vertical_dy:.6f}")

    with open(args.metadata, "r", encoding="utf-8") as f:
        metadata = json.load(f)
    assert metadata["schema_name"] == "mestimate-sidecar"
    assert metadata["schema_version"] == "v1"
    assert metadata["outputs"]["frame_row_count"] == len(frames)
    if metadata["outputs"].get("vectors_file") is not None:
        assert metadata["outputs"]["vector_row_count"] == len(vectors)
    assert metadata["outputs"].get("raw_vector_row_count", len(vectors)) == raw_vector_count
    assert metadata["image_dynamics"]["frame_difference_lag_frames"] == 1
    assert metadata["image_dynamics"]["frame_difference_threshold"] == 10
    assert metadata["frame_summary_encoding"]["float_significant_digits"] == 6
    assert metadata["vector_encoding"]["format"] in {"csv.gz", "bin.gz"}
    if args.vectors.endswith(".bin.gz"):
        assert metadata["vector_encoding"]["format"] == "bin.gz"
        assert metadata["vector_encoding"]["binary_header_size"] == 32
        assert metadata["vector_encoding"]["binary_record_size"] == 76
    else:
        assert metadata["vector_encoding"]["format"] == "csv.gz"


if __name__ == "__main__":
    main()
