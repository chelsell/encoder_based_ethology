import pathlib
import sys
from types import SimpleNamespace

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

from manage_archival_sge_queue import (  # noqa: E402
    qsub_command,
    rsync_collect_command,
    rsync_move_file_command,
    rsync_stage_command,
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
    assert cmd[-1] == "scripts/archival_plate_array.sge"
