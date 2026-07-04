import csv
import hashlib
import json
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

from make_well_archival_manifest import git_provenance, main as manifest_main  # noqa: E402


def write_csv(path, fieldnames, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def read_csv(path):
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_make_well_archival_manifest_records_roi_and_container_provenance(tmp_path, monkeypatch):
    source_catalog = tmp_path / "sources.csv"
    roi_table = tmp_path / "rois.csv"
    image = tmp_path / "pipeline.sif"
    image.write_text("container bytes", encoding="utf-8")
    write_csv(
        source_catalog,
        [
            "source_video_id",
            "source_path",
            "source_fingerprint",
            "video_width",
            "video_height",
            "pixel_format",
            "fps_nominal",
            "frame_count",
            "duration_s",
        ],
        [
            {
                "source_video_id": "runA",
                "source_path": "/data/runA.mkv",
                "source_fingerprint": "source-sha",
                "video_width": "1600",
                "video_height": "1068",
                "pixel_format": "hevc",
                "fps_nominal": "100.0",
                "frame_count": "1000",
                "duration_s": "10",
            }
        ],
    )
    roi_text = (
        "source_video_id,well_label,well_index,roi_x0,roi_y0,roi_x1,roi_y1,roi_width,roi_height\n"
        "runA,A01,0,10,20,30,40,20,20\n"
        "runA,A02,1,31,20,51,40,20,20\n"
    )
    roi_table.write_text(roi_text, encoding="utf-8")
    well_manifest = tmp_path / "well_manifest.csv"

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "make_well_archival_manifest.py",
            "--source-catalog",
            str(source_catalog),
            "--roi-table",
            str(roi_table),
            "--output-root",
            str(tmp_path / "runs"),
            "--well-manifest",
            str(well_manifest),
            "--image",
            str(image),
            "--sidecar-vector-output",
            "none",
        ],
    )

    manifest_main()

    wells = read_csv(well_manifest)
    plates = read_csv(tmp_path / "plate_archival_jobs.csv")
    assert len(wells) == 2
    assert len(plates) == 1
    assert wells[0]["plate_task_id"] == "1"
    assert wells[0]["source_video_id"] == "runA"
    assert wells[0]["well_label"] == "A01"
    assert wells[0]["roi_table_sha256"] == sha256_text(roi_text)
    assert wells[0]["container_image_sha256"] == sha256_text("container bytes")
    assert wells[0]["pipeline_mode"] == "single_plate_decode_to_well_outputs"
    assert wells[0]["sidecar_vector_output"] == "none"
    assert wells[0]["well_archive_path"].endswith("runs/runA/video/runA_A01.av1.mkv")
    assert plates[0]["n_wells"] == "2"
    assert plates[0]["pipeline_mode"] == "single_plate_decode_to_well_outputs"


def test_make_well_archival_manifest_accepts_independent_roi_records(tmp_path, monkeypatch):
    source_catalog = tmp_path / "sources.csv"
    roi_table = tmp_path / "roi_records.csv"
    solution_table = tmp_path / "roi_solutions.csv"
    solution_text = "roi_solution_id,source_video_id\nsolA,runA\n"
    solution_table.write_text(solution_text, encoding="utf-8")
    write_csv(
        source_catalog,
        [
            "source_video_id",
            "source_path",
            "source_fingerprint",
            "video_width",
            "video_height",
            "pixel_format",
            "fps_nominal",
            "frame_count",
            "duration_s",
        ],
        [
            {
                "source_video_id": "runA",
                "source_path": "",
                "source_fingerprint": "",
                "video_width": "1600",
                "video_height": "1068",
                "pixel_format": "gray",
                "fps_nominal": "100.0",
                "frame_count": "1000",
                "duration_s": "10",
            }
        ],
    )
    write_csv(
        roi_table,
        [
            "roi_record_id",
            "roi_record_version",
            "roi_solution_id",
            "source_video_id",
            "source_fingerprint",
            "source_path",
            "valar_run_id",
            "valar_run_tag",
            "plate_format",
            "template_id",
            "template_version",
            "roi_geometry_version",
            "transform_model",
            "transform_parameters_json",
            "well_label",
            "well_index",
            "row_index",
            "column_index",
            "analysis_roi_x0",
            "analysis_roi_y0",
            "analysis_roi_x1",
            "analysis_roi_y1",
            "analysis_roi_width",
            "analysis_roi_height",
            "fit_score",
            "fit_score_components_json",
            "qc_status",
            "review_status",
            "method",
            "method_version",
            "initialization_source",
            "created_at",
        ],
        [
            {
                "roi_record_id": "recA01",
                "roi_record_version": "1",
                "roi_solution_id": "solA",
                "source_video_id": "runA",
                "source_fingerprint": "roi-source-sha",
                "source_path": "/archive/runA.mkv",
                "valar_run_id": "123",
                "valar_run_tag": "tagA",
                "plate_format": "96",
                "template_id": "templateA",
                "template_version": "v1",
                "roi_geometry_version": "analysis100",
                "transform_model": "affine",
                "transform_parameters_json": "{}",
                "well_label": "A01",
                "well_index": "0",
                "row_index": "0",
                "column_index": "0",
                "analysis_roi_x0": "11",
                "analysis_roi_y0": "22",
                "analysis_roi_x1": "33",
                "analysis_roi_y1": "44",
                "analysis_roi_width": "22",
                "analysis_roi_height": "22",
                "fit_score": "0.98",
                "fit_score_components_json": "{}",
                "qc_status": "passed",
                "review_status": "accepted",
                "method": "roi_discovery",
                "method_version": "0.1",
                "initialization_source": "catalog",
                "created_at": "2026-07-03T00:00:00",
            }
        ],
    )
    well_manifest = tmp_path / "well_manifest.csv"

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "make_well_archival_manifest.py",
            "--source-catalog",
            str(source_catalog),
            "--roi-table",
            str(roi_table),
            "--roi-solution-table",
            str(solution_table),
            "--output-root",
            str(tmp_path / "runs"),
            "--well-manifest",
            str(well_manifest),
        ],
    )

    manifest_main()

    wells = read_csv(well_manifest)
    summary = json.loads(well_manifest.with_suffix(".summary.json").read_text(encoding="utf-8"))
    assert len(wells) == 1
    assert wells[0]["roi_schema"] == "independent_roi_records"
    assert wells[0]["source_path"] == "/archive/runA.mkv"
    assert wells[0]["source_fingerprint"] == "roi-source-sha"
    assert wells[0]["roi_x0"] == "11"
    assert wells[0]["roi_width"] == "22"
    assert wells[0]["roi_solution_id"] == "solA"
    assert wells[0]["roi_geometry_version"] == "analysis100"
    assert wells[0]["roi_solution_table_sha256"] == sha256_text(solution_text)
    assert summary["roi_schema"] == "independent_roi_records"
    assert summary["skipped_missing_source_path_wells"] == 0


def test_make_well_archival_manifest_can_skip_missing_source_paths(tmp_path, monkeypatch):
    source_catalog = tmp_path / "sources.csv"
    roi_table = tmp_path / "rois.csv"
    write_csv(
        source_catalog,
        [
            "source_video_id",
            "source_path",
            "source_fingerprint",
            "video_width",
            "video_height",
            "pixel_format",
            "fps_nominal",
            "frame_count",
            "duration_s",
        ],
        [
            {
                "source_video_id": "runA",
                "source_path": "",
                "source_fingerprint": "source-sha",
                "video_width": "1600",
                "video_height": "1068",
                "pixel_format": "hevc",
                "fps_nominal": "100.0",
                "frame_count": "1000",
                "duration_s": "10",
            }
        ],
    )
    roi_table.write_text(
        "source_video_id,well_label,well_index,roi_x0,roi_y0,roi_x1,roi_y1,roi_width,roi_height\n"
        "runA,A01,0,10,20,30,40,20,20\n",
        encoding="utf-8",
    )
    well_manifest = tmp_path / "well_manifest.csv"

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "make_well_archival_manifest.py",
            "--source-catalog",
            str(source_catalog),
            "--roi-table",
            str(roi_table),
            "--output-root",
            str(tmp_path / "runs"),
            "--well-manifest",
            str(well_manifest),
            "--skip-missing-source-path",
        ],
    )

    manifest_main()

    summary = json.loads(well_manifest.with_suffix(".summary.json").read_text(encoding="utf-8"))
    assert summary["plate_rows"] == 0
    assert summary["well_rows"] == 0
    assert summary["skipped_missing_source_path_wells"] == 1


def test_git_provenance_reports_dirty_repo(tmp_path):
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=tmp_path, check=True)
    tracked = tmp_path / "tracked.txt"
    tracked.write_text("clean\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=tmp_path, check=True, stdout=subprocess.DEVNULL)
    tracked.write_text("dirty\n", encoding="utf-8")

    provenance = git_provenance(tmp_path)

    assert provenance["commit"]
    assert provenance["dirty"] == "1"
