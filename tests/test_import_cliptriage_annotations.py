import json
import pathlib
import sqlite3
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

from import_cliptriage_annotations import (  # noqa: E402
    import_bundle,
    import_annotations,
    latest_rows,
    load_annotation_rows,
)


def make_db(path):
    con = sqlite3.connect(path)
    con.executescript(
        """
        create table clips (
            clip_id text primary key,
            manifest_json text not null,
            asset_path text not null,
            sampling_stratum text
        );
        create table presentations (
            presentation_id text primary key,
            clip_id text not null,
            task_id text not null,
            presentation_order integer not null,
            is_repeat boolean not null
        );
        create table annotation_sessions (
            annotation_session_id text primary key,
            annotator_id text not null,
            project_id text not null,
            task_id text not null,
            taxonomy_version text not null,
            ui_version text not null,
            created_at text not null,
            updated_at text not null
        );
        create table annotations (
            annotation_id text primary key,
            presentation_id text not null,
            clip_id text not null,
            annotator_id text not null,
            annotation_session_id text not null,
            taxonomy_version text not null,
            label_path_json text not null,
            primary_label_path text not null,
            decision_timestamps_json text not null,
            playback_count integer not null,
            was_review_flagged boolean not null,
            skipped boolean not null,
            supersedes_annotation_id text,
            created_at text not null,
            ui_version text not null
        );
        """
    )
    manifest = {
        "source_video_id": "sauronx_run_1",
        "source_path": "/shire/store/run.mkv",
        "run": 1,
        "run_tag": "20240101.000000.S1",
        "well": 123,
        "well_label": "A01",
        "start_frame": 100,
        "end_frame_exclusive": 200,
        "start_time_s": 1.0,
        "end_time_s": 2.0,
        "duration_s": 1.0,
        "fps_nominal": 100.0,
        "sampling_stratum": "mv_disagreement",
        "roi_x0": 10,
        "roi_y0": 20,
        "roi_x1": 30,
        "roi_y1": 40,
    }
    con.execute(
        "insert into clips values (?, ?, ?, ?)",
        ("clip-a", json.dumps(manifest), "clips/cl/clip-a/preview.mp4", "mv_disagreement"),
    )
    con.execute("insert into presentations values (?, ?, ?, ?, ?)", ("pres-a", "clip-a", "task", 1, 0))
    con.execute(
        "insert into annotation_sessions values (?, ?, ?, ?, ?, ?, ?, ?)",
        ("sess-a", "rater-a", "project", "task", "v0.1", "ui", "2026-01-01T00:00:00", "2026-01-01T00:00:00"),
    )
    con.execute(
        "insert into annotation_sessions values (?, ?, ?, ?, ?, ?, ?, ?)",
        ("sess-b", "rater-b", "project", "task", "v0.1", "ui", "2026-01-01T00:00:00", "2026-01-01T00:00:00"),
    )
    rows = [
        (
            "ann-old",
            "pres-a",
            "clip-a",
            "rater-a",
            "sess-a",
            "v0.1",
            ["multiple_fish", "quiet"],
            "multiple_fish / quiet",
            [{"at_ms": 10}, {"at_ms": 30}],
            0,
            0,
            0,
            None,
            "2026-01-01T00:00:01",
            "ui",
        ),
        (
            "ann-new",
            "pres-a",
            "clip-a",
            "rater-a",
            "sess-a",
            "v0.1",
            ["multiple_fish", "plausibly_behavioral", "stimulus_like_weak"],
            "multiple_fish / plausibly_behavioral / stimulus_like_weak",
            [{"at_ms": 10}, {"at_ms": 40}, {"at_ms": 90}],
            1,
            1,
            0,
            "ann-old",
            "2026-01-01T00:00:02",
            "ui",
        ),
        (
            "ann-b",
            "pres-a",
            "clip-a",
            "rater-b",
            "sess-b",
            "v0.1",
            ["multiple_fish", "artifact_dominant"],
            "multiple_fish / artifact_dominant",
            [],
            0,
            0,
            0,
            None,
            "2026-01-01T00:00:03",
            "ui",
        ),
    ]
    for row in rows:
        con.execute(
            "insert into annotations values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                row[0],
                row[1],
                row[2],
                row[3],
                row[4],
                row[5],
                json.dumps(row[6]),
                row[7],
                json.dumps(row[8]),
                row[9],
                row[10],
                row[11],
                row[12],
                row[13],
                row[14],
            ),
        )
    con.commit()
    con.close()


def test_latest_rows_keeps_latest_per_presentation_and_annotator(tmp_path):
    db = tmp_path / "annotations.sqlite"
    make_db(db)
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    rows = load_annotation_rows(con)

    current = latest_rows(rows, "presentation_annotator_task")

    assert [row["annotation_id"] for row in current] == ["ann-new", "ann-b"]
    assert current[0]["label_2"] == "stimulus_like_weak"
    assert current[0]["decision_elapsed_ms"] == 80.0
    assert current[0]["well_label"] == "A01"


def test_import_annotations_writes_history_current_and_clip_summary(tmp_path):
    db = tmp_path / "annotations.sqlite"
    out = tmp_path / "out"
    make_db(db)

    metadata = import_annotations(db, out, "presentation_annotator_task")

    assert metadata["history_rows"] == 3
    assert metadata["current_annotation_rows"] == 2
    assert metadata["clip_summary_rows"] == 1
    summary = (out / "cliptriage_clip_label_summary.csv").read_text(encoding="utf-8")
    assert "has_label_disagreement" in summary
    assert "multiple_fish / artifact_dominant" in summary
    assert "multiple_fish / plausibly_behavioral / stimulus_like_weak" in summary


def test_import_annotations_filters_task_and_annotator(tmp_path):
    db = tmp_path / "annotations.sqlite"
    out = tmp_path / "out"
    make_db(db)

    metadata = import_annotations(db, out, "presentation_annotator_task", annotator_id="rater-a")

    current = (out / "cliptriage_current_annotations.csv").read_text(encoding="utf-8")
    assert metadata["history_rows"] == 2
    assert metadata["current_annotation_rows"] == 1
    assert "ann-new" in current
    assert "ann-b" not in current


def test_import_bundle_reads_spec_tables(tmp_path):
    pd = __import__("pandas")
    bundle = tmp_path / "cliptriage_export_test_20260702"
    annotations = bundle / "annotations"
    annotations.mkdir(parents=True)
    pd.DataFrame(
        [
            {
                "clip_id": "clip-a",
                "source_video_id": "sauronx_run_1",
                "source_path": "/shire/store/run.mkv",
                "run": 1,
                "run_tag": "20240101.000000.S1",
                "well_label": "A01",
                "start_frame": 100,
                "end_frame_exclusive": 200,
                "sampling_stratum": "bundle_test",
                "roi_x0": 10,
                "roi_y0": 20,
                "roi_x1": 30,
                "roi_y1": 40,
            }
        ]
    ).to_parquet(bundle / "manifest.parquet")
    pd.DataFrame(
        [
            {
                "presentation_id": "pres-a",
                "clip_id": "clip-a",
                "task_id": "task",
                "presentation_order": 1,
                "is_repeat": False,
            }
        ]
    ).to_parquet(annotations / "presentations.parquet")
    pd.DataFrame(
        [
            {
                "annotation_session_id": "sess-a",
                "annotator_id": "rater-a",
                "task_id": "task",
                "taxonomy_version": "v0.2",
                "ui_version": "ui",
            }
        ]
    ).to_parquet(annotations / "annotation_sessions.parquet")
    event = {
        "annotation_id": "ann-a",
        "presentation_id": "pres-a",
        "clip_id": "clip-a",
        "annotator_id": "rater-a",
        "annotation_session_id": "sess-a",
        "taxonomy_version": "v0.2",
        "label_path_json": json.dumps(["fish_visible", "quiet_live"]),
        "primary_label_path": "fish_visible / quiet_live",
        "decision_timestamps_json": "[]",
        "playback_count": 0,
        "was_review_flagged": False,
        "skipped": False,
        "created_at": "2026-07-02T00:00:00",
        "ui_version": "ui",
    }
    pd.DataFrame([event]).to_parquet(annotations / "annotation_events.parquet")
    pd.DataFrame([event]).to_parquet(annotations / "latest_annotations.parquet")
    out = tmp_path / "out"

    metadata = import_bundle(bundle, out, "presentation_annotator_task")

    assert metadata["source"]["kind"] == "bundle"
    assert metadata["history_rows"] == 1
    current = (out / "cliptriage_current_annotations.csv").read_text(encoding="utf-8")
    assert "fish_visible / quiet_live" in current
    assert "/shire/store/run.mkv" in current
