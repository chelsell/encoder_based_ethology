import pathlib
import sys
from types import SimpleNamespace

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

from manage_archival_sge_queue import (  # noqa: E402
    cmd_submit,
    prepare_sge_log_dir,
    qsub_command,
    remote_staged_source_path,
    rsync_collect_command,
    rsync_move_file_command,
    rsync_stage_command,
    rsync_stage_push_command,
    staged_source_path,
)


def test_staged_source_path_uses_source_id_and_basename():
    row = {"source_video_id": "runA", "source_path": "/archive/a/b/source.mkv"}

    observed = staged_source_path(row, "/scratch/stage")

    assert observed == pathlib.Path("/scratch/stage/runA/source.mkv")


def test_rsync_stage_does_not_remove_archive_source():
    cmd = rsync_stage_command("/archive/source.mkv", "/scratch/stage/runA/source.mkv")

    assert cmd == [
        "rsync",
        "-a",
        "--partial",
        "--ignore-existing",
        "/archive/source.mkv",
        "/scratch/stage/runA/source.mkv",
    ]
    assert "--remove-source-files" not in cmd


def test_remote_staged_source_path_uses_cluster_visible_layout():
    row = {"source_video_id": "runA", "source_path": "/shire/store/a/b/source.mkv"}

    observed = remote_staged_source_path(row, "/wynton/scratch/me/staged_hevc/")

    assert observed == "/wynton/scratch/me/staged_hevc/runA/source.mkv"


def test_rsync_stage_push_uses_remote_mkdir_and_keeps_source():
    cmd = rsync_stage_push_command(
        "/shire/store/source.mkv",
        "me@dt2.wynton.ucsf.edu",
        "/wynton/scratch/me/staged_hevc/runA/source.mkv",
        "ssh -o ControlMaster=auto",
    )

    assert cmd == [
        "rsync",
        "-a",
        "--partial",
        "--ignore-existing",
        "-e",
        "ssh -o ControlMaster=auto",
        "--rsync-path",
        "mkdir -p /wynton/scratch/me/staged_hevc/runA && rsync",
        "/shire/store/source.mkv",
        "me@dt2.wynton.ucsf.edu:/wynton/scratch/me/staged_hevc/runA/source.mkv",
    ]
    assert "--remove-source-files" not in cmd


def test_rsync_collect_removes_cluster_source_files_after_transfer():
    cmd = rsync_collect_command("/scratch/out/runA", "/archive/out/runA")

    assert cmd == [
        "rsync",
        "-a",
        "--partial",
        "--remove-source-files",
        "/scratch/out/runA/",
        "/archive/out/runA/",
    ]


def test_rsync_move_file_uses_remove_source_files_for_staged_input_retirement():
    cmd = rsync_move_file_command("/scratch/stage/runA/source.mkv", "/scratch/retired/runA")

    assert cmd == [
        "rsync",
        "-a",
        "--partial",
        "--remove-source-files",
        "/scratch/stage/runA/source.mkv",
        "/scratch/retired/runA/",
    ]


def test_qsub_command_sets_sge_environment():
    args = SimpleNamespace(
        repo_dir="/repo",
        plate_manifest="/repo/manifests/plates.csv",
        well_manifest="/repo/manifests/wells.csv",
        staged_input_root="/scratch/stage",
        image="/images/pipeline.sif",
        apptainer_extra_bind="/scratch",
        run_sidecar=True,
        encoder="libaom-av1",
        crf=35,
        preset=8,
        encoder_threads=1,
        progress_interval_seconds=30.0,
        validation_mode="packet-count-sentinel",
        validation_sentinel_count=5,
        max_source_duration_seconds=3600.0,
        sge_script="scripts/archival_plate_array.sge",
        chunk_size=5,
        max_concurrent=3,
    )

    cmd = qsub_command(args, 12)

    assert cmd[:6] == ["qsub", "-t", "1-3", "-tc", "3", "-v"]
    assert "PLATE_MANIFEST=/repo/manifests/plates.csv" in cmd[6]
    assert "WELL_MANIFEST=/repo/manifests/wells.csv" in cmd[6]
    assert "STAGED_INPUT_ROOT=/scratch/stage" in cmd[6]
    assert "APPTAINER_EXTRA_BIND=/scratch" in cmd[6]
    assert "RUN_SIDECAR=1" in cmd[6]
    assert "CHUNK_SIZE=5" in cmd[6]
    assert "VALIDATION_MODE=packet-count-sentinel" in cmd[6]
    assert "VALIDATION_SENTINEL_COUNT=5" in cmd[6]
    assert "MAX_SOURCE_DURATION_SECONDS=3600.0" in cmd[6]
    assert "ENCODER_THREADS=1" in cmd[6]
    assert "PROGRESS_INTERVAL_SECONDS=30.0" in cmd[6]
    assert cmd[-1] == "scripts/archival_plate_array.sge"


def test_prepare_sge_log_dir_precedes_qsub_and_rejects_file(tmp_path):
    observed = prepare_sge_log_dir(tmp_path)

    assert observed == tmp_path / "sge_logs"
    assert observed.is_dir()

    other_repo = tmp_path / "other"
    other_repo.mkdir()
    (other_repo / "sge_logs").write_text("scheduler output", encoding="utf-8")
    try:
        prepare_sge_log_dir(other_repo)
    except RuntimeError as error:
        assert "not a directory" in str(error)
    else:
        raise AssertionError("expected a non-directory SGE log path to be rejected")


def test_submit_dry_run_can_use_plate_count_without_reading_manifest(capsys):
    args = SimpleNamespace(
        repo_dir="/repo",
        plate_manifest="/wynton/scratch/me/manifests/plates.csv",
        well_manifest="/wynton/scratch/me/manifests/wells.csv",
        staged_input_root="/wynton/scratch/me/staged",
        image="/wynton/scratch/me/image.sif",
        apptainer_extra_bind="/wynton/scratch",
        run_sidecar=False,
        encoder="libaom-av1",
        crf=35,
        preset=8,
        encoder_threads=1,
        progress_interval_seconds=30.0,
        validation_mode="packet-count-sentinel",
        validation_sentinel_count=5,
        max_source_duration_seconds=3600.0,
        sge_script="scripts/archival_plate_array.sge",
        chunk_size=5,
        max_concurrent=3,
        plate_count=12,
        dry_run=True,
    )

    cmd_submit(args)

    out = capsys.readouterr().out
    assert "qsub -t 1-3 -tc 3" in out
    assert "PLATE_MANIFEST=/wynton/scratch/me/manifests/plates.csv" in out
