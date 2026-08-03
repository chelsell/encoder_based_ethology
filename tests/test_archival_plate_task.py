import json
import pathlib
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

from run_archival_plate_task import (  # noqa: E402
    build_crop_filter,
    build_ffmpeg_command,
    cleanup_local_work,
    ffprobe_duration_seconds,
    ffprobe_packet_summary,
    map_well_rows_to_output_dir,
    resolve_source_path,
    rsync_copy_command,
    rsync_tree_command,
    run_one_plate,
    select_plate_rows_for_chunk,
    sentinel_indices,
    validate_outputs,
)


def test_sge_wrapper_passes_array_task_index_into_clean_container():
    script = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "archival_plate_array.sge"

    text = script.read_text(encoding="utf-8")

    assert '--task-index "${SGE_TASK_ID:?SGE_TASK_ID is required for the archival array}"' in text
    assert '--cleanenv --env "NSLOTS=${NSLOTS:-1}"' in text


def test_archival_container_installs_rsync_for_local_scratch_copies():
    definition = pathlib.Path(__file__).resolve().parents[1] / "mestimate_sidecar.def"

    text = definition.read_text(encoding="utf-8")

    assert "        rsync \\\n" in text


def test_archival_container_pins_current_libaom_source_and_digest():
    definition = pathlib.Path(__file__).resolve().parents[1] / "mestimate_sidecar.def"

    text = definition.read_text(encoding="utf-8")

    assert "refs/tags/v3.13.2.tar.gz" in text
    assert "551238557eccedc2ae987f9dd4660b6351c422e5895c950dfe8b63b5c4b8dfcd" in text
    assert 'test "$(pkg-config --modversion aom)" = "3.13.2"' in text
    assert "cmake --build /opt/libaom-v3.13.2/build --parallel 1" in text


def well_rows(tmp_path):
    return [
        {
            "source_video_id": "runA",
            "well_label": "A01",
            "well_index": "0",
            "roi_x0": "10",
            "roi_y0": "20",
            "roi_x1": "30",
            "roi_y1": "40",
            "roi_width": "20",
            "roi_height": "20",
            "well_archive_path": str(tmp_path / "runA" / "video" / "runA_A01.av1.mkv"),
        },
        {
            "source_video_id": "runA",
            "well_label": "A02",
            "well_index": "1",
            "roi_x0": "31",
            "roi_y0": "20",
            "roi_x1": "51",
            "roi_y1": "40",
            "roi_width": "20",
            "roi_height": "20",
            "well_archive_path": str(tmp_path / "runA" / "video" / "runA_A02.av1.mkv"),
        },
    ]


def test_build_crop_filter_splits_once_and_crops_each_well(tmp_path):
    filt = build_crop_filter(well_rows(tmp_path))

    assert filt.startswith("[0:v]format=gray,split=2[s0][s1]")
    assert "[s0]crop=20:20:10:20[v0]" in filt
    assert "[s1]crop=20:20:31:20[v1]" in filt


def test_build_ffmpeg_command_maps_all_wells_to_partial_outputs(tmp_path):
    rows = well_rows(tmp_path)
    progress = tmp_path / "shared" / "logs" / "ffmpeg_progress.log"
    cmd = build_ffmpeg_command(
        "ffmpeg",
        "/stage/runA/source.mkv",
        rows,
        "libaom-av1",
        35,
        8,
        force=True,
        encoder_threads=1,
        progress_path=progress,
        progress_interval_seconds=30,
    )

    assert cmd[:5] == ["ffmpeg", "-hide_banner", "-nostdin", "-v", "error"]
    assert "-filter_complex" in cmd
    assert cmd.count("-map") == 2
    assert "[v0]" in cmd
    assert "[v1]" in cmd
    assert str(pathlib.Path(rows[0]["well_archive_path"]).with_name("runA_A01.av1.partial.mkv")) in cmd
    assert cmd.count("-cpu-used") == 2
    assert cmd.count("-threads") == 2
    assert cmd.count("1") >= 2
    assert cmd[cmd.index("-progress") + 1] == str(progress)
    assert cmd[cmd.index("-stats_period") + 1] == "30"
    assert progress.parent.is_dir()


def test_build_ffmpeg_command_rejects_invalid_thread_and_progress_settings(tmp_path):
    rows = well_rows(tmp_path)

    with pytest.raises(ValueError, match="encoder_threads"):
        build_ffmpeg_command("ffmpeg", "source.mkv", rows, "libaom-av1", 35, 8, False, encoder_threads=0)
    with pytest.raises(ValueError, match="progress_interval"):
        build_ffmpeg_command(
            "ffmpeg", "source.mkv", rows, "libaom-av1", 35, 8, False, progress_interval_seconds=0
        )


def test_run_one_plate_publishes_encoding_manifest_before_ffmpeg(tmp_path, monkeypatch):
    source = tmp_path / "source.mkv"
    source.write_bytes(b"source")
    output_dir = tmp_path / "shared" / "runA"
    plate_manifest = tmp_path / "plates.csv"
    plate_manifest.write_text(
        "plate_task_id,source_video_id,source_path,output_dir\n"
        f"1,runA,{source},{output_dir}\n",
        encoding="utf-8",
    )
    well_manifest = tmp_path / "wells.csv"
    rows = well_rows(tmp_path / "shared")
    columns = list(rows[0])
    well_manifest.write_text(
        ",".join(columns) + "\n" + "\n".join(",".join(row[column] for column in columns) for row in rows) + "\n",
        encoding="utf-8",
    )
    args = SimpleNamespace(
        plate_manifest=str(plate_manifest),
        well_manifest=str(well_manifest),
        staged_input_root="",
        work_root="",
        ffmpeg="ffmpeg",
        ffprobe="ffprobe",
        encoder="libaom-av1",
        crf=35,
        preset=8,
        encoder_threads=1,
        progress_interval_seconds=30.0,
        validation_mode="full-decode",
        validation_sentinel_count=2,
        max_source_duration_seconds=3600.0,
        run_sidecar=False,
        sidecar_bin="mestimate-sidecar",
        force=False,
        keep_local_work=False,
        dry_run=False,
    )
    observed = {}

    def fake_run(cmd, check=False):
        manifest = json.loads((output_dir / "manifest" / "archival_plate_task.json").read_text(encoding="utf-8"))
        observed["status_during_ffmpeg"] = manifest["status"]
        observed["progress_path"] = cmd[cmd.index("-progress") + 1]
        for token in cmd:
            if token.endswith(".partial.mkv"):
                pathlib.Path(token).write_bytes(b"av1")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("run_archival_plate_task.ffprobe_duration_seconds", lambda *_args: 10.0)
    monkeypatch.setattr("run_archival_plate_task.subprocess.run", fake_run)
    monkeypatch.setattr(
        "run_archival_plate_task.validate_outputs",
        lambda _ffprobe, selected_rows, **_kwargs: [{"well_label": row["well_label"]} for row in selected_rows],
    )

    result = run_one_plate(
        args,
        {"source_video_id": "runA", "source_path": str(source), "output_dir": str(output_dir)},
        task_index=1,
        task_count=1,
    )

    assert observed["status_during_ffmpeg"] == "encoding"
    assert observed["progress_path"] == str(output_dir / "logs" / "ffmpeg_progress.log")
    assert result["sge_nslots"] == 1
    assert result["status"] == "validated"


def test_resolve_source_path_uses_staged_root_when_present():
    plate = {"source_video_id": "runA", "source_path": "/archive/source.mkv"}

    observed = resolve_source_path(plate, "/scratch/staged")

    assert observed == pathlib.Path("/scratch/staged/runA/source.mkv")


def test_select_plate_rows_for_chunk_maps_array_task_to_serial_plate_rows(tmp_path):
    manifest = tmp_path / "plates.csv"
    manifest.write_text(
        "plate_task_id,source_video_id,source_path,output_dir\n"
        "1,runA,/a.mkv,/out/runA\n"
        "2,runB,/b.mkv,/out/runB\n"
        "3,runC,/c.mkv,/out/runC\n"
        "4,runD,/d.mkv,/out/runD\n"
        "5,runE,/e.mkv,/out/runE\n",
        encoding="utf-8",
    )

    rows, plate_count, chunk_count = select_plate_rows_for_chunk(manifest, task_index=2, chunk_size=2)

    assert plate_count == 5
    assert chunk_count == 3
    assert [row["source_video_id"] for row in rows] == ["runC", "runD"]


def test_map_well_rows_to_output_dir_preserves_manifest_rows_but_redirects_paths(tmp_path):
    rows = well_rows(tmp_path)
    rows[0]["well_sidecar_dir"] = str(tmp_path / "shared" / "sidecar" / "mv_v1" / "A01")

    mapped = map_well_rows_to_output_dir(rows, tmp_path / "local" / "runA")

    assert mapped[0]["well_archive_path"] == str(tmp_path / "local" / "runA" / "video" / "runA_A01.av1.mkv")
    assert mapped[0]["well_sidecar_dir"] == str(tmp_path / "local" / "runA" / "sidecar" / "mv_v1" / "A01")
    assert rows[0]["well_archive_path"] != mapped[0]["well_archive_path"]


def test_local_work_rsync_commands_do_not_remove_inputs_or_outputs():
    assert rsync_copy_command("/shared/source.mkv", "/tmp/job/source.mkv") == [
        "rsync",
        "-a",
        "--partial",
        "--ignore-existing",
        "/shared/source.mkv",
        "/tmp/job/source.mkv",
    ]
    assert rsync_tree_command("/tmp/job/out", "/shared/out") == [
        "rsync",
        "-a",
        "--partial",
        "/tmp/job/out/",
        "/shared/out/",
    ]


def test_cleanup_local_work_removes_only_source_specific_tmp_dirs(tmp_path):
    work_root = tmp_path / "job"
    keep = work_root / "input" / "runB" / "source.mkv"
    source = work_root / "input" / "runA" / "source.mkv"
    output = work_root / "output" / "runA" / "video" / "runA_A01.av1.mkv"
    source.parent.mkdir(parents=True)
    output.parent.mkdir(parents=True)
    keep.parent.mkdir(parents=True)
    source.write_text("source", encoding="utf-8")
    output.write_text("output", encoding="utf-8")
    keep.write_text("keep", encoding="utf-8")

    removed = cleanup_local_work(work_root, "runA")

    assert str(work_root / "input" / "runA") in removed
    assert str(work_root / "output" / "runA") in removed
    assert not (work_root / "input" / "runA").exists()
    assert not (work_root / "output" / "runA").exists()
    assert keep.exists()


def test_sentinel_indices_are_deterministic_and_span_wells():
    assert sentinel_indices(96, 5) == {0, 24, 48, 71, 95}
    assert sentinel_indices(2, 5) == {0, 1}
    assert sentinel_indices(96, 0) == set()


def test_ffprobe_packet_summary_parses_av1_stream(monkeypatch):
    monkeypatch.setattr(
        "run_archival_plate_task.subprocess.check_output",
        lambda *args, **kwargs: '{"streams":[{"codec_name":"av1","width":20,"height":20,"duration":"10.5","nb_read_packets":"100"}]}',
    )

    observed = ffprobe_packet_summary("ffprobe", "/tmp/well.mkv")

    assert observed == {
        "codec_name": "av1",
        "width": 20,
        "height": 20,
        "duration_s": 10.5,
        "packet_count": 100,
    }


def test_ffprobe_duration_seconds_requires_positive_duration(monkeypatch):
    monkeypatch.setattr("run_archival_plate_task.subprocess.check_output", lambda *args, **kwargs: "3599.5\n")
    assert ffprobe_duration_seconds("ffprobe", "/tmp/source.mkv") == 3599.5

    monkeypatch.setattr("run_archival_plate_task.subprocess.check_output", lambda *args, **kwargs: "N/A\n")
    with pytest.raises(RuntimeError, match="no positive duration"):
        ffprobe_duration_seconds("ffprobe", "/tmp/source.mkv")


def test_packet_count_validation_checks_all_outputs_and_decodes_sentinels(tmp_path, monkeypatch):
    rows = well_rows(tmp_path)
    for row in rows:
        path = pathlib.Path(row["well_archive_path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"av1")
    monkeypatch.setattr(
        "run_archival_plate_task.ffprobe_packet_summary",
        lambda _ffprobe, _path: {
            "codec_name": "av1",
            "width": 20,
            "height": 20,
            "duration_s": 10.0,
            "packet_count": 100,
        },
    )
    monkeypatch.setattr("run_archival_plate_task.ffprobe_frame_count", lambda _ffprobe, _path: 100)

    observed = validate_outputs("ffprobe", rows, mode="packet-count-sentinel", sentinel_count=1)

    assert [row["frame_count"] for row in observed] == [100, 100]
    assert [row["count_basis"] for row in observed] == ["video_packets", "video_packets"]
    assert [row["sentinel_full_decode"] for row in observed] == [False, True]


def test_packet_count_validation_rejects_inconsistent_well_counts(tmp_path, monkeypatch):
    rows = well_rows(tmp_path)
    for row in rows:
        path = pathlib.Path(row["well_archive_path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"av1")
    counts = iter((100, 99))
    monkeypatch.setattr(
        "run_archival_plate_task.ffprobe_packet_summary",
        lambda _ffprobe, _path: {
            "codec_name": "av1",
            "width": 20,
            "height": 20,
            "duration_s": 10.0,
            "packet_count": next(counts),
        },
    )

    with pytest.raises(RuntimeError, match="counts differ"):
        validate_outputs("ffprobe", rows, mode="packet-count", sentinel_count=0)
