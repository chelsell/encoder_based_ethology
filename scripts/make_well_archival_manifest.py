#!/usr/bin/env python3
import argparse
import csv
import hashlib
import json
import pathlib
import subprocess


ROI_COLUMNS = [
    "source_video_id",
    "well_label",
    "well_index",
    "roi_x0",
    "roi_y0",
    "roi_x1",
    "roi_y1",
    "roi_width",
    "roi_height",
]

ROI_RECORD_COLUMNS = [
    "source_video_id",
    "source_path",
    "source_fingerprint",
    "well_label",
    "well_index",
    "analysis_roi_x0",
    "analysis_roi_y0",
    "analysis_roi_x1",
    "analysis_roi_y1",
    "analysis_roi_width",
    "analysis_roi_height",
]

ROI_PROVENANCE_COLUMNS = [
    "roi_record_id",
    "roi_record_version",
    "roi_solution_id",
    "valar_run_id",
    "valar_run_tag",
    "plate_format",
    "template_id",
    "template_version",
    "roi_geometry_version",
    "transform_model",
    "fit_score",
    "qc_status",
    "review_status",
    "method",
    "method_version",
    "initialization_source",
    "created_at",
]

WELL_FIELDNAMES = [
    "plate_task_id",
    "source_video_id",
    "source_path",
    "source_fingerprint",
    "source_video_width",
    "source_video_height",
    "source_pixel_format",
    "source_fps_nominal",
    "source_frame_count",
    "source_duration_s",
    "well_label",
    "well_index",
    "roi_x0",
    "roi_y0",
    "roi_x1",
    "roi_y1",
    "roi_width",
    "roi_height",
    "roi_schema",
    "roi_table_path",
    "roi_table_sha256",
    "roi_solution_table_path",
    "roi_solution_table_sha256",
    "roi_repo_path",
    "roi_repo_commit",
    "roi_repo_dirty",
    "container_image",
    "container_image_sha256",
    "pipeline_mode",
    "well_archive_path",
    "well_sidecar_dir",
    "plate_qc_dir",
    "sidecar_vector_output",
    "sidecar_vector_format",
    "frame_diff_threshold",
    *ROI_PROVENANCE_COLUMNS,
]

PLATE_FIELDNAMES = [
    "plate_task_id",
    "source_video_id",
    "source_path",
    "source_fingerprint",
    "output_dir",
    "n_wells",
    "well_manifest_path",
    "roi_schema",
    "roi_table_path",
    "roi_table_sha256",
    "roi_solution_table_path",
    "roi_solution_table_sha256",
    "roi_repo_commit",
    "roi_repo_dirty",
    "container_image",
    "container_image_sha256",
    "pipeline_mode",
]

SOURCE_COLUMNS = [
    "source_video_id",
    "source_path",
    "source_fingerprint",
    "video_width",
    "video_height",
    "pixel_format",
    "fps_nominal",
    "frame_count",
    "duration_s",
]


def sha256_file(path):
    if not path:
        return ""
    path = pathlib.Path(path)
    if not path.exists() or not path.is_file():
        return ""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def require_columns(rows, required, label):
    if not rows:
        raise SystemExit(f"{label} has no rows")
    missing = [col for col in required if col not in rows[0]]
    if missing:
        raise SystemExit(f"{label} is missing required columns: {', '.join(missing)}")


def detect_roi_schema(fieldnames):
    header = set(fieldnames or [])
    if set(ROI_COLUMNS).issubset(header):
        return "simple_roi_table"
    if set(ROI_RECORD_COLUMNS).issubset(header):
        return "independent_roi_records"
    required = sorted(set(ROI_COLUMNS) | set(ROI_RECORD_COLUMNS))
    missing = [col for col in required if col not in header]
    raise SystemExit(
        "ROI table is neither the simple crop schema nor the independent ROI-record schema; "
        f"missing columns include: {', '.join(missing[:12])}"
    )


def normalize_roi_row(row, roi_schema):
    if roi_schema == "independent_roi_records":
        out = dict(row)
        out.update(
            {
                "roi_x0": row["analysis_roi_x0"],
                "roi_y0": row["analysis_roi_y0"],
                "roi_x1": row["analysis_roi_x1"],
                "roi_y1": row["analysis_roi_y1"],
                "roi_width": row["analysis_roi_width"],
                "roi_height": row["analysis_roi_height"],
            }
        )
        return out
    return row


def normalize_roi_rows(rows):
    if not rows:
        raise SystemExit("ROI table has no rows")
    roi_schema = detect_roi_schema(rows[0])
    if roi_schema == "independent_roi_records":
        normalized = []
        for row in rows:
            normalized.append(normalize_roi_row(row, roi_schema))
        return normalized, "independent_roi_records"
    return rows, roi_schema


def git_provenance(repo):
    if not repo:
        return {"path": "", "commit": "", "dirty": ""}
    repo = pathlib.Path(repo).resolve()
    try:
        commit = subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        status = subprocess.check_output(
            ["git", "-C", str(repo), "status", "--porcelain"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return {"path": str(repo), "commit": "", "dirty": "unknown"}
    return {"path": str(repo), "commit": commit, "dirty": "1" if status.strip() else "0"}


def source_by_id(source_rows):
    out = {}
    for row in source_rows:
        source_id = row["source_video_id"]
        if source_id in out:
            raise SystemExit(f"duplicate source_video_id in source catalog: {source_id}")
        out[source_id] = row
    return out


def source_value(source, roi, key):
    return source.get(key, "") or roi.get(key, "")


def source_context_for(source, roi):
    return {
        "source_path": source_value(source, roi, "source_path"),
        "source_fingerprint": source_value(source, roi, "source_fingerprint"),
        "source_video_width": source_value(source, roi, "video_width"),
        "source_video_height": source_value(source, roi, "video_height"),
        "source_pixel_format": source_value(source, roi, "pixel_format"),
        "source_fps_nominal": source_value(source, roi, "fps_nominal"),
        "source_frame_count": source_value(source, roi, "frame_count"),
        "source_duration_s": source_value(source, roi, "duration_s"),
    }


def build_well_row(roi, source_context, args, roi_sha, solution_sha, image_sha, roi_git, roi_schema, task_id):
    output_root = pathlib.Path(args.output_root)
    roi_table_path = str(pathlib.Path(args.roi_table).resolve())
    solution_table_path = str(pathlib.Path(args.roi_solution_table).resolve()) if args.roi_solution_table else ""
    image_path = str(pathlib.Path(args.image).resolve()) if args.image else ""
    source_id = roi["source_video_id"]
    plate_dir = output_root / source_id
    well_label = roi["well_label"]
    well_archive_path = plate_dir / "video" / f"{source_id}_{well_label}.av1.mkv"
    well_sidecar_dir = plate_dir / "sidecar" / "mv_v1" / well_label
    row = {
        "plate_task_id": task_id,
        "source_video_id": source_id,
        **source_context,
        "well_label": well_label,
        "well_index": roi.get("well_index", ""),
        "roi_x0": roi["roi_x0"],
        "roi_y0": roi["roi_y0"],
        "roi_x1": roi["roi_x1"],
        "roi_y1": roi["roi_y1"],
        "roi_width": roi.get("roi_width", ""),
        "roi_height": roi.get("roi_height", ""),
        "roi_schema": roi_schema,
        "roi_table_path": roi_table_path,
        "roi_table_sha256": roi_sha,
        "roi_solution_table_path": solution_table_path,
        "roi_solution_table_sha256": solution_sha,
        "roi_repo_path": roi_git["path"],
        "roi_repo_commit": roi_git["commit"],
        "roi_repo_dirty": roi_git["dirty"],
        "container_image": image_path,
        "container_image_sha256": image_sha,
        "pipeline_mode": "single_plate_decode_to_well_outputs",
        "well_archive_path": str(well_archive_path),
        "well_sidecar_dir": str(well_sidecar_dir),
        "plate_qc_dir": str(plate_dir / "sidecar" / "plate_qc_v1"),
        "sidecar_vector_output": args.sidecar_vector_output,
        "sidecar_vector_format": args.sidecar_vector_format,
        "frame_diff_threshold": args.frame_diff_threshold,
    }
    for col in ROI_PROVENANCE_COLUMNS:
        row[col] = roi.get(col, "")
    return row


def make_rows(source_rows, roi_rows, args, roi_sha, solution_sha, image_sha, roi_git, roi_schema):
    sources = source_by_id(source_rows)
    plate_ids = {}
    well_rows = []
    plate_rows = []
    source_context = {}
    skipped_missing_source_path = 0
    output_root = pathlib.Path(args.output_root)
    roi_table_path = str(pathlib.Path(args.roi_table).resolve())
    solution_table_path = str(pathlib.Path(args.roi_solution_table).resolve()) if args.roi_solution_table else ""
    image_path = str(pathlib.Path(args.image).resolve()) if args.image else ""

    for roi in sorted(roi_rows, key=lambda r: (r["source_video_id"], int(r.get("well_index") or 0), r["well_label"])):
        source_id = roi["source_video_id"]
        if source_id not in sources:
            raise SystemExit(f"ROI table references source_video_id absent from source catalog: {source_id}")
        source = sources[source_id]
        source_path = source_value(source, roi, "source_path")
        if args.skip_missing_source_path and not source_path:
            skipped_missing_source_path += 1
            continue
        if source_id not in plate_ids:
            plate_ids[source_id] = len(plate_ids) + 1
        plate_dir = output_root / source_id
        well_label = roi["well_label"]
        well_archive_path = plate_dir / "video" / f"{source_id}_{well_label}.av1.mkv"
        well_sidecar_dir = plate_dir / "sidecar" / "mv_v1" / well_label
        source_context[source_id] = source_context_for(source, roi)
        row = build_well_row(
            roi, source_context[source_id], args, roi_sha, solution_sha, image_sha, roi_git, roi_schema, plate_ids[source_id]
        )
        well_rows.append(row)

    for source_id, task_id in sorted(plate_ids.items(), key=lambda item: item[1]):
        source = source_context[source_id]
        plate_dir = output_root / source_id
        wells = [r for r in well_rows if r["source_video_id"] == source_id]
        plate_rows.append(
            {
                "plate_task_id": task_id,
                "source_video_id": source_id,
                "source_path": source.get("source_path", ""),
                "source_fingerprint": source.get("source_fingerprint", ""),
                "output_dir": str(plate_dir),
                "n_wells": len(wells),
                "well_manifest_path": str(pathlib.Path(args.well_manifest).resolve()),
                "roi_schema": roi_schema,
                "roi_table_path": roi_table_path,
                "roi_table_sha256": roi_sha,
                "roi_solution_table_path": solution_table_path,
                "roi_solution_table_sha256": solution_sha,
                "roi_repo_commit": roi_git["commit"],
                "roi_repo_dirty": roi_git["dirty"],
                "container_image": image_path,
                "container_image_sha256": image_sha,
                "pipeline_mode": "single_plate_decode_to_well_outputs",
            }
        )
    return plate_rows, well_rows, skipped_missing_source_path


def stream_manifests(source_rows, args, roi_sha, solution_sha, image_sha, roi_git, roi_schema, plate_manifest):
    sources = source_by_id(source_rows)
    plate_ids = {}
    plate_rows = {}
    skipped_missing_source_path = 0
    well_rows = 0
    output_root = pathlib.Path(args.output_root)
    roi_table_path = str(pathlib.Path(args.roi_table).resolve())
    solution_table_path = str(pathlib.Path(args.roi_solution_table).resolve()) if args.roi_solution_table else ""
    image_path = str(pathlib.Path(args.image).resolve()) if args.image else ""

    well_manifest = pathlib.Path(args.well_manifest)
    well_manifest.parent.mkdir(parents=True, exist_ok=True)
    with open(args.roi_table, newline="", encoding="utf-8") as roi_f, well_manifest.open(
        "w", newline="", encoding="utf-8"
    ) as well_f:
        reader = csv.DictReader(roi_f)
        writer = csv.DictWriter(well_f, fieldnames=WELL_FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        for raw_roi in reader:
            roi = normalize_roi_row(raw_roi, roi_schema)
            source_id = roi["source_video_id"]
            if source_id not in sources:
                raise SystemExit(f"ROI table references source_video_id absent from source catalog: {source_id}")
            source_context = source_context_for(sources[source_id], roi)
            if args.skip_missing_source_path and not source_context["source_path"]:
                skipped_missing_source_path += 1
                continue
            if source_id not in plate_ids:
                plate_ids[source_id] = len(plate_ids) + 1
                plate_dir = output_root / source_id
                plate_rows[source_id] = {
                    "plate_task_id": plate_ids[source_id],
                    "source_video_id": source_id,
                    "source_path": source_context["source_path"],
                    "source_fingerprint": source_context["source_fingerprint"],
                    "output_dir": str(plate_dir),
                    "n_wells": 0,
                    "well_manifest_path": str(well_manifest.resolve()),
                    "roi_schema": roi_schema,
                    "roi_table_path": roi_table_path,
                    "roi_table_sha256": roi_sha,
                    "roi_solution_table_path": solution_table_path,
                    "roi_solution_table_sha256": solution_sha,
                    "roi_repo_commit": roi_git["commit"],
                    "roi_repo_dirty": roi_git["dirty"],
                    "container_image": image_path,
                    "container_image_sha256": image_sha,
                    "pipeline_mode": "single_plate_decode_to_well_outputs",
                }
            row = build_well_row(
                roi,
                source_context,
                args,
                roi_sha,
                solution_sha,
                image_sha,
                roi_git,
                roi_schema,
                plate_ids[source_id],
            )
            writer.writerow(row)
            plate_rows[source_id]["n_wells"] += 1
            well_rows += 1

    plate_manifest = pathlib.Path(plate_manifest)
    plate_manifest.parent.mkdir(parents=True, exist_ok=True)
    with plate_manifest.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=PLATE_FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        for row in sorted(plate_rows.values(), key=lambda r: int(r["plate_task_id"])):
            writer.writerow(row)
    return len(plate_rows), well_rows, skipped_missing_source_path


def write_csv(path, rows):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0].keys()) if rows else []
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(
        description="Build reproducible plate/well archival manifests from a source catalog and ROI table."
    )
    parser.add_argument("--source-catalog", required=True)
    parser.add_argument("--roi-table", required=True)
    parser.add_argument(
        "--roi-solution-table",
        default="",
        help="Optional per-video ROI solution table to record as provenance when --roi-table is an ROI records export.",
    )
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--well-manifest", required=True)
    parser.add_argument("--plate-manifest", default="")
    parser.add_argument("--roi-repo", default="")
    parser.add_argument("--fail-dirty-roi-repo", action="store_true")
    parser.add_argument("--image", default="", help="Apptainer SIF/sandbox path to record in provenance.")
    parser.add_argument("--sidecar-vector-output", choices=["none", "sampled", "all"], default="none")
    parser.add_argument("--sidecar-vector-format", choices=["csv", "bin"], default="bin")
    parser.add_argument("--frame-diff-threshold", type=int, default=10)
    parser.add_argument(
        "--skip-missing-source-path",
        action="store_true",
        help="Omit wells whose source path is absent from both source catalog and ROI records.",
    )
    args = parser.parse_args()

    source_rows = read_csv(args.source_catalog)
    require_columns(source_rows, SOURCE_COLUMNS, "source catalog")
    with open(args.roi_table, newline="", encoding="utf-8") as f:
        roi_schema = detect_roi_schema(csv.DictReader(f).fieldnames)

    roi_git = git_provenance(args.roi_repo)
    if args.fail_dirty_roi_repo and roi_git["dirty"] == "1":
        raise SystemExit(f"ROI repository has uncommitted changes: {roi_git['path']}")

    roi_sha = sha256_file(args.roi_table)
    solution_sha = sha256_file(args.roi_solution_table)
    image_sha = sha256_file(args.image)
    plate_manifest = args.plate_manifest or str(pathlib.Path(args.well_manifest).with_name("plate_archival_jobs.csv"))

    plate_row_count, well_row_count, skipped_missing_source_path = stream_manifests(
        source_rows,
        args,
        roi_sha,
        solution_sha,
        image_sha,
        roi_git,
        roi_schema,
        plate_manifest,
    )
    summary = {
        "source_catalog": str(pathlib.Path(args.source_catalog).resolve()),
        "roi_table": str(pathlib.Path(args.roi_table).resolve()),
        "roi_table_sha256": roi_sha,
        "roi_schema": roi_schema,
        "roi_solution_table": str(pathlib.Path(args.roi_solution_table).resolve()) if args.roi_solution_table else "",
        "roi_solution_table_sha256": solution_sha,
        "roi_repo": roi_git,
        "container_image": str(pathlib.Path(args.image).resolve()) if args.image else "",
        "container_image_sha256": image_sha,
        "plate_manifest": str(pathlib.Path(plate_manifest).resolve()),
        "well_manifest": str(pathlib.Path(args.well_manifest).resolve()),
        "plate_rows": plate_row_count,
        "well_rows": well_row_count,
        "skipped_missing_source_path_wells": skipped_missing_source_path,
        "pipeline_mode": "single_plate_decode_to_well_outputs",
    }
    summary_path = pathlib.Path(args.well_manifest).with_suffix(".summary.json")
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
