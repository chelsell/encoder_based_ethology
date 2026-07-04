#!/usr/bin/env python3
import argparse
import csv
import pathlib


VIDEO_EXTENSIONS = {".avi", ".mkv", ".mov", ".mp4", ".webm"}


def read_input_file(path):
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            rows.append(pathlib.Path(line))
    return rows


def discover_inputs(input_root, pattern):
    root = pathlib.Path(input_root)
    return sorted(path for path in root.rglob(pattern) if path.suffix.lower() in VIDEO_EXTENSIONS)


def output_dir_for(input_path, input_root, output_root, preserve_layout):
    input_path = pathlib.Path(input_path).resolve()
    output_root = pathlib.Path(output_root)
    if preserve_layout and input_root:
        root = pathlib.Path(input_root).resolve()
        try:
            rel_parent = input_path.parent.relative_to(root)
        except ValueError:
            rel_parent = pathlib.Path()
        return output_root / rel_parent / input_path.stem
    return output_root / input_path.stem


def make_rows(inputs, input_root, output_root, args):
    rows = []
    for task_id, input_path in enumerate(inputs, start=1):
        rows.append(
            {
                "task_id": task_id,
                "input_path": str(pathlib.Path(input_path).resolve()),
                "output_dir": str(output_dir_for(input_path, input_root, output_root, args.preserve_layout).resolve()),
                "method": args.method,
                "mb_size": args.mb_size,
                "search_param": args.search_param,
                "frame_diff_threshold": args.frame_diff_threshold,
                "frame_output": args.frame_output,
                "summary_float_precision": args.summary_float_precision,
                "vector_output": args.vector_output,
                "vector_format": args.vector_format,
                "vector_source": args.vector_source,
                "vector_frame_stride": args.vector_frame_stride,
                "vector_spatial_stride": args.vector_spatial_stride,
                "vector_min_magnitude": args.vector_min_magnitude,
                "force": int(args.force),
            }
        )
    return rows


def write_manifest(path, rows):
    fieldnames = [
        "task_id",
        "input_path",
        "output_dir",
        "method",
        "mb_size",
        "search_param",
        "frame_diff_threshold",
        "frame_output",
        "summary_float_precision",
        "vector_output",
        "vector_format",
        "vector_source",
        "vector_frame_stride",
        "vector_spatial_stride",
        "vector_min_magnitude",
        "force",
    ]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description="Create a Slurm array manifest for containerized sidecar extraction.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--input-root", help="Directory to scan recursively for videos.")
    source.add_argument("--input-file", help="Text file containing one video path per line.")
    parser.add_argument("--pattern", default="*", help="rglob pattern used with --input-root.")
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--method", default="epzs")
    parser.add_argument("--mb-size", type=int, default=16)
    parser.add_argument("--search-param", type=int, default=12)
    parser.add_argument("--frame-diff-threshold", type=int, default=10)
    parser.add_argument("--frame-output", choices=["csv", "bin"], default="csv")
    parser.add_argument("--summary-float-precision", type=int, default=6)
    parser.add_argument("--vector-output", choices=["all", "sampled", "none"], default="none")
    parser.add_argument("--vector-format", choices=["csv", "bin"], default="bin")
    parser.add_argument("--vector-source", choices=["all", "past", "future"], default="all")
    parser.add_argument("--vector-frame-stride", type=int, default=1)
    parser.add_argument("--vector-spatial-stride", type=int, default=1)
    parser.add_argument("--vector-min-magnitude", type=float, default=0.0)
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--preserve-layout",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Preserve input subdirectories under --output-root.",
    )
    args = parser.parse_args()

    if args.input_file:
        inputs = read_input_file(args.input_file)
        input_root = None
    else:
        inputs = discover_inputs(args.input_root, args.pattern)
        input_root = args.input_root
    rows = make_rows(inputs, input_root, args.output_root, args)
    pathlib.Path(args.manifest).parent.mkdir(parents=True, exist_ok=True)
    write_manifest(args.manifest, rows)
    print(f"wrote {len(rows)} tasks to {args.manifest}")
    if rows:
        print(f"submit with --array=1-{len(rows)}")


if __name__ == "__main__":
    main()
