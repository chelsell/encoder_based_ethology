#!/usr/bin/env python3
import argparse
import csv
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import time


def str_to_bool(value):
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def load_task(manifest, task_index):
    with open(manifest, "r", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if task_index < 1 or task_index > len(rows):
        raise SystemExit(f"task index {task_index} is outside manifest range 1-{len(rows)}")
    return rows[task_index - 1], len(rows)


def default_task_index():
    value = os.environ.get("SLURM_ARRAY_TASK_ID")
    if not value:
        raise SystemExit("Pass --task-index or run under a Slurm array with SLURM_ARRAY_TASK_ID set.")
    return int(value)


def unique_paths(paths):
    seen = set()
    out = []
    for path in paths:
        p = str(pathlib.Path(path).resolve())
        if p not in seen:
            out.append(p)
            seen.add(p)
    return out


def auto_bind_paths(input_path, output_dir, extra_binds):
    paths = [
        pathlib.Path(input_path).resolve().parent,
        pathlib.Path(output_dir).resolve(),
    ]
    paths.extend(extra_binds)
    return unique_paths(paths)


def build_command(args, row):
    input_path = pathlib.Path(row["input_path"]).resolve()
    output_dir = pathlib.Path(row["output_dir"]).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    binds = auto_bind_paths(input_path, output_dir, args.bind)
    cmd = [args.apptainer_bin, "run"]
    if args.cleanenv:
        cmd.append("--cleanenv")
    for bind in binds:
        cmd.extend(["--bind", f"{bind}:{bind}"])
    cmd.extend(
        [
            str(pathlib.Path(args.image).resolve()),
            "--input",
            str(input_path),
            "--output-dir",
            str(output_dir),
            "--method",
            row.get("method") or args.method,
            "--mb-size",
            str(row.get("mb_size") or args.mb_size),
            "--search-param",
            str(row.get("search_param") or args.search_param),
            "--frame-diff-threshold",
            str(row.get("frame_diff_threshold") or args.frame_diff_threshold),
            "--frame-output",
            row.get("frame_output") or args.frame_output,
            "--summary-float-precision",
            str(row.get("summary_float_precision") or args.summary_float_precision),
            "--vector-output",
            row.get("vector_output") or args.vector_output,
            "--vector-format",
            row.get("vector_format") or args.vector_format,
            "--vector-source",
            row.get("vector_source") or args.vector_source,
            "--vector-frame-stride",
            str(row.get("vector_frame_stride") or args.vector_frame_stride),
            "--vector-spatial-stride",
            str(row.get("vector_spatial_stride") or args.vector_spatial_stride),
            "--vector-min-magnitude",
            str(row.get("vector_min_magnitude") or args.vector_min_magnitude),
        ]
    )
    if str_to_bool(row.get("force", args.force)):
        cmd.append("--force")
    return cmd, binds


def sha256_file(path):
    path = pathlib.Path(path)
    if not path.exists() or not path.is_file():
        return ""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write_task_log(path, payload):
    pathlib.Path(path).parent.mkdir(parents=True, exist_ok=True)
    pathlib.Path(path).write_text(json.dumps(payload, indent=2), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="Run one sidecar extraction task from a Slurm manifest.")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--task-index", type=int, default=None)
    parser.add_argument("--image", required=True, help="Apptainer SIF or sandbox containing mestimate-sidecar.")
    parser.add_argument("--apptainer-bin", default=os.environ.get("APPTAINER_BIN", "apptainer"))
    parser.add_argument("--bind", action="append", default=[], help="Additional host path to bind to the same path inside the container.")
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
    parser.add_argument("--cleanenv", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    task_index = args.task_index if args.task_index is not None else default_task_index()
    row, task_count = load_task(args.manifest, task_index)
    cmd, binds = build_command(args, row)
    log_path = pathlib.Path(row["output_dir"]) / "sidecar_slurm_task.json"
    payload = {
        "manifest": str(pathlib.Path(args.manifest).resolve()),
        "task_index": task_index,
        "task_count": task_count,
        "row": row,
        "image": str(pathlib.Path(args.image).resolve()),
        "image_sha256": sha256_file(args.image),
        "binds": binds,
        "command": cmd,
        "slurm": {
            "job_id": os.environ.get("SLURM_JOB_ID"),
            "array_job_id": os.environ.get("SLURM_ARRAY_JOB_ID"),
            "array_task_id": os.environ.get("SLURM_ARRAY_TASK_ID"),
            "submit_dir": os.environ.get("SLURM_SUBMIT_DIR"),
        },
    }
    if args.dry_run:
        payload["status"] = "dry_run"
        write_task_log(log_path, payload)
        print(" ".join(cmd))
        return

    started = time.monotonic()
    completed = subprocess.run(cmd, check=False)
    payload["elapsed_seconds"] = round(time.monotonic() - started, 6)
    payload["returncode"] = completed.returncode
    payload["status"] = "ok" if completed.returncode == 0 else "failed"
    write_task_log(log_path, payload)
    if completed.returncode != 0:
        sys.exit(completed.returncode)


if __name__ == "__main__":
    main()
