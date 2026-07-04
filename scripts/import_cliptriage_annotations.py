#!/usr/bin/env python3
import argparse
import csv
import json
import pathlib
import sqlite3
from collections import defaultdict


SCHEMA_NAME = "cliptriage-annotation-import"
SCHEMA_VERSION = "v0.1"

MANIFEST_COLUMNS = [
    "source_video_id",
    "source_path",
    "source_fingerprint",
    "run",
    "run_tag",
    "well",
    "well_label",
    "well_index",
    "row",
    "column",
    "start_frame",
    "end_frame_exclusive",
    "start_time_s",
    "end_time_s",
    "duration_s",
    "fps_nominal",
    "sampling_stratum",
    "anchor_type",
    "roi_ref",
    "roi_x0",
    "roi_y0",
    "roi_x1",
    "roi_y1",
    "roi_width",
    "roi_height",
    "battery_id",
    "battery_name",
    "experiment_id",
    "experiment_name",
    "sauron",
    "sauron_config",
    "age",
    "n_fish",
    "control_type",
    "variant_id",
    "variant_name",
    "treatments",
    "well_group",
    "submission",
    "physical_plate",
    "datetime_run",
    "sampling_config_hash",
    "random_seed",
]

ANNOTATION_COLUMNS = [
    "annotation_id",
    "presentation_id",
    "clip_id",
    "annotator_id",
    "annotation_session_id",
    "task_id",
    "presentation_order",
    "is_repeat",
    "taxonomy_version",
    "ui_version",
    "created_at",
    "primary_label_path",
    "label_depth",
    "label_0",
    "label_1",
    "label_2",
    "label_path_json",
    "decision_count",
    "decision_elapsed_ms",
    "decision_timestamps_json",
    "playback_count",
    "was_review_flagged",
    "skipped",
    "supersedes_annotation_id",
    "asset_path",
]


def connect_readonly(database):
    path = pathlib.Path(database).resolve()
    if not path.exists():
        raise FileNotFoundError(path)
    uri = f"file:{path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def require_tables(conn):
    observed = {
        row["name"]
        for row in conn.execute("select name from sqlite_master where type = 'table'")
    }
    required = {"annotations", "presentations", "clips", "annotation_sessions"}
    missing = sorted(required - observed)
    if missing:
        raise ValueError(f"annotation database is missing required tables: {', '.join(missing)}")


def parse_json(value, default):
    if value in (None, ""):
        return default
    if isinstance(value, list | dict):
        return value
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def decision_elapsed_ms(decisions):
    if not decisions:
        return ""
    values = []
    for decision in decisions:
        if isinstance(decision, dict) and "at_ms" in decision:
            try:
                values.append(float(decision["at_ms"]))
            except (TypeError, ValueError):
                pass
    if len(values) < 2:
        return ""
    return max(values) - min(values)


def as_bool_int(value):
    if value in (None, ""):
        return 0
    if isinstance(value, str):
        return int(value.strip().lower() in {"1", "true", "yes", "y"})
    return int(bool(value))


def clean_scalar(value):
    if value is None:
        return ""
    try:
        # pandas NaN is not equal to itself.
        if value != value:
            return ""
    except TypeError:
        pass
    return value


def row_get(row, key, default=""):
    if isinstance(row, sqlite3.Row):
        return row[key] if key in row.keys() else default
    return row.get(key, default)


def normalize_annotation_record(annotation, manifest=None, presentation=None, session=None, asset_path=""):
    manifest = manifest or {}
    presentation = presentation or {}
    session = session or {}
    label_path = parse_json(
        row_get(annotation, "label_path_json", row_get(annotation, "label_path", [])),
        [],
    )
    if isinstance(label_path, str):
        label_path = [part.strip() for part in label_path.split("/") if part.strip()]
    decisions = parse_json(
        row_get(annotation, "decision_timestamps_json", row_get(annotation, "decision_timestamps", [])),
        [],
    )
    primary_label_path = row_get(annotation, "primary_label_path", "")
    if not primary_label_path and label_path:
        primary_label_path = " / ".join(str(part) for part in label_path)
    out = {
        "annotation_id": clean_scalar(row_get(annotation, "annotation_id")),
        "presentation_id": clean_scalar(row_get(annotation, "presentation_id")),
        "clip_id": clean_scalar(row_get(annotation, "clip_id", manifest.get("clip_id", ""))),
        "annotator_id": clean_scalar(row_get(annotation, "annotator_id", session.get("annotator_id", ""))),
        "annotation_session_id": clean_scalar(row_get(annotation, "annotation_session_id")),
        "task_id": clean_scalar(row_get(annotation, "task_id", presentation.get("task_id", session.get("task_id", "")))),
        "presentation_order": clean_scalar(row_get(annotation, "presentation_order", presentation.get("presentation_order", ""))),
        "is_repeat": as_bool_int(row_get(annotation, "is_repeat", presentation.get("is_repeat", 0))),
        "taxonomy_version": clean_scalar(row_get(annotation, "taxonomy_version", session.get("taxonomy_version", ""))),
        "ui_version": clean_scalar(row_get(annotation, "ui_version", session.get("ui_version", ""))),
        "created_at": clean_scalar(row_get(annotation, "created_at")),
        "primary_label_path": primary_label_path,
        "label_depth": len(label_path),
        "label_0": label_path[0] if len(label_path) > 0 else "",
        "label_1": label_path[1] if len(label_path) > 1 else "",
        "label_2": label_path[2] if len(label_path) > 2 else "",
        "label_path_json": json.dumps(label_path, sort_keys=True),
        "decision_count": len(decisions),
        "decision_elapsed_ms": decision_elapsed_ms(decisions),
        "decision_timestamps_json": json.dumps(decisions, sort_keys=True),
        "playback_count": clean_scalar(row_get(annotation, "playback_count", 0)),
        "was_review_flagged": as_bool_int(row_get(annotation, "was_review_flagged", 0)),
        "skipped": as_bool_int(row_get(annotation, "skipped", 0)),
        "supersedes_annotation_id": clean_scalar(row_get(annotation, "supersedes_annotation_id", "")),
        "asset_path": clean_scalar(row_get(annotation, "asset_path", asset_path)),
    }
    for col in MANIFEST_COLUMNS:
        out[col] = clean_scalar(manifest.get(col, ""))
    return out


def load_annotation_rows(conn):
    sql = """
        select
            a.annotation_id,
            a.presentation_id,
            a.clip_id,
            a.annotator_id,
            a.annotation_session_id,
            a.taxonomy_version,
            a.label_path_json,
            a.primary_label_path,
            a.decision_timestamps_json,
            a.playback_count,
            a.was_review_flagged,
            a.skipped,
            a.supersedes_annotation_id,
            a.created_at,
            a.ui_version,
            p.task_id,
            p.presentation_order,
            p.is_repeat,
            c.manifest_json,
            c.asset_path,
            c.sampling_stratum as clip_sampling_stratum
        from annotations a
        join presentations p on p.presentation_id = a.presentation_id
        join clips c on c.clip_id = a.clip_id
        order by a.created_at, a.annotation_id
    """
    rows = []
    for row in conn.execute(sql):
        manifest = parse_json(row["manifest_json"], {})
        presentation = {
            "task_id": row["task_id"],
            "presentation_order": row["presentation_order"],
            "is_repeat": row["is_repeat"],
        }
        out = normalize_annotation_record(row, manifest, presentation, asset_path=row["asset_path"])
        if not out["sampling_stratum"]:
            out["sampling_stratum"] = row["clip_sampling_stratum"] or ""
        rows.append(out)
    return rows


def read_table(path):
    path = pathlib.Path(path)
    if path.suffix.lower() == ".parquet":
        try:
            import pandas as pd
        except ModuleNotFoundError as exc:
            raise SystemExit("Reading annotation bundles requires pandas with parquet support.") from exc
        return pd.read_parquet(path).to_dict("records")
    if path.suffix.lower() == ".csv":
        with path.open("r", newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))
    raise ValueError(f"unsupported table format: {path}")


def first_existing(*paths):
    for path in paths:
        path = pathlib.Path(path)
        if path.exists():
            return path
    return None


def load_bundle_rows(bundle_dir, table_name="annotation_events"):
    bundle = pathlib.Path(bundle_dir)
    annotations_dir = bundle / "annotations"
    manifest_path = first_existing(bundle / "manifest.parquet", bundle / "manifest.csv")
    presentations_path = first_existing(
        annotations_dir / "presentations.parquet",
        annotations_dir / "presentations.csv",
    )
    sessions_path = first_existing(
        annotations_dir / "annotation_sessions.parquet",
        annotations_dir / "annotation_sessions.csv",
    )
    table_path = first_existing(
        annotations_dir / f"{table_name}.parquet",
        annotations_dir / f"{table_name}.csv",
    )
    if manifest_path is None:
        raise FileNotFoundError(bundle / "manifest.parquet")
    if table_path is None:
        raise FileNotFoundError(annotations_dir / f"{table_name}.parquet")
    manifest_by_clip = {str(row["clip_id"]): row for row in read_table(manifest_path)}
    presentations_by_id = {}
    if presentations_path is not None:
        presentations_by_id = {
            str(row["presentation_id"]): row for row in read_table(presentations_path)
        }
    sessions_by_id = {}
    if sessions_path is not None:
        sessions_by_id = {
            str(row["annotation_session_id"]): row for row in read_table(sessions_path)
        }
    rows = []
    for annotation in read_table(table_path):
        clip_id = str(row_get(annotation, "clip_id", ""))
        presentation_id = str(row_get(annotation, "presentation_id", ""))
        session_id = str(row_get(annotation, "annotation_session_id", ""))
        manifest = manifest_by_clip.get(clip_id, {})
        presentation = presentations_by_id.get(presentation_id, {})
        session = sessions_by_id.get(session_id, {})
        asset_path = str(bundle / "clips" / clip_id[:2] / clip_id / "preview.mp4") if clip_id else ""
        rows.append(normalize_annotation_record(annotation, manifest, presentation, session, asset_path))
    rows.sort(key=lambda row: (str(row["created_at"]), str(row["annotation_id"])))
    return rows


def latest_rows(rows, scope):
    latest = {}
    for index, row in enumerate(rows):
        if scope == "presentation_annotator_task":
            key = (row["presentation_id"], row["annotator_id"], row["task_id"])
        elif scope == "presentation_annotator":
            key = (row["presentation_id"], row["annotator_id"])
        elif scope == "clip_annotator":
            key = (row["clip_id"], row["annotator_id"])
        elif scope == "presentation":
            key = (row["presentation_id"],)
        elif scope == "clip":
            key = (row["clip_id"],)
        else:
            raise ValueError(f"unknown latest scope: {scope}")
        latest[key] = (index, row)
    return [item[1] for item in sorted(latest.values(), key=lambda pair: pair[0])]


def filter_rows(rows, task_id=None, annotator_id=None, taxonomy_version=None):
    out = []
    for row in rows:
        if task_id and str(row.get("task_id", "")) != task_id:
            continue
        if annotator_id and str(row.get("annotator_id", "")) != annotator_id:
            continue
        if taxonomy_version and str(row.get("taxonomy_version", "")) != taxonomy_version:
            continue
        out.append(row)
    return out


def summarize_by_clip(current_rows):
    grouped = defaultdict(list)
    for row in current_rows:
        grouped[row["clip_id"]].append(row)
    summaries = []
    for clip_id, rows in sorted(grouped.items()):
        non_skipped = [row for row in rows if int(row["skipped"]) == 0]
        labels = sorted({row["primary_label_path"] for row in non_skipped})
        annotators = sorted({row["annotator_id"] for row in rows})
        base = rows[-1]
        summary = {
            "clip_id": clip_id,
            "n_current_annotations": len(rows),
            "n_non_skipped_annotations": len(non_skipped),
            "n_review_flagged": sum(int(row["was_review_flagged"]) for row in rows),
            "annotator_ids": ";".join(annotators),
            "primary_label_paths": ";".join(labels),
            "consensus_primary_label_path": labels[0] if len(labels) == 1 else "",
            "has_label_disagreement": int(len(labels) > 1),
            "all_skipped": int(len(non_skipped) == 0),
            "latest_created_at": max(str(row["created_at"]) for row in rows),
        }
        for col in [
            "source_video_id",
            "source_path",
            "run",
            "run_tag",
            "well",
            "well_label",
            "start_frame",
            "end_frame_exclusive",
            "start_time_s",
            "end_time_s",
            "duration_s",
            "fps_nominal",
            "sampling_stratum",
            "roi_x0",
            "roi_y0",
            "roi_x1",
            "roi_y1",
        ]:
            summary[col] = base.get(col, "")
        summaries.append(summary)
    return summaries


def write_csv(path, rows, fieldnames):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_outputs(rows, output_dir, latest_scope, source, filters=None, current_rows=None):
    if current_rows is None:
        current = latest_rows(rows, latest_scope)
    else:
        current = current_rows
    clip_summary = summarize_by_clip(current)
    out = pathlib.Path(output_dir)
    write_csv(out / "cliptriage_annotation_history.csv", rows, ANNOTATION_COLUMNS + MANIFEST_COLUMNS)
    write_csv(out / "cliptriage_current_annotations.csv", current, ANNOTATION_COLUMNS + MANIFEST_COLUMNS)
    summary_fields = [
        "clip_id",
        "n_current_annotations",
        "n_non_skipped_annotations",
        "n_review_flagged",
        "annotator_ids",
        "primary_label_paths",
        "consensus_primary_label_path",
        "has_label_disagreement",
        "all_skipped",
        "latest_created_at",
        "source_video_id",
        "source_path",
        "run",
        "run_tag",
        "well",
        "well_label",
        "start_frame",
        "end_frame_exclusive",
        "start_time_s",
        "end_time_s",
        "duration_s",
        "fps_nominal",
        "sampling_stratum",
        "roi_x0",
        "roi_y0",
        "roi_x1",
        "roi_y1",
    ]
    write_csv(out / "cliptriage_clip_label_summary.csv", clip_summary, summary_fields)
    metadata = {
        "schema_name": SCHEMA_NAME,
        "schema_version": SCHEMA_VERSION,
        "source": source,
        "latest_scope": latest_scope,
        "filters": filters or {},
        "history_rows": len(rows),
        "current_annotation_rows": len(current),
        "clip_summary_rows": len(clip_summary),
        "unit_of_observation": {
            "history": "one append-only annotation event",
            "current_annotations": f"latest annotation event per {latest_scope}",
            "clip_summary": "one clip with current annotation labels summarized across annotators",
        },
        "warning": "Human labels describe visible clip content under the annotation taxonomy; they are not prevalence estimates or validated biological outcomes.",
    }
    (out / "cliptriage_annotation_import_metadata.json").write_text(
        json.dumps(metadata, indent=2),
        encoding="utf-8",
    )
    return metadata


def import_annotations(database, output_dir, latest_scope, task_id=None, annotator_id=None, taxonomy_version=None):
    conn = connect_readonly(database)
    require_tables(conn)
    rows = load_annotation_rows(conn)
    filters = {"task_id": task_id, "annotator_id": annotator_id, "taxonomy_version": taxonomy_version}
    rows = filter_rows(rows, task_id=task_id, annotator_id=annotator_id, taxonomy_version=taxonomy_version)
    return write_outputs(
        rows,
        output_dir,
        latest_scope,
        {"kind": "sqlite", "path": str(pathlib.Path(database).resolve())},
        filters=filters,
    )


def import_bundle(bundle_dir, output_dir, latest_scope, task_id=None, annotator_id=None, taxonomy_version=None):
    rows = load_bundle_rows(bundle_dir, "annotation_events")
    filters = {"task_id": task_id, "annotator_id": annotator_id, "taxonomy_version": taxonomy_version}
    rows = filter_rows(rows, task_id=task_id, annotator_id=annotator_id, taxonomy_version=taxonomy_version)
    latest_table_path = first_existing(
        pathlib.Path(bundle_dir) / "annotations" / "latest_annotations.parquet",
        pathlib.Path(bundle_dir) / "annotations" / "latest_annotations.csv",
    )
    current_rows = None
    if latest_table_path is not None:
        current_rows = filter_rows(
            load_bundle_rows(bundle_dir, "latest_annotations"),
            task_id=task_id,
            annotator_id=annotator_id,
            taxonomy_version=taxonomy_version,
        )
    return write_outputs(
        rows,
        output_dir,
        latest_scope,
        {"kind": "bundle", "path": str(pathlib.Path(bundle_dir).resolve())},
        filters=filters,
        current_rows=current_rows,
    )


def main():
    parser = argparse.ArgumentParser(description="Import cliptriage annotations into analysis-ready CSV tables.")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--database", default=None, help="cliptriage SQLite database.")
    source.add_argument("--bundle-dir", default=None, help="Export bundle matching docs/annotation_spec.md.")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--task-id", default=None)
    parser.add_argument("--annotator-id", default=None)
    parser.add_argument("--taxonomy-version", default=None)
    parser.add_argument(
        "--latest-scope",
        choices=["presentation_annotator_task", "presentation_annotator", "clip_annotator", "presentation", "clip"],
        default="presentation_annotator_task",
        help="Scope used to collapse append-only annotation history into current labels.",
    )
    args = parser.parse_args()
    if args.bundle_dir:
        metadata = import_bundle(
            args.bundle_dir,
            args.output_dir,
            args.latest_scope,
            task_id=args.task_id,
            annotator_id=args.annotator_id,
            taxonomy_version=args.taxonomy_version,
        )
    else:
        database = args.database or "/home/cole/code/well_annotation/data/annotations/cliptriage.sqlite"
        metadata = import_annotations(
            database,
            args.output_dir,
            args.latest_scope,
            task_id=args.task_id,
            annotator_id=args.annotator_id,
            taxonomy_version=args.taxonomy_version,
        )
    print(json.dumps(metadata, indent=2))


if __name__ == "__main__":
    main()
