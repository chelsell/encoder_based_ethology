#!/usr/bin/env python3
import argparse
import csv
import json
import pathlib
import time
from dataclasses import dataclass

from integrate_mestimate_sidecar import default_options, integrate_sidecar


FRAME_SUFFIX = ".mestimate-v1.frames.csv.gz"
VECTOR_SUFFIXES = [".mestimate-v1.vectors.csv.gz", ".mestimate-v1.vectors.bin.gz"]
METADATA_SUFFIX = ".mestimate-v1.metadata.json"


@dataclass(frozen=True)
class SidecarSet:
    stem: str
    sidecar_dir: pathlib.Path
    frames: pathlib.Path
    vectors: pathlib.Path
    metadata: pathlib.Path | None


def sidecar_stem_from_frames(path):
    name = pathlib.Path(path).name
    if not name.endswith(FRAME_SUFFIX):
        raise ValueError(f"not a sidecar frame table: {path}")
    return name[: -len(FRAME_SUFFIX)]


def discover_sidecars(input_root):
    root = pathlib.Path(input_root)
    sidecars = []
    missing = []
    for frames in sorted(root.rglob(f"*{FRAME_SUFFIX}")):
        stem = sidecar_stem_from_frames(frames)
        vectors = next((frames.with_name(f"{stem}{suffix}") for suffix in VECTOR_SUFFIXES if frames.with_name(f"{stem}{suffix}").exists()), None)
        metadata = frames.with_name(f"{stem}{METADATA_SUFFIX}")
        if vectors is None:
            missing.append(
                {
                    "stem": stem,
                    "sidecar_dir": str(frames.parent),
                    "frames": str(frames),
                    "missing": "vectors",
                    "expected": " or ".join(str(frames.with_name(f"{stem}{suffix}")) for suffix in VECTOR_SUFFIXES),
                }
            )
            continue
        sidecars.append(
            SidecarSet(
                stem=stem,
                sidecar_dir=frames.parent,
                frames=frames,
                vectors=vectors,
                metadata=metadata if metadata.exists() else None,
            )
        )
    return sidecars, missing


def relative_output_dir(input_root, output_root, sidecar_dir):
    input_root = pathlib.Path(input_root).resolve()
    output_root = pathlib.Path(output_root)
    sidecar_dir = pathlib.Path(sidecar_dir).resolve()
    try:
        rel = sidecar_dir.relative_to(input_root)
    except ValueError:
        rel = pathlib.Path(sidecar_dir.name)
    return output_root / rel


def output_complete(output_dir, stem, bin_ms):
    required = [
        output_dir / f"{stem}.mv-features-v1.frames.csv.gz",
        output_dir / f"{stem}.mv-features-v1.metadata.json",
    ]
    required.extend(output_dir / f"{stem}.mv-features-v1.bin-{b}ms.csv.gz" for b in bin_ms)
    return all(path.exists() for path in required)


def path_size(path):
    return pathlib.Path(path).stat().st_size if path and pathlib.Path(path).exists() else 0


def summarize_outputs(result):
    paths = [result["frame_path"], *result["bin_paths"], result["metadata_path"]]
    return sum(path_size(path) for path in paths)


def write_csv(path, rows, fieldnames):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def run_batch(args):
    input_root = pathlib.Path(args.input_root)
    output_root = pathlib.Path(args.output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    sidecars, missing = discover_sidecars(input_root)
    rows = []
    started = time.monotonic()
    for sidecar in sidecars:
        out_dir = relative_output_dir(input_root, output_root, sidecar.sidecar_dir)
        source_bytes = path_size(sidecar.frames) + path_size(sidecar.vectors) + path_size(sidecar.metadata)
        row = {
            "stem": sidecar.stem,
            "sidecar_dir": str(sidecar.sidecar_dir),
            "frames": str(sidecar.frames),
            "vectors": str(sidecar.vectors),
            "metadata": str(sidecar.metadata) if sidecar.metadata else "",
            "output_dir": str(out_dir),
            "source_bytes": source_bytes,
            "derived_bytes": 0,
            "derived_to_source_ratio": "",
            "frame_rows": "",
            "elapsed_seconds": 0.0,
            "status": "pending",
        }
        if output_complete(out_dir, sidecar.stem, args.bin_ms) and not args.force:
            row["status"] = "skipped_existing"
            row["derived_bytes"] = sum(
                path_size(path)
                for path in [
                    out_dir / f"{sidecar.stem}.mv-features-v1.frames.csv.gz",
                    out_dir / f"{sidecar.stem}.mv-features-v1.metadata.json",
                    *(out_dir / f"{sidecar.stem}.mv-features-v1.bin-{b}ms.csv.gz" for b in args.bin_ms),
                ]
            )
            row["derived_to_source_ratio"] = (
                row["derived_bytes"] / source_bytes if source_bytes else ""
            )
            rows.append(row)
            continue
        item_started = time.monotonic()
        try:
            opts = default_options(
                frames=str(sidecar.frames),
                vectors=str(sidecar.vectors),
                metadata=str(sidecar.metadata) if sidecar.metadata else "",
                output_dir=str(out_dir),
                bin_ms=args.bin_ms,
                vector_source=args.vector_source,
                active_vector_threshold=args.active_vector_threshold,
                min_active_blocks_per_frame=args.min_active_blocks_per_frame,
                min_active_run_frames=args.min_active_run_frames,
                grid_rows=args.grid_rows,
                grid_cols=args.grid_cols,
                capped_active_vectors=args.capped_active_vectors,
            )
            result = integrate_sidecar(opts)
            row["status"] = "wrote"
            row["derived_bytes"] = summarize_outputs(result)
            row["derived_to_source_ratio"] = (
                row["derived_bytes"] / source_bytes if source_bytes else ""
            )
            row["frame_rows"] = result["frame_rows"]
        except Exception as exc:  # noqa: BLE001
            row["status"] = "error"
            row["error"] = f"{type(exc).__name__}: {exc}"
            if not args.keep_going:
                rows.append(row)
                raise
        finally:
            row["elapsed_seconds"] = round(time.monotonic() - item_started, 6)
        rows.append(row)

    fieldnames = [
        "stem",
        "sidecar_dir",
        "frames",
        "vectors",
        "metadata",
        "output_dir",
        "source_bytes",
        "derived_bytes",
        "derived_to_source_ratio",
        "frame_rows",
        "elapsed_seconds",
        "status",
        "error",
    ]
    for row in rows:
        row.setdefault("error", "")
    write_csv(output_root / "mv_feature_batch_manifest.csv", rows, fieldnames)
    write_csv(
        output_root / "mv_feature_batch_missing.csv",
        missing,
        ["stem", "sidecar_dir", "frames", "missing", "expected"],
    )
    summary = {
        "input_root": str(input_root),
        "output_root": str(output_root),
        "discovered_sidecars": len(sidecars),
        "missing_sidecar_parts": len(missing),
        "wrote": sum(1 for row in rows if row["status"] == "wrote"),
        "skipped_existing": sum(1 for row in rows if row["status"] == "skipped_existing"),
        "errors": sum(1 for row in rows if row["status"] == "error"),
        "elapsed_seconds": round(time.monotonic() - started, 6),
        "parameters": {
            "bin_ms": args.bin_ms,
            "vector_source": args.vector_source,
            "active_vector_threshold": args.active_vector_threshold,
            "min_active_blocks_per_frame": args.min_active_blocks_per_frame,
            "min_active_run_frames": args.min_active_run_frames,
            "grid_rows": args.grid_rows,
            "grid_cols": args.grid_cols,
            "capped_active_vectors": args.capped_active_vectors,
        },
    }
    (output_root / "mv_feature_batch_summary.json").write_text(
        json.dumps(summary, indent=2),
        encoding="utf-8",
    )
    return summary


def main():
    parser = argparse.ArgumentParser(description="Batch-integrate a tree of mestimate sidecars into compact MV features.")
    parser.add_argument("--input-root", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--bin-ms", type=int, nargs="+", default=[50, 100, 250])
    parser.add_argument("--vector-source", choices=["all", "past", "future"], default="past")
    parser.add_argument("--active-vector-threshold", type=float, default=0.0)
    parser.add_argument("--min-active-blocks-per-frame", type=int, default=2)
    parser.add_argument("--min-active-run-frames", type=int, default=2)
    parser.add_argument("--grid-rows", type=int, default=4)
    parser.add_argument("--grid-cols", type=int, default=4)
    parser.add_argument("--capped-active-vectors", type=int, default=6)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--keep-going", action="store_true")
    args = parser.parse_args()
    summary = run_batch(args)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
